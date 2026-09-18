"""Direct provider smoke test; does NOT certify the production interpretation path."""
import asyncio
import json
import os
from pathlib import Path
import time

import httpx
from dotenv import load_dotenv

from harness.judge import interpretation_metrics, strict_json

PROMPT = '''Interpret this synthetic operator note: "Solar output drops by 80% from 1 PM to 3 PM."
Return only a JSON object with note_index=0, applies, directive_type,
structured_adjustment containing hours and factor, and explanation.
The allowed directive is solar_reduction. Hours are start-inclusive/end-exclusive.
Hours must be JSON integers from 0 to 23 in ascending order (24-hour clock).
The factor is the fraction remaining. Do not include markdown.'''


async def probe(provider):
    prefix = "LLM_" if provider == "gemini" else "LLM_FALLBACK_"
    key=os.getenv(prefix+"API_KEY","")
    model=os.getenv(prefix+"MODEL","")
    if not key: return {"provider":provider,"status":"missing_key","passed":False}
    if not model: return {"provider":provider,"status":"missing_model","passed":False}
    started=time.perf_counter()
    result={"provider":provider,"model":model,"passed":False}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            if provider == "gemini":
                from urllib.parse import quote
                url="https://generativelanguage.googleapis.com/v1beta/models/"+quote(model,safe="")+":generateContent"
                response=await asyncio.wait_for(client.post(url,headers={"x-goog-api-key":key},json={
                    "contents":[{"parts":[{"text":PROMPT}]}],
                    "generationConfig":{"temperature":0,"responseMimeType":"application/json","maxOutputTokens":1024},
                }),timeout=22)
            else:
                base=os.getenv("LLM_FALLBACK_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
                if base != "https://openrouter.ai/api/v1":
                    return {**result,"status":"unexpected_openrouter_base_url"}
                response=await asyncio.wait_for(client.post(base+"/chat/completions",headers={"Authorization":"Bearer "+key},json={
                    "model":model,"messages":[{"role":"user","content":PROMPT}],
                    "temperature":0,"max_tokens":1024,"response_format":{"type":"json_object"},
                }),timeout=22)
        result["http_status"]=response.status_code
        if response.status_code != 200:
            result["status"]="provider_rejected_request"
            return result
        body=response.json()
        if provider == "gemini":
            content="".join(p.get("text","") for p in body["candidates"][0]["content"]["parts"] if not p.get("thought"))
            result["responding_model"]=body.get("modelVersion",model)
        else:
            content=body["choices"][0]["message"]["content"]
            result["responding_model"]=body.get("model",model)
        got=strict_json(content)
        raw_hours=got.get("structured_adjustment",{}).get("hours") if isinstance(got,dict) and isinstance(got.get("structured_adjustment"),dict) else None
        result["returned_hours"]=[h if type(h) is int and 0 <= h < 24 else "non-integer/out-of-range" for h in raw_hours] if isinstance(raw_hours,list) else "not-an-array"
        expected={"note_index":0,"applies":True,"directive_type":"solar_reduction","structured_adjustment":{"hours":[13,14],"factor":.2}}
        result["interpretation"]=interpretation_metrics([got],[expected])
        result["passed"]=all(v==1 for v in result["interpretation"].values())
        result["status"]="completed"
    except Exception as exc:
        result["status"]="error"
        result["error_type"]=type(exc).__name__
    finally:
        result["seconds"]=round(time.perf_counter()-started,3)
    return result


async def run():
    # Sequential to avoid unnecessary quota bursts.
    return [await probe("gemini"),await probe("openrouter")]


def main():
    root=Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env",override=False)
    results=asyncio.run(run())
    report={"scope":"direct connectivity/JSON smoke only; production pipeline still requires live tests","providers":results}
    path=root / "harness/reports/providers.json"
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))
    return int(not all(r["passed"] for r in results))


if __name__ == "__main__": raise SystemExit(main())
