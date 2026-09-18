"""Real ASGI + solver, deterministic interpreter fixture; never a live LLM."""
import httpx
import pytest

from app import guardrails, optimizer, pipeline, verifier
from app.llm import interpreter
from app.main import app
from harness.judge import ROOT, evaluate_case, load_cases

CASES = load_cases(ROOT / "tests/data/public_samples.json")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
async def test_public_sample_in_process(case, monkeypatch, require_implemented):
    require_implemented(pipeline.run_pipeline, guardrails.validate_interpretations,
                        guardrails.build_constraint_set, optimizer.solve, verifier.verify)
    async def interpret(notes, battery):
        return [dict(note_index=d["note_index"], directive_type=d["directive_type"],
                     explanation="Offline ground-truth fixture", **(d["structured_adjustment"] or {}))
                for d in case["expected_output"]["directive_interpretation"]]
    monkeypatch.setattr(interpreter, "interpret_notes", interpret)
    if hasattr(pipeline, "interpret_notes"):
        monkeypatch.setattr(pipeline, "interpret_notes", interpret)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as client:
        response = await client.post("/optimize-energy", json=case["input"])
    assert response.status_code == 200, response.text
    result = evaluate_case(case, response.json())
    assert result["valid"] and result["shared_verifier"] == "passed", result
    assert all(v == 1 for v in result["interpretation"].values()), result
    assert abs(result["recalculated_cost_bdt"] - case["expected_output"]["total_cost_bdt"]) <= .01
