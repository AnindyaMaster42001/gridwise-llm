"""Reproducers for integration risks; owned by D, fixes belong to A/B/C."""
import asyncio
import logging

import pytest

from app import pipeline, optimizer
from app.llm import interpreter
from app.schemas import ScenarioRequest
from harness.judge import ROOT, expected_constraints, load_cases

pytestmark = pytest.mark.release


@pytest.mark.asyncio
async def test_provider_exception_does_not_leak_sensitive_text_to_logs(monkeypatch,caplog):
    canary="SYNTHETIC_PRIVATE_PROVIDER_DETAIL"
    async def fail(*args): raise RuntimeError(canary)
    monkeypatch.setattr(interpreter,"interpret_notes",fail)
    req=ScenarioRequest.model_validate(load_cases(ROOT / "tests/data/public_samples.json")[0]["input"])
    with caplog.at_level(logging.ERROR):
        await pipeline._call_interpreter(req.operator_notes,req.battery,5,"audit")
    assert canary not in caplog.text, "raw provider exception was logged"


@pytest.mark.asyncio
async def test_solver_deadline_is_bounded(monkeypatch):
    import time
    case=load_cases(ROOT / "tests/data/public_samples.json")[0]
    req=ScenarioRequest.model_validate(case["input"])
    c=expected_constraints(req,case["expected_output"]["directive_interpretation"])
    def slow(*args):
        time.sleep(.7)
        return []
    monkeypatch.setattr(optimizer,"solve",slow)
    started=time.perf_counter()
    await pipeline._solve_with_ladder(req.ordered_hours(),req.battery,c.applied,started+.3,"audit")
    assert time.perf_counter()-started < .6, "solver exceeded pipeline deadline without cancellation"
