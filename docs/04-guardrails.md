# Guardrails — turning an untrusted guess into a legal directive

Owner: **Member C**. Nothing in `app/guardrails.py` may call a network service.
Pure functions only, so the whole file is unit-testable without a key.

## The policy in one line

**Repair what is unambiguously repairable; demote everything else to `no_op`;
never invent, never drop.**

Dropping an entry breaks the "exactly one entry per note in note_index order"
invariant and costs schema points on top of interpretation points. Inventing a
directive the model did not produce is explicitly forbidden by §08.

## Stage 1 — shape

For each note index `0 … N-1`, find the raw entry whose `note_index` matches. If
the model omitted `note_index`, fall back to position. If two entries claim the
same index, keep the first and re-map the rest positionally. If an index is
missing entirely, synthesise a `no_op` for it.

## Stage 2 — type

`directive_type` must be one of the six in `schemas.DIRECTIVE_TYPES`. Accept
case and whitespace variation (`"Solar_Reduction"`, `" no_op "`). Anything else
→ `no_op`. Do **not** try to guess a type from the note text here; that is the
model's job, and a wrong guess is scored the same as a wrong model.

## Stage 3 — hours (`normalise_hours`)

Applies to every type except `no_op`.

| Input | Output | Why |
|---|---|---|
| `[14, 13]` | `[13, 14]` | ascending is mandatory |
| `[13, 13, 14]` | `[13, 14]` | unique is mandatory |
| `["13", 14.0]` | `[13, 14]` | weak models emit strings and floats |
| `[13, 25, 14]` | `[13, 14]` | drop the illegal hour, keep the rest |
| `13` | `[13]` | a scalar is a one-hour window |
| `[]`, `None`, `"afternoon"` | `[]` → demote to `no_op` | nothing usable |

An empty result after normalisation means the directive cannot be applied →
`no_op`.

## Stage 4 — numbers

**`factor`** (`solar_reduction`) — must land in `[0, 1]` as the *remaining*
fraction.

| Input | Output | Rule |
|---|---|---|
| `0.2` | `0.2` | already a fraction |
| `20`, `"20%"` | `0.2` | percent written as a whole number |
| `1.0` | `1.0` | legal — no reduction |
| `-0.1`, `1.4`, `"a lot"` | demote to `no_op` | not repairable |

Careful with the reduction/remainder trap: `80% reduction` means `factor = 0.2`.
That conversion belongs in the **prompt**, not here — here you cannot tell
whether `0.8` meant "80% remains" or "80% was lost". Only flip the value when the
model's own `explanation` says "reduction"/"drop of"/"decrease of" **and** it
also emitted the matching percentage; otherwise trust the number. Getting this
backwards silently doubles the error, so unit-test both directions.

**`minimum_energy_kwh`** — finite, `≥ 0`, `≤ battery.capacity_kwh`. Clamp to
capacity if above (a reserve above capacity is infeasible and would poison the
LP). Negative or non-numeric → `no_op`. If the model returned a percentage
(`50`, with a 200 kWh battery), the prompt should already have resolved it; as a
safety net, a value `≤ 1.0` on a battery whose capacity is far larger is almost
certainly a fraction — **log it, do not auto-convert.** Silent conversion is how
a correct `1 kWh` reserve becomes a wrong `200 kWh` one.

**`max_grid_kwh`** — finite, `≥ 0`. No upper clamp. Negative or non-numeric →
`no_op`.

## Stage 5 — `applies` and the adjustment

Set mechanically from the final type, never from the model:

```python
applies = directive_type != "no_op"
adjustment = None if not applies else {exact keys for that type}
```

`REQUIRED_KEYS` in `guardrails.py` is the authority on which keys each type
carries. Emit exactly those keys — no extras, the schema is checked.

## Stage 6 — `build_constraint_set`

Fold the surviving directives into six per-hour arrays. Order matters when two
directives hit the same hour:

| Field | Combination rule |
|---|---|
| `effective_solar_kwh[h]` | **multiply** each factor in: two 50% reductions → 0.25 |
| `min_energy_kwh[h]` | `max(base_minimum, every directive value)` |
| `max_grid_kwh[h]` | `min(every cap)`, `None` if uncapped |
| `charge_blocked[h]` | `any` |
| `discharge_blocked[h]` | `any` |

Keep `applied` populated with the `Directive` objects that actually contributed
— the pipeline reads it to write a truthful `plan_summary`, and the infeasibility
ladder reads `source` to decide what to drop first.

## Test list (write these, they are cheap and they catch real bugs)

- every directive type round-trips from a clean raw dict;
- unsorted / duplicate / string / out-of-range hours;
- `factor` as `20`, `"20%"`, `0.2`, `1.4`, `-1`;
- reserve above capacity clamps;
- unknown `directive_type` → `no_op` with `structured_adjustment is None`;
- three notes where the model returned only two entries;
- two entries claiming `note_index = 0`;
- raw `[]` → N all-`no_op` directives;
- two `solar_reduction`s overlapping on one hour multiply;
- two `max_grid_window`s overlapping take the min.
