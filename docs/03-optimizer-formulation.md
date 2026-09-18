# Optimizer formulation — verified against all 10 public samples

> **Status: spiked and confirmed.** `docs/reference/lp_spike.py` implements
> exactly the formulation below and reproduces the organizer's reference
> `total_cost_bdt` on **all ten** public sample cases with a difference of
> `+0.00` on every one. Implement this; do not improvise.
>
> ```
> SAMPLE-01: ours=38365.00 expected=38365   diff=+0.00
> SAMPLE-02: ours=42885.00 expected=42885   diff=+0.00
> SAMPLE-03: ours=35480.00 expected=35480   diff=+0.00
> SAMPLE-04: ours=40495.00 expected=40495   diff=+0.00
> SAMPLE-05: ours=33950.00 expected=33950   diff=+0.00
> SAMPLE-06: ours=34090.00 expected=34090   diff=+0.00
> SAMPLE-07: ours=38550.00 expected=38550   diff=+0.00
> SAMPLE-08: ours=37665.00 expected=37665   diff=+0.00
> SAMPLE-09: ours=34873.00 expected=34873   diff=+0.00
> SAMPLE-10: ours=41620.00 expected=41620   diff=+0.00
> ```

## Why a linear program is exact here

The Problem Statement defines no battery round-trip efficiency and no grid
export. Charging and discharging are therefore linear and lossless, the demand
constraint is an equality, and every directive maps to a linear bound. The
feasible region is a polytope and the objective is linear, so the LP optimum
**is** the true optimum — there is no integrality gap to close and no heuristic
to tune. `scipy.optimize.linprog(method="highs")` solves it in single-digit
milliseconds, which leaves the whole latency budget for the model call.

You might expect to need a binary "charge XOR discharge" indicator, which would
make this a MILP. You do not: see *Why simultaneous charge and discharge is not
a problem* below.

## Variables (96)

| Block | Index | Meaning | Bounds |
|---|---|---|---|
| `g[h]` | `0 … 23` | grid import | `0 … max_grid[h]` (`None` → unbounded) |
| `s[h]` | `24 … 47` | solar used | `0 … effective_solar[h]` |
| `c[h]` | `48 … 71` | charge | `0 … 0 if charge_blocked[h] else max_charge` |
| `d[h]` | `72 … 95` | discharge | `0 … 0 if discharge_blocked[h] else max_discharge` |

All four bound vectors come straight off the `ConstraintSet`. Note that
curtailment is free: `s[h]` has an upper bound, not an equality, so unused solar
simply is not used.

## Objective

```
minimise   Σ_h  tariff[h] · g[h]
```

Only `g` carries cost. Solar and battery movement are free.

## Equality constraints (25 rows)

```
for each h:   g[h] + s[h] + d[h] − c[h] = demand[h]        # §9.5 energy balance
              Σ_h c[h] − Σ_h d[h]       = 0                # §9.6 end-of-day neutrality
```

The neutrality row is what stops the solver draining the starting battery for
free. It is the single constraint teams most often forget.

## Inequality constraints (48 rows)

With `E[h] = E0 + Σ_{k≤h} (c[k] − d[k])`, for each `h`:

```
  Σ_{k≤h} c[k] − Σ_{k≤h} d[k]  ≤  capacity − E0        # E[h] ≤ capacity
−(Σ_{k≤h} c[k] − Σ_{k≤h} d[k]) ≤  E0 − min_energy[h]   # E[h] ≥ active minimum
```

`min_energy[h]` already folds in `minimum_battery_reserve`
(`max(base_minimum, directive_value)`), so the optimizer needs no special case.

## Why simultaneous charge and discharge is not a problem

An LP may return `c[h] > 0` and `d[h] > 0` in the same hour — physically silly,
but never *cheaper*, since both are free and they cancel. Collapse to a net:

```python
net = c[h] - d[h]
if   net >  TOL_SNAP: action, battery_kwh = "charge",    net
elif net < -TOL_SNAP: action, battery_kwh = "discharge", -net
else:                 action, battery_kwh = "idle",      0.0
```

The collapse is always legal:

- **Energy balance survives.** `g + s + d − c = demand` ⇔ `g + s − net = demand`,
  and the emitted plan uses `net`.
- **Rate limits survive.** `net ≤ c ≤ max_charge` and `−net ≤ d ≤ max_discharge`.
- **Blocked windows survive.** `net > 0` requires `c > 0`, impossible when
  `charge_blocked[h]`, and symmetrically for discharge.
- **State of charge is unchanged**, because `E` only ever depended on `c − d`.

So no binary variables, no MILP, no branch and bound.

## Post-processing — where plans actually go wrong

1. **Recompute `battery_energy_after_kwh` by walking the emitted `battery_kwh`
   sequence.** Do not copy the LP's cumulative values. The judge replays the
   plan from the actions; if the two ever disagree by more than 0.01 the case is
   invalid even though the LP was right.
2. **Snap numeric dust.** HiGHS returns things like `-3.4e-14`. Clip `|x| < 1e-9`
   to `0`, clamp every emitted value to `≥ 0`, then round to 6 decimals.
3. **Force the last hour home.** After rounding, set
   `plan[23].battery_energy_after_kwh = battery.initial_energy_kwh` exactly, and
   adjust `plan[23].battery_kwh` to match the transition from hour 22 if rounding
   moved it. Neutrality is a hard invalidator.
4. **Recompute the three totals from the emitted plan**, in
   `pipeline.summarise_totals`. Never report the LP objective.

## Infeasibility ladder

Organizer scoring scenarios are guaranteed feasible, so infeasibility means *our
interpretation is wrong*, not that the scenario is impossible. Do not return an
invalid plan; degrade instead:

1. Solve with every directive.
2. On `InfeasibleError`, drop directives one at a time — lowest confidence first
   (`source == "fallback"` before `source == "llm"`), and hard windows
   (`no_charge` / `no_discharge`) before caps — re-solving each time.
3. Solve with no directives at all.
4. `safe_baseline_plan()`: grid covers demand, free solar is used up to the
   effective cap, battery idle. Balance holds, the battery never moves so bounds
   and neutrality hold trivially. Only a `max_grid_window` can break it, so
   pre-charge before capped hours and discharge inside them by exactly the
   shortfall.

Log which rung fired. Never silently return rung 4 as if it were rung 1.

## Numerical settings

- `method="highs"` (HiGHS dual simplex; deterministic, no tuning needed).
- Build `A_ub` / `A_eq` as dense `numpy` arrays — at 96 columns, sparsity buys
  nothing and costs debugging time.
- Assert `result.status == 0` before reading `result.x`; `status == 2` is
  infeasible, `status == 3` unbounded (which would mean a bug in the bounds).
