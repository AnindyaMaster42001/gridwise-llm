"""Explicit opt-in avoids spending provider quota in ordinary offline CI."""
import json
import os

import pytest

from app.schemas import BatteryInput
from app.llm.interpreter import interpret_notes
from app.guardrails import validate_interpretations
from harness.judge import ROOT, interpretation_metrics

BANK = json.loads((ROOT / "harness/paraphrase_bank.json").read_text(encoding="utf-8"))


def test_bank_covers_every_directive_with_twelve_unique_notes():
    from collections import Counter
    counts = Counter(c["expected"]["directive_type"] for c in BANK["cases"])
    assert len(counts) == 6 and min(counts.values()) >= 12
    assert len({c["note"] for c in BANK["cases"]}) == len(BANK["cases"])


@pytest.mark.skipif(os.getenv("RUN_LIVE_LLM") != "1" or not os.getenv("LLM_API_KEY"), reason="requires RUN_LIVE_LLM=1 and LLM_API_KEY")
@pytest.mark.asyncio
@pytest.mark.parametrize("case", BANK["cases"], ids=lambda c: c["note"][:65])
async def test_live_paraphrase(case):
    battery = BatteryInput.model_validate(BANK["battery_context"])
    raw = await interpret_notes([case["note"]], battery)
    result = validate_interpretations(raw, [case["note"]], battery)
    assert all(d.source in ("llm", "llm_repaired") for d in result), "fallback must not masquerade as a live model test"
    kind = case["expected"]["directive_type"]
    expected = [{"note_index":0, "directive_type":kind, "applies":kind != "no_op",
                 "structured_adjustment":{k:v for k,v in case["expected"].items() if k != "directive_type"} if kind != "no_op" else None}]
    metrics = interpretation_metrics([d.to_interpretation().model_dump() for d in result], expected)
    assert all(v == 1 for v in metrics.values()), metrics
