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

This module is written from the specification text, deliberately *not* by
reusing anything in app/optimizer.py. A verifier that shares code with the
solver happily certifies the solver's own bugs.
"""

from __future__ import annotations

import math
from typing import List, Optional

from app.schemas import HORIZON, TOL, BatteryInput, ConstraintSet, HourInput, HourPlan


def _finite(value: float) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _at(values: List, index: int, default=None):
    """Tolerate a short ConstraintSet array rather than raising mid-verification."""
    if 0 <= index < len(values):
        return values[index]
    return default


def verify(
    plan: List[HourPlan],
    hours: List[HourInput],
    battery: BatteryInput,
    constraints: ConstraintSet,
) -> List[str]:
    """Return a list of human-readable violations. Empty list == valid."""
    violations: List[str] = []

    # ---- structure -------------------------------------------------------
    if len(plan) != HORIZON:
        violations.append(f"hourly_plan has {len(plan)} entries, expected {HORIZON}")

    by_hour = {}
    for entry in plan:
        if entry.hour in by_hour:
            violations.append(f"hour {entry.hour}: duplicated in hourly_plan")
            continue
        by_hour[entry.hour] = entry
    missing = [h for h in range(HORIZON) if h not in by_hour]
    if missing:
        violations.append(f"hourly_plan is missing hours {missing}")

    demand_by_hour = {h.hour: float(h.demand_kwh) for h in hours}

    capacity = float(battery.capacity_kwh)
    initial = float(battery.initial_energy_kwh)
    max_charge = float(battery.max_charge_kwh_per_hour)
    max_discharge = float(battery.max_discharge_kwh_per_hour)

    # ---- hour-by-hour replay --------------------------------------------
    energy_before = initial
    last_energy_after: Optional[float] = None

    for hour in range(HORIZON):
        entry = by_hour.get(hour)
        if entry is None:
            continue

        grid = float(entry.grid_kwh)
        solar_used = float(entry.solar_used_kwh)
        battery_kwh = float(entry.battery_kwh)
        energy_after = float(entry.battery_energy_after_kwh)
        action = entry.battery_action

        # finite and non-negative
        bad_number = False
        for name, value in (
            ("grid_kwh", grid),
            ("solar_used_kwh", solar_used),
            ("battery_kwh", battery_kwh),
            ("battery_energy_after_kwh", energy_after),
        ):
            if not _finite(value):
                violations.append(f"hour {hour}: {name} is not a finite number")
                bad_number = True
            elif value < -TOL:
                violations.append(f"hour {hour}: {name} is negative ({value:.4f})")
                bad_number = True
        if bad_number:
            energy_before = energy_after if _finite(energy_after) else energy_before
            last_energy_after = energy_before
            continue

        # action / magnitude consistency
        if action == "idle":
            if abs(battery_kwh) > TOL:
                violations.append(
                    f"hour {hour}: battery_action is idle but battery_kwh is {battery_kwh:.4f}"
                )
        elif action in ("charge", "discharge"):
            if battery_kwh <= 0.0:
                violations.append(
                    f"hour {hour}: battery_action is {action} but battery_kwh is {battery_kwh:.4f}"
                )
        else:
            violations.append(f"hour {hour}: unknown battery_action {action!r}")

        charge = battery_kwh if action == "charge" else 0.0
        discharge = battery_kwh if action == "discharge" else 0.0

        # hourly rate limits (§9.3)
        if charge > max_charge + TOL:
            violations.append(
                f"hour {hour}: charge {charge:.4f} exceeds max_charge_kwh_per_hour {max_charge:.4f}"
            )
        if discharge > max_discharge + TOL:
            violations.append(
                f"hour {hour}: discharge {discharge:.4f} exceeds max_discharge_kwh_per_hour {max_discharge:.4f}"
            )

        # directive windows
        if _at(constraints.charge_blocked, hour, False) and charge > TOL:
            violations.append(f"hour {hour}: charging {charge:.4f} inside a no_charge_window")
        if _at(constraints.discharge_blocked, hour, False) and discharge > TOL:
            violations.append(f"hour {hour}: discharging {discharge:.4f} inside a no_discharge_window")

        # state transition (§9.1)
        expected_after = energy_before + charge - discharge
        if abs(energy_after - expected_after) > TOL:
            violations.append(
                f"hour {hour}: battery_energy_after_kwh {energy_after:.4f} does not follow "
                f"{energy_before:.4f} with {action} {battery_kwh:.4f} (expected {expected_after:.4f})"
            )

        # battery bounds (§9.2), directive reserve already folded into min_energy
        floor = float(_at(constraints.min_energy_kwh, hour, float(battery.minimum_energy_kwh)))
        if energy_after < floor - TOL:
            violations.append(
                f"hour {hour}: battery_energy_after_kwh {energy_after:.4f} below required minimum {floor:.4f}"
            )
        if energy_after > capacity + TOL:
            violations.append(
                f"hour {hour}: battery_energy_after_kwh {energy_after:.4f} above capacity {capacity:.4f}"
            )

        # solar usage (§9.4)
        available = float(_at(constraints.effective_solar_kwh, hour, 0.0))
        if solar_used > available + TOL:
            violations.append(
                f"hour {hour}: solar_used_kwh {solar_used:.4f} exceeds effective solar {available:.4f}"
            )

        # grid cap directive
        cap = _at(constraints.max_grid_kwh, hour, None)
        if cap is not None and grid > float(cap) + TOL:
            violations.append(
                f"hour {hour}: grid_kwh {grid:.4f} exceeds max_grid_window cap {float(cap):.4f}"
            )

        # energy balance (§9.5)
        if hour in demand_by_hour:
            demand = demand_by_hour[hour]
            supplied = grid + solar_used + discharge
            required = demand + charge
            if abs(supplied - required) > TOL:
                violations.append(
                    f"hour {hour}: energy balance broken, supply {supplied:.4f} != demand+charge {required:.4f}"
                )
        else:
            violations.append(f"hour {hour}: no matching demand entry in the scenario")

        energy_before = energy_after
        last_energy_after = energy_after

    # ---- end-of-day neutrality (§9.6) ------------------------------------
    if last_energy_after is None:
        violations.append("hourly_plan produced no battery state to check for neutrality")
    elif abs(last_energy_after - initial) > TOL:
        violations.append(
            f"end-of-day battery {last_energy_after:.4f} does not return to the initial {initial:.4f}"
        )

    return violations
