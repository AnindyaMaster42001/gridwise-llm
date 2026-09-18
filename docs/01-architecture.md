# Architecture

```
                    POST /optimize-energy
                             │
                   ┌─────────▼──────────┐
                   │  app/main.py       │  FastAPI, status codes, error shaping
                   │  app/pipeline.py   │  orchestration + time budget   ANINDYA
                   └─────────┬──────────┘
                             │  operator_notes + battery
                   ┌─────────▼──────────┐
                   │  app/llm/          │  MANDATORY LLM STEP              NINAD
                   │   prompts.py       │  system prompt + flat JSON schema
                   │   client.py        │  provider abstraction + failover
                   │   interpreter.py   │  one call, cache, retry
                   │   fallback.py      │  regex backup, only if all fail
                   └─────────┬──────────┘
                             │  raw dicts  (UNTRUSTED)
                   ┌─────────▼──────────┐
                   │  app/guardrails.py │  repair → validate → Directive[] KABYA
                   │                    │  → ConstraintSet (per-hour arrays)
                   └─────────┬──────────┘
                             │  ConstraintSet (TRUSTED)
                   ┌─────────▼──────────┐
                   │  app/optimizer.py  │  linear program, exact optimum   KABYA
                   │                    │  safe_baseline_plan() on failure
                   └─────────┬──────────┘
                             │  HourPlan[24]
                   ┌─────────▼──────────┐
                   │  app/verifier.py   │  replay, same checks as the judge KABYA
                   └─────────┬──────────┘
                             │  valid plan + totals recomputed from the plan
                             ▼
                        200 JSON response

   harness/judge.py  ─── scores a live URL exactly like the organizers    FAYEK
```

## The one idea

**Human language is never trusted as math.** The model's job ends the moment it
emits a structured guess. Everything after `guardrails.py` is deterministic,
pure, and unit-testable, which is why 75 of the 100 points are reachable without
the model being perfect.

## Why these boundaries

- **The model output is untrusted data, not a function call.** `guardrails.py`
  is allowed to repair it and allowed to demote it to `no_op`, but never to
  invent a directive. That is the exact guardrail wording in §08 of the spec.
- **`ConstraintSet` is the waist of the hourglass.** The optimizer and the
  verifier both read it and nothing else. That means Kabya can build and test
  the entire optimisation half with hand-written `ConstraintSet`s, before Member
  B's first prompt exists.
- **The verifier is a copy of the judge, not a copy of the optimizer.** It must
  be written from the spec, independently — a verifier that shares code with the
  solver validates the bug as well as the plan.
- **Everything reachable without a network call stays reachable.** `/health`
  never touches the model. The pipeline has a hard budget and a
  `safe_baseline_plan` so a provider outage degrades cost, not validity.

## Failure ladder

| Failure | Response |
|---|---|
| Primary provider errors / times out | retry once, then the fallback provider |
| Every provider down | `fallback.rule_based_interpret`, `source="fallback"` |
| Model output unparseable | tolerant JSON extraction, then repair, then `no_op` |
| A directive is illegal after repair | that note becomes `no_op`, others survive |
| LP infeasible | drop directives one at a time (lowest confidence first) |
| LP still infeasible or verification fails | `safe_baseline_plan` |
| Anything unhandled | 500 with `{"error": "internal_error"}`, service stays up |

Every rung keeps the service answering a **valid** schedule. An invalid cheap
plan scores zero for both application and optimization on that case; a valid
expensive plan keeps 25 of the 35.
