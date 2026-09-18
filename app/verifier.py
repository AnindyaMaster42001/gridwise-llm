"""
Self-replay of the final plan.  OWNER: Kabya — Lane C.

The judge replays hourly_plan hour by hour against its own ground-truth
directives. We run the same replay against our own ConstraintSet before we
answer, so an invalid plan is never returned — if verification fails we ship the
safe baseline instead.

Checks (Problem Statement §09, §11.3):
  * 24 unique hours, 0..23;
  * all values finite and non-negative;
  * battery_kwh == 0 exactly when action == "idle";
  * charge <= max_charge, discharge <= max_discharge;
  * charge == 0 in blocked-charge hours, discharge == 0 in blocked-discharge;
  * min_energy[h] <= battery_energy_after_kwh[h] <= capacity;
  * state transition matches the declared action;
  * solar_used_kwh <= effective_solar[h];
  * grid_kwh <= max_grid[h] where capped;
  * grid + solar_used + discharge == demand + charge, every hour;
  * final battery_energy_after_kwh == initial_energy_kwh.
All comparisons use the 0.01 absolute tolerance from schemas.TOL.
"""

from __future__ import annotations

from typing import List

from app.schemas import BatteryInput, ConstraintSet, HourInput, HourPlan


def verify(
    plan: List[HourPlan],
    hours: List[HourInput],
    battery: BatteryInput,
    constraints: ConstraintSet,
) -> List[str]:
    """Return a list of human-readable violations. Empty list == valid."""
    raise NotImplementedError("TODO(Kabya): see docs/team/KABYA-lane-c-guardrails-optimizer.md")
