"""
Deterministic backup interpreter.  OWNER: Member B.

Used ONLY when every LLM provider has failed. Its job is to keep the service
answering with something plausible instead of 500-ing, not to replace the model.
Keep it clearly separated so an artifact reviewer can see the LLM is the primary
path (the Guide penalises phrase-matching as the *sole* interpreter).

Report its use honestly: set Directive.source = "fallback" and say so in the
explanation text.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.schemas import BatteryInput


def rule_based_interpret(notes: List[str], battery: BatteryInput) -> List[Dict[str, Any]]:
    """Same output shape as `interpreter.interpret_notes`, from regex heuristics.

    Minimum viable coverage:
      * time windows: "from 2 AM until 5 AM", "between 13:00 and 15:00",
        "1-3 PM", "noon", "midnight";
      * percentages: "80% reduction" -> 0.2, "drop to 25%" -> 0.25, "half",
        "one-fifth", "a quarter";
      * verbs: charge/charging -> no_charge_window, discharge -> no_discharge,
        "keep at least N kWh" -> reserve, "must not exceed N kWh" -> grid cap;
      * anything unmatched -> no_op.
    """
    raise NotImplementedError("TODO(Member B)")
