"""Validate a local/pulled image without credentials; remove only our own container.

This verifies Docker mechanics, not mandatory live-LLM behavior. Registry pull
and external reachability remain separate checks in the deployment runbook.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time
import uuid
import urllib.error
import urllib.request

from harness.preflight import scan_text
from harness.judge import ROOT, evaluate_case, load_cases, strict_json


def docker(*args, timeout=60):
    return subprocess.run(["docker",*args],capture_output=True,text=True,check=True,timeout=timeout).stdout.strip()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image",default="gridwise-llm:lane-d")
    parser.add_argument("--output",type=Path,default=Path("harness/reports/container.json"))
    args=parser.parse_args()
    name="gridwise-d-audit-"+uuid.uuid4().hex[:10]
    started=time.perf_counter()
    report={"image":args.image,"scope":"local container without real LLM credentials","passed":False}
    created=False
    try:
        report["last_check"]="start container"
        docker("run","--detach","--name",name,"--read-only","--tmpfs","/tmp:rw,noexec,nosuid,size=64m",
               "--cap-drop","ALL","--security-opt","no-new-privileges:true",
               "-e","ALLOW_NO_LLM=1","-p","127.0.0.1::8000",args.image)
        created=True
        report["last_check"]="health ready within 60 seconds"
        state=json.loads(docker("inspect",name))[0]
        port=state["NetworkSettings"]["Ports"]["8000/tcp"][0]["HostPort"]
        url=f"http://127.0.0.1:{port}/health"
        while time.perf_counter()-started < 60:
            try:
                with urllib.request.urlopen(url,timeout=2) as response:
                    if response.status == 200 and json.load(response).get("status") == "ok": break
            except (OSError,ValueError): pass
            time.sleep(.25)
        else: raise RuntimeError("health not ready within 60 seconds")
        report["startup_seconds"]=time.perf_counter()-started
        report["last_check"]="optimization route and response schema"
        sample=load_cases(ROOT / "tests/data/public_samples.json")[0]
        request=urllib.request.Request(f"http://127.0.0.1:{port}/optimize-energy",
            data=json.dumps(sample["input"]).encode(),headers={"Content-Type":"application/json"})
        with urllib.request.urlopen(request,timeout=30) as response:
            report["sample_http_status"]=response.status
            result=evaluate_case(sample,strict_json(response.read().decode()))
        report["sample_schema_errors"]=result["schema_errors"]
        report["sample_ground_truth_valid"]=result["valid"]
        assert report["sample_http_status"] == 200 and not result["schema_errors"], "container API/schema failed"
        report["last_check"]="non-root user"
        report["uid"]=docker("exec",name,"id","-u")
        assert report["uid"] == "10001", "unexpected container user"
        report["last_check"]="image metadata and artifact exclusion"
        # Do not dump image environment or history: inspect and report booleans only.
        metadata=docker("image","inspect",args.image)
        history=docker("history","--no-trunc",args.image)
        report["secret_pattern_matches"]=scan_text(metadata) or scan_text(history)
        assert not report["secret_pattern_matches"], "possible secret in image metadata"
        image=json.loads(metadata)[0]
        report["image_id"]=image["Id"]
        report["local_repo_digests"]=image.get("RepoDigests",[])
        report["read_only"]=state["HostConfig"]["ReadonlyRootfs"]
        report["artifact_exclusion"]=docker("exec",name,"python","-c",
            "from pathlib import Path; assert not any(Path('/app').rglob('.env')); assert not Path('/app/.git').exists(); assert not Path('/app/tests').exists(); print('passed')")
        # Let Docker exercise its own configured HEALTHCHECK, not just host curl.
        report["last_check"]="Docker HEALTHCHECK"
        deadline=time.perf_counter()+25
        while time.perf_counter()<deadline:
            status=docker("inspect","--format={{.State.Health.Status}}",name)
            if status == "healthy": break
            time.sleep(.5)
        report["docker_health"]=status
        assert status == "healthy", "Docker HEALTHCHECK did not pass"
        report["last_check"]="graceful shutdown"
        docker("stop","--time","10",name)
        exit_code=int(docker("inspect","--format={{.State.ExitCode}}",name))
        report["shutdown_exit_code"]=exit_code
        assert exit_code == 0, "container did not shut down gracefully"
        report["passed"]=True
        report["last_check"]="complete"
    except Exception as exc:
        # Some Docker exceptions include full command/environment text.
        report["error_type"]=type(exc).__name__
    finally:
        if created:
            try: docker("rm","--force",name)
            except Exception: report["cleanup_required_container"]=name
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))
    return int(not report["passed"])


if __name__ == "__main__": raise SystemExit(main())
