# Module contracts — the interfaces nobody may change alone

Four people are editing this repo at once for four hours. These signatures are
the seam. `app/schemas.py` and `app/config.py` are **frozen**: if you genuinely
need a change there, say so in the team channel and change it once, together.

## File ownership (no two people edit the same file)

| Files | Owner |
|---|---|
| `app/main.py`, `app/pipeline.py` | **A** |
| `app/llm/*` | **B** |
| `app/guardrails.py`, `app/optimizer.py`, `app/verifier.py` | **C** |
| `harness/*`, `tests/*`, `Dockerfile`, `README.md`, deploy config | **D** |
| `app/schemas.py`, `app/config.py`, `docs/*` | shared, frozen, announce changes |

## The four seams

### B → C  (raw interpretation)

```python
async def interpret_notes(notes: list[str], battery: BatteryInput) -> list[dict]
```

Returns one dict per note, in note order, **untrusted**. Expected keys — all
optional, because the model is allowed to get it wrong:

```python
{
  "note_index": 0,
  "directive_type": "solar_reduction",     # or anything; guardrails validates
  "hours": [13, 14],                       # or null
  "factor": 0.2,                           # or null
  "minimum_energy_kwh": null,
  "max_grid_kwh": null,
  "explanation": "…"
}
```

Raises `InterpretationUnavailable` only when every provider is exhausted.

### C → C  (validated directives, then constraints)

```python
def validate_interpretations(raw: list[dict], notes: list[str],
                             battery: BatteryInput) -> list[Directive]
def build_constraint_set(directives: list[Directive], hours: list[HourInput],
                         battery: BatteryInput) -> ConstraintSet
```

`validate_interpretations` **never raises** and always returns exactly
`len(notes)` directives with `note_index == 0..N-1`.

### C → A  (plan)

```python
def solve(hours, battery, constraints) -> list[HourPlan]      # raises InfeasibleError
def safe_baseline_plan(hours, battery, constraints) -> list[HourPlan]   # never raises
def verify(plan, hours, battery, constraints) -> list[str]    # [] == valid
```

### A → HTTP  (response)

```python
async def run_pipeline(payload: ScenarioRequest, request_id: str) -> OptimizeResponse
```

Never raises for a well-formed scenario.

## Invariants every module must preserve

1. `len(directive_interpretation) == len(operator_notes)`, ordered `0..N-1`.
2. `applies == (directive_type != "no_op")`, always.
3. `structured_adjustment is None` **iff** `directive_type == "no_op"`.
4. Every `hours` array: unique ints `0..23`, ascending.
5. `hourly_plan` has 24 entries, hours `0..23` ascending.
6. `battery_kwh == 0` exactly when `battery_action == "idle"`.
7. Totals are recomputed **from `hourly_plan`** in `pipeline.summarise_totals`
   and nowhere else.
8. Nothing returned to the client, and nothing logged, contains an API key, a
   raw prompt with a key in it, or a stack trace.

## Working agreement

- Branch per member: `feat/a-api`, `feat/b-llm`, `feat/c-solver`, `feat/d-harness`.
- Small commits, push often, PR into `main`, do not force-push `main`.
- If you need a stub from someone else that does not exist yet, write a local
  fake in **your own** file and delete it at integration. Do not edit their file.
- Announce it in the team channel the moment you touch anything in the shared
  column above.
