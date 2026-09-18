"""
24-hour cost-minimising schedule.  OWNER: Kabya — Lane C.

Formulation is a pure linear program — the battery here is lossless (no
round-trip efficiency in the Problem Statement), so LP gives the exact optimum,
not an approximation. The formulation in docs/03-optimizer-formulation.md was
spiked against all 10 public samples and reproduced the organizer's reference
cost to the cent on every one. Implement that formulation; do not improvise a
greedy heuristic as the primary solver.

Decision variables, 96 of them:
    g[h]  grid import            0 <= g[h] <= max_grid[h]
    s[h]  solar used             0 <= s[h] <= effective_solar[h]
    c[h]  charge                 0 <= c[h] <= 0 if blocked else max_charge
    d[h]  discharge              0 <= d[h] <= 0 if blocked else max_discharge

Objective:  min  sum_h tariff[h] * g[h]

Equalities:
    g[h] + s[h] + d[h] - c[h] = demand[h]              for every h
    sum_h c[h] - sum_h d[h]   = 0                      end-of-day neutrality

Inequalities, with E[h] = E0 + sum_{k<=h} (c[k] - d[k]):
    E[h] <= capacity
    E[h] >= min_energy[h]

Post-processing (this is where plans go wrong, be careful):
  * net[h] = c[h] - d[h]; emit ONE action per hour. net > tol -> charge,
    net < -tol -> discharge, else idle with battery_kwh = 0.
  * Recompute battery_energy_after_kwh by walking the emitted battery_kwh
    sequence, not from the LP's own values, so the judge's replay matches.
  * Snap |value| < 1e-9 to 0; clip tiny negatives; round to 6 decimals.
  * Force the final battery_energy_after_kwh to exactly initial_energy_kwh.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import linprog

from app.schemas import HORIZON, BatteryInput, ConstraintSet, Directive, HourInput, HourPlan

log = logging.getLogger(__name__)

# Below this the LP is reporting numeric dust, not a decision.
SNAP = 1e-9
# Emitted values carry six decimals; anything smaller is rounded away, so a
# non-zero action is always >= 1e-6 and "idle iff battery_kwh == 0" holds.
DECIMALS = 6


class InfeasibleError(RuntimeError):
    """The LP has no solution under the given ConstraintSet."""


def _snap(value: float) -> float:
    return 0.0 if abs(value) < SNAP else float(value)


def _round(value: float) -> float:
    return round(float(value) + 0.0, DECIMALS)


def _scenario_arrays(hours: Sequence[HourInput]) -> Tuple[List[float], List[float]]:
    ordered = sorted(hours, key=lambda h: h.hour)
    return (
        [float(h.demand_kwh) for h in ordered],
        [float(h.tariff_bdt_per_kwh) for h in ordered],
    )


# --------------------------------------------------------------------------
# The linear program
# --------------------------------------------------------------------------


def solve(
    hours: List[HourInput],
    battery: BatteryInput,
    constraints: ConstraintSet,
) -> List[HourPlan]:
    """Optimal 24-entry plan. Raises InfeasibleError if the LP has no solution."""
    demand, tariff = _scenario_arrays(hours)
    capacity = float(battery.capacity_kwh)
    initial = float(battery.initial_energy_kwh)
    max_charge = float(battery.max_charge_kwh_per_hour)
    max_discharge = float(battery.max_discharge_kwh_per_hour)

    n = 4 * HORIZON
    cost = np.zeros(n)
    cost[:HORIZON] = tariff

    # Equalities: 24 balance rows + 1 neutrality row.
    a_eq = np.zeros((HORIZON + 1, n))
    b_eq = np.zeros(HORIZON + 1)
    for h in range(HORIZON):
        a_eq[h, h] = 1.0                    # grid
        a_eq[h, HORIZON + h] = 1.0          # solar used
        a_eq[h, 3 * HORIZON + h] = 1.0      # discharge
        a_eq[h, 2 * HORIZON + h] = -1.0     # charge
        b_eq[h] = demand[h]
    a_eq[HORIZON, 2 * HORIZON : 3 * HORIZON] = 1.0
    a_eq[HORIZON, 3 * HORIZON : 4 * HORIZON] = -1.0
    b_eq[HORIZON] = 0.0

    # Inequalities: state of charge inside [min_energy[h], capacity] every hour.
    a_ub = np.zeros((2 * HORIZON, n))
    b_ub = np.zeros(2 * HORIZON)
    for h in range(HORIZON):
        row = np.zeros(n)
        row[2 * HORIZON : 2 * HORIZON + h + 1] = 1.0
        row[3 * HORIZON : 3 * HORIZON + h + 1] = -1.0
        a_ub[2 * h] = row
        b_ub[2 * h] = capacity - initial
        a_ub[2 * h + 1] = -row
        b_ub[2 * h + 1] = initial - float(constraints.min_energy_kwh[h])

    bounds: List[Tuple[float, Optional[float]]] = []
    for h in range(HORIZON):
        cap = constraints.max_grid_kwh[h]
        bounds.append((0.0, None if cap is None else max(0.0, float(cap))))
    for h in range(HORIZON):
        bounds.append((0.0, max(0.0, float(constraints.effective_solar_kwh[h]))))
    for h in range(HORIZON):
        bounds.append((0.0, 0.0 if constraints.charge_blocked[h] else max_charge))
    for h in range(HORIZON):
        bounds.append((0.0, 0.0 if constraints.discharge_blocked[h] else max_discharge))

    result = linprog(cost, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if result.status != 0 or result.x is None:
        raise InfeasibleError(f"LP did not solve (status {result.status}: {result.message})")

    return _emit_plan(result.x, demand, battery, constraints)


def _emit_plan(
    x: np.ndarray,
    demand: List[float],
    battery: BatteryInput,
    constraints: ConstraintSet,
) -> List[HourPlan]:
    """Turn the LP vector into a plan the judge's own replay will reproduce."""
    initial = float(battery.initial_energy_kwh)
    max_charge = float(battery.max_charge_kwh_per_hour)
    max_discharge = float(battery.max_discharge_kwh_per_hour)

    solar_used: List[float] = []
    net: List[float] = []
    for h in range(HORIZON):
        available = max(0.0, float(constraints.effective_solar_kwh[h]))
        s = min(available, max(0.0, _snap(float(x[HORIZON + h]))))
        solar_used.append(_round(s))

        raw_net = _snap(float(x[2 * HORIZON + h]) - float(x[3 * HORIZON + h]))
        net.append(_round(_clamp_net(raw_net, h, max_charge, max_discharge, constraints)))

    # End-of-day neutrality (§9.6) is a hard invalidator, so the last hour is
    # derived from the walked state rather than trusted from the LP: whatever
    # rounding did to hours 0..22, hour 23 is exactly the move that lands on E0.
    energy_after: List[float] = []
    energy = initial
    for h in range(HORIZON - 1):
        energy = _round(energy + net[h])
        energy_after.append(energy)

    wanted = initial - energy
    final = _round(_clamp_net(_round(wanted), HORIZON - 1, max_charge, max_discharge, constraints))
    net[HORIZON - 1] = final
    if abs(final - wanted) <= 1e-6:
        energy_after.append(_round(initial))
    else:
        # Cannot close the loop inside the rate limits. Report the state we
        # actually reach and let verify() reject it rather than lying about it.
        log.warning("optimizer: hour 23 cannot return to E0 (short by %.6f kWh)", wanted - final)
        energy_after.append(_round(energy + final))

    plan: List[HourPlan] = []
    for h in range(HORIZON):
        movement = net[h]
        grid = demand[h] - solar_used[h] + movement
        if grid < 0.0:
            # Cannot happen from a solved LP, but curtailing keeps the balance
            # exact instead of clamping grid and breaking it.
            solar_used[h] = _round(max(0.0, solar_used[h] + grid))
            grid = demand[h] - solar_used[h] + movement
        grid = _round(max(0.0, _snap(grid)))

        if movement > 0.0:
            action, magnitude = "charge", movement
        elif movement < 0.0:
            action, magnitude = "discharge", -movement
        else:
            action, magnitude = "idle", 0.0

        plan.append(
            HourPlan(
                hour=h,
                grid_kwh=grid,
                solar_used_kwh=solar_used[h],
                battery_action=action,
                battery_kwh=_round(magnitude),
                battery_energy_after_kwh=max(0.0, energy_after[h]),
            )
        )
    return plan


def _clamp_net(
    value: float,
    hour: int,
    max_charge: float,
    max_discharge: float,
    constraints: ConstraintSet,
) -> float:
    """Keep a net movement inside the rate limits and blocked windows."""
    if value > 0.0:
        if constraints.charge_blocked[hour]:
            return 0.0
        return min(value, max_charge)
    if value < 0.0:
        if constraints.discharge_blocked[hour]:
            return 0.0
        return -min(-value, max_discharge)
    return 0.0


# --------------------------------------------------------------------------
# The safe baseline — bottom of the failure ladder, never raises
# --------------------------------------------------------------------------


def safe_baseline_plan(
    hours: List[HourInput],
    battery: BatteryInput,
    constraints: ConstraintSet,
) -> List[HourPlan]:
    """Always-valid, never-optimal plan used when the LP fails.

    Buy everything from the grid, use free solar up to the effective cap, keep
    the battery idle: energy balance holds, the battery never moves so bounds
    and end-of-day neutrality hold trivially. Only a max_grid_window cap can
    break it, so discharge into capped hours (and pre-charge earlier) just
    enough to respect the cap.

    A valid expensive plan scores far better than an invalid cheap one: an
    invalid case loses its directive-application AND its optimization credit.
    """
    ordered = sorted(hours, key=lambda h: h.hour)
    demand = [float(h.demand_kwh) for h in ordered]
    tariff = [float(h.tariff_bdt_per_kwh) for h in ordered]
    initial = float(battery.initial_energy_kwh)
    max_charge = float(battery.max_charge_kwh_per_hour)
    max_discharge = float(battery.max_discharge_kwh_per_hour)

    solar_used = [
        min(max(0.0, float(constraints.effective_solar_kwh[h])), demand[h]) for h in range(HORIZON)
    ]
    idle_grid = [demand[h] - solar_used[h] for h in range(HORIZON)]

    # What each capped hour needs the battery to cover.
    desired = [0.0] * HORIZON
    for h in range(HORIZON):
        cap = constraints.max_grid_kwh[h]
        if cap is None or constraints.discharge_blocked[h]:
            continue
        shortfall = idle_grid[h] - max(0.0, float(cap))
        if shortfall > 0.0:
            desired[h] = -min(shortfall, max_discharge)

    # Pay for those discharges with earlier charging, cheapest hour first, and
    # only where the extra import does not itself breach a cap.
    for h in range(HORIZON):
        if desired[h] >= 0.0:
            continue
        outstanding = -desired[h]
        for k in sorted(range(h), key=lambda i: (tariff[i], i)):
            if outstanding <= 1e-9:
                break
            if constraints.charge_blocked[k] or desired[k] < 0.0:
                continue
            room = max_charge - desired[k]
            cap_k = constraints.max_grid_kwh[k]
            if cap_k is not None:
                room = min(room, max(0.0, float(cap_k) - (idle_grid[k] + desired[k])))
            take = min(outstanding, max(0.0, room))
            if take <= 0.0:
                continue
            desired[k] += take
            outstanding -= take

    floors = _reachability_floors(constraints, battery)
    net, energy_after = _simulate(desired, battery, constraints, floors)

    # Close any residual so the day ends exactly where it started.
    for _ in range(8):
        residual = energy_after[HORIZON - 1] - initial
        if abs(residual) <= 1e-9:
            break
        if residual > 0.0:  # too much stored: discharge more, latest and dearest first
            outstanding = residual
            for h in sorted(range(HORIZON), key=lambda i: (-tariff[i], -i)):
                if outstanding <= 1e-9:
                    break
                if constraints.discharge_blocked[h]:
                    continue
                room = max_discharge - max(0.0, -desired[h])
                take = min(outstanding, max(0.0, room))
                desired[h] -= take
                outstanding -= take
        else:  # too little stored: charge more, cheapest first
            outstanding = -residual
            for h in sorted(range(HORIZON), key=lambda i: (tariff[i], i)):
                if outstanding <= 1e-9:
                    break
                if constraints.charge_blocked[h]:
                    continue
                room = max_charge - max(0.0, desired[h])
                cap_h = constraints.max_grid_kwh[h]
                if cap_h is not None:
                    room = min(room, max(0.0, float(cap_h) - (idle_grid[h] + max(0.0, desired[h]))))
                take = min(outstanding, max(0.0, room))
                desired[h] += take
                outstanding -= take
        net, energy_after = _simulate(desired, battery, constraints, floors)

    plan: List[HourPlan] = []
    for h in range(HORIZON):
        movement = net[h]
        grid = demand[h] - solar_used[h] + movement
        if grid < 0.0:
            solar_used[h] = _round(max(0.0, solar_used[h] + grid))
            grid = demand[h] - solar_used[h] + movement
        grid = _round(max(0.0, _snap(grid)))

        if movement > 0.0:
            action, magnitude = "charge", movement
        elif movement < 0.0:
            action, magnitude = "discharge", -movement
        else:
            action, magnitude = "idle", 0.0

        after = energy_after[h]
        if h == HORIZON - 1 and abs(after - initial) <= 1e-6:
            after = initial

        plan.append(
            HourPlan(
                hour=h,
                grid_kwh=grid,
                solar_used_kwh=_round(max(0.0, solar_used[h])),
                battery_action=action,
                battery_kwh=_round(magnitude),
                battery_energy_after_kwh=_round(max(0.0, after)),
            )
        )
    return plan


def _simulate(
    desired: List[float],
    battery: BatteryInput,
    constraints: ConstraintSet,
    floors: Optional[List[float]] = None,
) -> Tuple[List[float], List[float]]:
    """Clamp a wish-list of net movements into a physically legal sequence.

    Guarantees, by construction: rate limits, blocked windows, capacity, and the
    per-hour energy floor (which can force a charge the caller never asked for).

    `floors` is the look-ahead floor from `_reachability_floors`. Without it a
    reserve that needs several hours of charging is met too late: the floor is
    only noticed in the hour it applies, by which time one hour of charging is
    all that is left.
    """
    capacity = float(battery.capacity_kwh)
    energy = float(battery.initial_energy_kwh)
    max_charge = float(battery.max_charge_kwh_per_hour)
    max_discharge = float(battery.max_discharge_kwh_per_hour)

    effective_floors = floors if floors is not None else _reachability_floors(constraints, battery)

    net: List[float] = []
    energy_after: List[float] = []
    for h in range(HORIZON):
        want = desired[h]
        floor = effective_floors[h]
        # The floor can demand a charge; the ceiling and the rate limits then win.
        want = max(want, floor - energy)
        want = min(want, capacity - energy)
        if want > 0.0 and constraints.charge_blocked[h]:
            want = 0.0
        if want < 0.0 and constraints.discharge_blocked[h]:
            want = 0.0
        want = max(-max_discharge, min(max_charge, want))
        want = min(want, capacity - energy)
        want = max(want, -energy)

        movement = _round(_snap(want))
        energy = _round(energy + movement)
        net.append(movement)
        energy_after.append(energy)
    return net, energy_after


def _reachability_floors(constraints: ConstraintSet, battery: BatteryInput) -> List[float]:
    """Per-hour energy floors that also account for how long charging takes.

    A reserve of 500 kWh at hour 12 on a battery starting at 200 with a 100
    kWh/h charge limit has to start charging at hour 10, not hour 12. Walking
    the floors backwards propagates each requirement as far back as the charge
    rate makes necessary. Hour 23 additionally carries the neutrality target,
    so the run home to the initial level is planned for too.
    """
    capacity = float(battery.capacity_kwh)
    initial = float(battery.initial_energy_kwh)
    max_charge = float(battery.max_charge_kwh_per_hour)

    floors = [min(float(constraints.min_energy_kwh[h]), capacity) for h in range(HORIZON)]
    floors[HORIZON - 1] = max(floors[HORIZON - 1], min(initial, capacity))
    for h in range(HORIZON - 2, -1, -1):
        # Energy the next hour can start from and still reach its own floor.
        climbable = 0.0 if constraints.charge_blocked[h + 1] else max_charge
        floors[h] = max(floors[h], min(floors[h + 1] - climbable, capacity))
    return floors


# --------------------------------------------------------------------------
# The infeasibility ladder (Lane C task C5)
# --------------------------------------------------------------------------


@dataclass
class SolveOutcome:
    """What the ladder settled on. `rung` is for logging and plan_summary."""

    plan: List[HourPlan]
    constraints: ConstraintSet
    rung: str
    dropped_note_indices: List[int] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


def _drop_priority(directive: Directive) -> Tuple[int, int, int]:
    """Least trustworthy and most likely to over-constrain, first."""
    source_rank = {"fallback": 0, "llm_repaired": 1, "llm": 2, "safe_default": 3}
    type_rank = {
        "no_charge_window": 0,
        "no_discharge_window": 0,
        "max_grid_window": 1,
        "minimum_battery_reserve": 2,
        "solar_reduction": 3,
    }
    return (
        source_rank.get(directive.source, 2),
        type_rank.get(directive.directive_type, 4),
        directive.note_index,
    )


def solve_with_ladder(
    hours: List[HourInput],
    battery: BatteryInput,
    directives: List[Directive],
) -> SolveOutcome:
    """Solve, degrading through the ladder until a plan verifies. Never raises.

    Organizer scoring scenarios are guaranteed feasible, so infeasibility means
    *our interpretation* is wrong. Rungs:
      1. every directive
      2. drop the least trustworthy directives one at a time
      3. no directives at all
      4. the safe baseline
    """
    # Imported here: guardrails and verifier are siblings in Lane C, and keeping
    # the import local documents that solve() itself has no such dependency.
    from app.guardrails import build_constraint_set
    from app.verifier import verify

    ordered = sorted(hours, key=lambda h: h.hour)
    applicable = [d for d in directives if d.applies]
    trail: List[str] = []

    def attempt(active: List[Directive], rung: str, dropped: List[int]) -> Optional[SolveOutcome]:
        constraints = build_constraint_set(active, ordered, battery)
        try:
            plan = solve(ordered, battery, constraints)
        except InfeasibleError as exc:
            trail.append(f"{rung}: {exc}")
            return None
        except Exception as exc:  # pragma: no cover - solver blew up unexpectedly
            log.exception("optimizer: %s raised", rung)
            trail.append(f"{rung}: solver error ({type(exc).__name__})")
            return None
        violations = verify(plan, ordered, battery, constraints)
        if violations:
            trail.append(f"{rung}: plan failed self-verification ({violations[0]})")
            return None
        return SolveOutcome(plan=plan, constraints=constraints, rung=rung, dropped_note_indices=dropped, notes=list(trail))

    outcome = attempt(applicable, "all_directives", [])
    if outcome is not None:
        return outcome

    order = sorted(applicable, key=_drop_priority)
    dropped: List[int] = []
    for victim in order[:-1] if len(order) > 1 else []:
        dropped.append(victim.note_index)
        remaining = [d for d in applicable if d.note_index not in dropped]
        outcome = attempt(remaining, f"dropped_{len(dropped)}_directive(s)", list(dropped))
        if outcome is not None:
            log.warning("optimizer: dropped note(s) %s to reach feasibility", dropped)
            return outcome

    outcome = attempt([], "no_directives", [d.note_index for d in applicable])
    if outcome is not None:
        log.warning("optimizer: solved with no directives applied")
        return outcome

    constraints = build_constraint_set(applicable, ordered, battery)
    try:
        plan = safe_baseline_plan(ordered, battery, constraints)
    except Exception:  # pragma: no cover - contract says it never raises
        log.exception("optimizer: safe baseline raised, emitting the grid-only plan")
        plan = _grid_only_plan(ordered, battery, constraints)
    trail.append("safe_baseline: last rung")
    log.error("optimizer: fell through to the safe baseline")
    return SolveOutcome(
        plan=plan,
        constraints=constraints,
        rung="safe_baseline",
        dropped_note_indices=[],
        notes=list(trail),
    )


def _grid_only_plan(
    hours: List[HourInput],
    battery: BatteryInput,
    constraints: ConstraintSet,
) -> List[HourPlan]:
    """Absolute last resort: buy everything, touch nothing. Cannot fail."""
    initial = float(battery.initial_energy_kwh)
    plan: List[HourPlan] = []
    for index, hour in enumerate(sorted(hours, key=lambda h: h.hour)):
        available = max(0.0, float(constraints.effective_solar_kwh[index]))
        solar = min(available, float(hour.demand_kwh))
        plan.append(
            HourPlan(
                hour=hour.hour,
                grid_kwh=_round(max(0.0, float(hour.demand_kwh) - solar)),
                solar_used_kwh=_round(solar),
                battery_action="idle",
                battery_kwh=0.0,
                battery_energy_after_kwh=_round(initial),
            )
        )
    return plan
