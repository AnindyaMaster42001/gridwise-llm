"""
Prompt assets for operator-note interpretation.  OWNER: Member B.

The prompt is the single highest-leverage artifact in this repo: 25 of the 100
points are scored directly on what comes out of it, and another 25 depend on it
being right. Treat it like code — version it, test it against
`harness/paraphrase_bank.json`, never tune it against a single note.

Hard rules the prompt must encode (Problem Statement §04, §05, §08):
  * Exactly one entry per note, in note_index order.
  * directive_type from the closed set only; unrelated notes -> "no_op".
  * Windows are start-inclusive / end-exclusive: "1 PM to 3 PM" -> [13, 14].
  * hours: unique ints 0..23, ascending (a window that wraps midnight still
    sorts ascending, e.g. 10 PM-2 AM -> [0, 1, 22, 23]).
  * solar_reduction.factor is the fraction that REMAINS: "80% reduction" -> 0.2,
    "drops to 20%" -> 0.2, "about half" -> 0.5, "one-fifth" -> 0.2.
  * minimum_battery_reserve given as a percentage is a percentage of
    battery.capacity_kwh -> the prompt is given capacity so it can do the math.
  * Never invent demand, solar, tariff or battery values.
"""

from __future__ import annotations

from typing import List

from app.schemas import BatteryInput

SYSTEM_PROMPT = """TODO(Member B)"""

FEW_SHOT: List[dict] = []  # TODO(Member B): paraphrase-diverse, not copies of the public samples


def build_user_prompt(notes: List[str], battery: BatteryInput) -> str:
    """Render the per-request user message.

    Must include the note list with explicit zero-based indices and the battery
    capacity (needed to resolve "50% of capacity" style reserves).
    """
    raise NotImplementedError("TODO(Member B): see docs/team/MEMBER-B-llm-interpretation.md")


def response_json_schema() -> dict:
    """JSON schema handed to the provider for structured / constrained output.

    Shape:
      {"interpretations": [
         {"note_index": int, "directive_type": <enum>,
          "hours": [int] | null, "factor": number | null,
          "minimum_energy_kwh": number | null, "max_grid_kwh": number | null,
          "explanation": str}
      ]}

    Deliberately FLAT: one object shape for all directive types, with unused
    fields null. Flat schemas survive weaker models far better than a oneOf
    union, and app.guardrails re-assembles the exact structured_adjustment.
    """
    raise NotImplementedError("TODO(Member B)")
