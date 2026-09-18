# Spec digest — everything the code must obey

Condensed from `BUP_CSE_FEST_2026_Preliminary_Problem_Statement_GridWise_LLM.pdf`
(canonical for behaviour) and the Participant Guide (canonical for deployment
and scoring). **If this digest and the PDFs ever disagree, the PDFs win.**

---

## 1. The task

BUP's campus buys grid electricity, generates rooftop solar, and runs a battery.
Given the next 24 hours of demand / solar / tariff, a battery spec, and 1–3
natural-language operator notes, return:

1. a machine-checkable interpretation of every note, and
2. a valid, cheap 24-hour operating schedule that obeys every note that applies.

The LLM is **mandatory** in the note-interpretation path. Using a model only for
`plan_summary` or documentation fails the challenge requirement outright and
makes the team ineligible for the shortlist.

## 2. Endpoints

| Method | Path | Behaviour |
|---|---|---|
| GET | `/health` | HTTP 200, `{"status":"ok"}`, ready within 60 s of start |
| POST | `/optimize-energy` | one scenario in, interpretation + plan out, ≤ 30 s |

Codes: `200` success · `400` malformed JSON or structurally invalid request ·
`422` optional, semantically invalid but well-formed · `500` controlled internal
error with **no stack trace and no secrets**.

## 3. Request

```jsonc
{
  "scenario_id": "GRID-101",
  "operator_notes": ["...", "..."],          // 1–3 non-empty strings
  "hours": [                                  // exactly 24, hours 0..23
    {"hour": 0, "demand_kwh": 180, "solar_kwh": 0, "tariff_bdt_per_kwh": 7}
  ],
  "battery": {
    "capacity_kwh": 500,
    "initial_energy_kwh": 200,
    "minimum_energy_kwh": 50,
    "max_charge_kwh_per_hour": 100,
    "max_discharge_kwh_per_hour": 100
  }
}
```

## 4. Response

```jsonc
{
  "scenario_id": "GRID-101",                  // must echo the request
  "directive_interpretation": [               // exactly one entry per note,
    {                                         // in note_index order 0..N-1
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
      "explanation": "..."
    }
  ],
  "hourly_plan": [                            // exactly 24 entries, hours 0..23
    {
      "hour": 0,
      "grid_kwh": 180,
      "solar_used_kwh": 0,
      "battery_action": "idle",               // charge | discharge | idle
      "battery_kwh": 0,
      "battery_energy_after_kwh": 200
    }
  ],
  "total_grid_kwh": 0,
  "total_cost_bdt": 0,
  "peak_grid_kwh": 0,
  "plan_summary": "..."
}
```

## 5. The six directive types

| `directive_type` | Meaning | `structured_adjustment` | Optimizer effect |
|---|---|---|---|
| `solar_reduction` | less usable solar in those hours | `{"hours":[…], "factor": n}` | `effective_solar[h] = solar[h] * factor` |
| `minimum_battery_reserve` | keep at least this much stored | `{"hours":[…], "minimum_energy_kwh": n}` | `E_after[h] >= max(base_min, n)` |
| `no_charge_window` | cannot charge | `{"hours":[…]}` | `charge[h] = 0` |
| `no_discharge_window` | cannot discharge | `{"hours":[…]}` | `discharge[h] = 0` |
| `max_grid_window` | grid import capped | `{"hours":[…], "max_grid_kwh": n}` | `grid[h] <= n` |
| `no_op` | irrelevant note | `null` | nothing |

## 6. Interpretation rules that are machine-checked

- Exactly one entry per note, in `note_index` order, no gaps, no duplicates.
- `applies = true` for **every** non-`no_op` directive. `applies = false` **only**
  for `no_op`, and then `structured_adjustment` must be `null`.
- `hours` are unique integers `0..23` in **ascending order**. A window that wraps
  midnight still sorts ascending: 10 PM–2 AM → `[0, 1, 22, 23]`.
- Windows are **start-inclusive, end-exclusive**: "1 PM to 3 PM" → `[13, 14]`;
  "6 PM until 10 PM" → `[18, 19, 20, 21]`; "between 11 AM and 2 PM" → `[11,12,13]`.
- `factor` is the fraction that **remains**, in `[0, 1]`: "80% reduction" → `0.2`,
  "drops to 20%" → `0.2`, "about half" → `0.5`, "one-fifth" → `0.2`.
- Reserves expressed as a percentage are a percentage of `battery.capacity_kwh`
  ("50% of capacity" with a 200 kWh battery → `100`).
- Reserve values: finite, ≥ 0, ≤ capacity. `max_grid_kwh`: finite, ≥ 0.
- The model may never invent demand, solar, tariff, battery limits, or a
  directive type outside the six above.
- Hidden notes paraphrase the same rules in unseen wording. Never key on public
  phrasing.

## 7. Energy and battery rules (§09)

```
charge:    E_after = E_before + battery_kwh
discharge: E_after = E_before - battery_kwh
idle:      E_after = E_before  and  battery_kwh = 0

active_minimum[h] <= E_after[h] <= capacity_kwh
charge    <= max_charge_kwh_per_hour
discharge <= max_discharge_kwh_per_hour
0 <= solar_used_kwh <= effective_solar_kwh          (surplus is curtailed)

grid_kwh + solar_used_kwh + battery_discharge_kwh
      = demand_kwh + battery_charge_kwh             (every hour)

battery_energy_after_kwh[23] == initial_energy_kwh  (end-of-day neutrality)
```

No grid export. The battery is **lossless** — the statement defines no
round-trip efficiency, which is what makes an exact LP possible.

Objective: `total_cost_bdt = Σ grid_kwh[h] * tariff_bdt_per_kwh[h]`, minimised
**after** validity, never instead of it.

## 8. What the judge does

1. Compares `directive_interpretation` against its own ground truth — relevance,
   type, hours, numeric values — within 0.01 tolerance. Free-text `explanation`
   is **not** matched byte-for-byte.
2. Replays `hourly_plan` hour by hour using **its own ground-truth directives**,
   not the interpretation we returned. A correctly-extracted-but-not-applied
   directive still fails the case.
3. Recomputes `total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh` from
   `hourly_plan` and compares to the values we reported.
4. Scores cost only if the case is valid:
   `quality = min(1, organizer_optimal_cost / our_recalculated_cost)`.

Numeric tolerance throughout: **0.01 kWh / 0.01 BDT absolute**.

## 9. Non-negotiables

- Equivalent optimal schedules are accepted — no byte-for-byte plan matching.
- Organizer scoring scenarios are always feasible and never contain mutually
  contradictory hard directives.
- No secrets in the repo, the logs, the responses, or the Docker image.
- Only synthetic challenge data.
- Repository: created after question reveal, private during the event, public
  after the submission deadline.
