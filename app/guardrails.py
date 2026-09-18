"""
Deterministic validation of untrusted model output.  OWNER: Kabya — Lane C.

The Problem Statement (§08) treats LLM output as untrusted structured data.
Nothing here may call a model; everything here must be pure and unit-testable.

Two responsibilities:
  1. validate_interpretations(): raw dicts -> List[Directive], exactly one per
     note, in note_index order, every shape guaranteed legal.
  2. build_constraint_set(): List[Directive] -> ConstraintSet, the per-hour
     numbers the optimizer and verifier both read.

Repair-not-reject policy
------------------------
A near-miss from the model should be repaired, because a demoted note loses all
five "relevance" points for that case:
  * hours unsorted / duplicated      -> sort + dedupe
  * hours as floats or strings       -> coerce to int
  * hour out of 0..23                -> drop that hour (keep the rest)
  * factor given as 20 or "20%"      -> 0.2
  * factor expressed as the reduction rather than the remainder -> only convert
    when the model explicitly said "reduction"; otherwise trust the number
  * reserve above capacity           -> clamp to capacity
  * missing note_index               -> positional index
Anything still illegal after repair, or any unknown directive_type, becomes
no_op — never a guess at a different directive, never a dropped entry.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.schemas import BatteryInput, ConstraintSet, Directive, HourInput

REQUIRED_KEYS: Dict[str, Tuple[str, ...]] = {
    "solar_reduction": ("hours", "factor"),
    "minimum_battery_reserve": ("hours", "minimum_energy_kwh"),
    "no_charge_window": ("hours",),
    "no_discharge_window": ("hours",),
    "max_grid_window": ("hours", "max_grid_kwh"),
    "no_op": (),
}


def validate_interpretations(
    raw: List[Dict[str, Any]],
    notes: List[str],
    battery: BatteryInput,
) -> List[Directive]:
    """Return exactly `len(notes)` Directives, index 0..N-1, all legal.

    Never raises: a completely unusable `raw` yields all-no_op directives.
    """
    raise NotImplementedError("TODO(Kabya): see docs/team/KABYA-lane-c-guardrails-optimizer.md")


def normalise_hours(value: Any) -> List[int]:
    """Coerce to unique ints in 0..23, ascending. Returns [] if nothing survives."""
    raise NotImplementedError("TODO(Kabya)")


def normalise_factor(value: Any, explanation: str = "") -> Optional[float]:
    """Coerce to a usable-fraction in [0, 1], or None if impossible."""
    raise NotImplementedError("TODO(Kabya)")


def build_constraint_set(
    directives: List[Directive],
    hours: List[HourInput],
    battery: BatteryInput,
) -> ConstraintSet:
    """Fold applied directives into per-hour arrays (Problem Statement §5.3).

      solar_reduction          effective_solar[h] = solar[h] * factor   (multiply
                               if several land on one hour)
      minimum_battery_reserve  min_energy[h] = max(base_minimum, directive value)
      no_charge_window         charge_blocked[h] = True
      no_discharge_window      discharge_blocked[h] = True
      max_grid_window          max_grid[h] = min(existing cap, directive value)
    """
    raise NotImplementedError("TODO(Kabya)")
