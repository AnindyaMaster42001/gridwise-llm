# Member A — API surface & pipeline orchestration

**You own:** `app/main.py`, `app/pipeline.py`
**You must not edit:** `app/llm/*` (B) · `app/guardrails.py`, `app/optimizer.py`,
`app/verifier.py` (C) · `harness/*`, `tests/*`, `Dockerfile`, `README.md` (D) ·
`app/schemas.py`, `app/config.py` (frozen, team decision only)

**Points you directly control:** all 10 of *API Contract & Schema*, most of the
10 for *Performance & Reliability*, and the correctness of the totals that every
other category is recalculated against.

**Read first:** `docs/00-spec-digest.md` §2–§4, `docs/02-contracts.md`,
`docs/01-architecture.md`.

---

## Why this lane exists

Three people are producing parts that only make sense together. You are the one
who decides what happens when one of those parts is late, slow, or wrong — and
the judge only ever sees your output. Your real product is not the happy path;
it is the guarantee that **a well-formed request always gets a valid 200 inside
the budget**, whatever else is on fire.

---

## Task A1 — pin the HTTP contract (~20 min)

`app/main.py` already serves `/health` and shapes errors. Verify and harden:

1. `GET /health` → `200 {"status":"ok"}`, never touches the model, answers in
   milliseconds even while `/optimize-energy` is busy.
2. `POST /optimize-energy` returns the exact response schema, with
   `scenario_id` echoed from the request.
3. **Malformed JSON and schema violations both return 400.** FastAPI's default
   is 422; the handler in `main.py` overrides that. Test it:
   ```bash
   curl -i -X POST localhost:8000/optimize-energy -H 'Content-Type: application/json' -d '{"scenario_id":'
   curl -i -X POST localhost:8000/optimize-energy -H 'Content-Type: application/json' -d '{"scenario_id":"x"}'
   ```
   Both must be 400, and the service must still serve `/health` afterwards.
4. Any unhandled exception → `500 {"error":"internal_error"}`. No stack trace, no
   config dump, no key. Log the traceback server-side only.

**Done when:** `bash scripts/smoke.sh http://localhost:8000` shows 200 / 400 /
and a live service after the bad request.

## Task A2 — `run_pipeline` (~40 min, the core)

```python
async def run_pipeline(payload: ScenarioRequest, request_id: str = "") -> OptimizeResponse:
    hours   = payload.ordered_hours()          # never trust arrival order
    battery = payload.battery

    # 1. mandatory LLM step, with its own timeout
    try:
        raw = await interpret_notes(payload.operator_notes, battery)
        source = "llm"
    except InterpretationUnavailable:
        raw = rule_based_interpret(payload.operator_notes, battery)
        source = "fallback"

    # 2. untrusted -> trusted. Never raises.
    directives  = validate_interpretations(raw, payload.operator_notes, battery)
    constraints = build_constraint_set(directives, hours, battery)

    # 3. solve, with the infeasibility ladder
    plan = solve_with_ladder(hours, battery, constraints, directives)

    # 4. never return a plan we know is invalid
    if verify(plan, hours, battery, constraints):
        plan = safe_baseline_plan(hours, battery, constraints)

    # 5. totals come from the plan, not from the solver
    total_grid, total_cost, peak = summarise_totals(plan, [h.tariff_bdt_per_kwh for h in hours])

    return OptimizeResponse(
        scenario_id=payload.scenario_id,
        directive_interpretation=[d.to_interpretation() for d in directives],
        hourly_plan=plan,
        total_grid_kwh=total_grid,
        total_cost_bdt=total_cost,
        peak_grid_kwh=peak,
        plan_summary=build_plan_summary(directives, total_cost, peak),
    )
```

Five things that are easy to get wrong here:

- **`payload.ordered_hours()`, always.** Hidden cases may send hours shuffled.
- **One entry per note, in order.** `validate_interpretations` guarantees it;
  do not filter, re-sort, or deduplicate its output afterwards.
- **`summarise_totals` reads the emitted plan.** Not the LP objective, not the
  constraint set. The judge recalculates from `hourly_plan` and compares at 0.01.
- **`peak_grid_kwh` is `max`, not the sum.**
- **Round once, at the end.** Six decimals is plenty; rounding twice is how a
  0.01 mismatch appears.

## Task A3 — the time budget (~20 min)

The judge fails a request at 30 s. `REQUEST_BUDGET_S` defaults to 22 s.

- Wrap the interpretation step in `asyncio.wait_for` with `LLM_TIMEOUT_S`.
- Track elapsed time from the top of the handler. If the remaining budget drops
  below ~3 s, skip straight to the regex fallback rather than starting a retry.
- The LP is milliseconds — it never needs a budget check. If it ever does, that
  is a bug in the bounds, not slowness.
- Run the solver with `asyncio.to_thread` so one request cannot block the event
  loop and stall a concurrent `/health`.

## Task A4 — `build_plan_summary` (~10 min)

Not machine-graded, but judges read it in tie-breaks, and a summary that claims
directives which were not applied is worse than no summary.

> "Applied 2 operator directives (solar reduced to 20% for hours 13–14; no
> battery charging in hours 14–15). Charged 180 kWh off-peak overnight and
> discharged 180 kWh across the evening peak; 1 note was not schedule-relevant.
> Total 2,395 kWh from the grid at 34,090.00 BDT, peak 175 kWh."

Build it from `constraints.applied` and the real numbers. If the safe baseline
fired, say so.

## Task A5 — integration duty (continuous)

You are the integrator. From T+1:30 you own `main` being green. When a lane is
late, stub it in **your** file and move on:

```python
async def _stub_interpret(notes, battery):
    return [{"note_index": i, "directive_type": "no_op"} for i in range(len(notes))]
```

---

## Definition of done

- [ ] `/health` and `/optimize-energy` match the spec exactly
- [ ] malformed JSON → 400; unhandled error → 500 with no trace; service survives both
- [ ] all 10 public samples return 200 with a plan that `verify()` accepts
- [ ] totals recomputed from `hourly_plan` agree with the harness at 0.01
- [ ] a killed LLM key still produces a valid 200
- [ ] p95 under 5 s over 3 repeats of the 10 samples
- [ ] nothing in a response or a log line contains a key or a traceback

## Agent prompt

> Read `docs/00-spec-digest.md`, `docs/01-architecture.md`, `docs/02-contracts.md`
> and `docs/team/MEMBER-A-api-orchestration.md` in this repo. Implement
> `app/pipeline.py` and harden `app/main.py` to the contract there. Only edit
> those two files. `app/schemas.py` is frozen. Where another member's module is
> not yet implemented, write a local stub inside `app/pipeline.py` and mark it
> `# STUB — remove at integration`. Then run
> `python3 -m pytest tests/ -q` and `bash scripts/smoke.sh http://localhost:8000`
> and report what passes.
