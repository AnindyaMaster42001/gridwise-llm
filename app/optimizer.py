"""
24-hour cost-minimising schedule.  OWNER: Member C.

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

from typing import List

from app.schemas import BatteryInput, ConstraintSet, HourInput, HourPlan


class InfeasibleError(RuntimeError):
    """The LP has no solution under the given ConstraintSet."""


def solve(
    hours: List[HourInput],
    battery: BatteryInput,
    constraints: ConstraintSet,
) -> List[HourPlan]:
    """Optimal 24-entry plan. Raises InfeasibleError if the LP has no solution."""
    raise NotImplementedError("TODO(Member C): see docs/team/MEMBER-C-guardrails-optimizer.md")


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
    raise NotImplementedError("TODO(Member C)")
