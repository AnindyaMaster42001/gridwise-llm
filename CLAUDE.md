# GridWise LLM — project context

BUP CSE Fest 2026, online preliminary. One HTTP service that reads 1–3
natural-language campus operator notes with an LLM, converts them into validated
structured directives, and returns a cost-optimal, fully valid 24-hour energy
schedule that obeys them.

## Read these before writing code

| File | What it gives you |
|---|---|
| `docs/00-spec-digest.md` | the complete rules, condensed from the organizers' PDFs |
| `docs/01-architecture.md` | how the four modules fit together |
| `docs/02-contracts.md` | the interfaces and **who owns which file** |
| `docs/03-optimizer-formulation.md` | the LP, verified against all 10 public samples |
| `docs/04-guardrails.md` | untrusted model output → legal directive |
| `docs/05-llm-interpretation.md` | prompt rules and provider strategy |
| `docs/06-scoring-and-risks.md` | the 100-point rubric and where points leak |
| `docs/07-deployment.md` | hosting, Docker, secret hygiene |
| `docs/team/` | the four individual briefs |

The organizers' PDFs are canonical. `docs/00-spec-digest.md` is faithful to them,
but if you find a disagreement, the PDF wins and the digest gets fixed.

## Non-negotiable rules

1. **The LLM must interpret the operator notes.** A language model has to
   directly produce the structured interpretation that reaches the optimizer.
   Regex logic may repair or back it up; it may never be the only interpreter.
   Using a model only for `plan_summary` fails the challenge requirement.
2. **Model output is untrusted data.** It passes through `app/guardrails.py`
   before anything else sees it. Guardrails may repair or demote to `no_op`;
   they may never invent a directive.
3. **Valid beats cheap.** An invalid case loses its directive-application credit
   *and* its optimization credit. Never return a schedule that
   `app/verifier.py` rejects — return the safe baseline instead.
4. **Totals come from `hourly_plan`.** `pipeline.summarise_totals` recomputes
   them from the emitted plan; nothing reports solver internals.
5. **Windows are start-inclusive, end-exclusive.** "1 PM to 3 PM" → `[13, 14]`.
6. **`factor` is the fraction that remains.** "80% reduction" → `0.2`.
7. **`hours` are unique ints 0–23, ascending**, even when the window wraps
   midnight: 10 PM–2 AM → `[0, 1, 22, 23]`.
8. **Exactly one interpretation entry per note**, in `note_index` order.
   `applies == (directive_type != "no_op")`, and `structured_adjustment is None`
   iff `no_op`.
9. **No secrets anywhere** — repo, logs, responses, or image. Config comes from
   environment variables; `.env` is git-ignored.
10. **Tolerance is 0.01** absolute, kWh and BDT.

## File ownership

Four people work in parallel. Stay in your lane.

| Files | Owner |
|---|---|
| `app/main.py`, `app/pipeline.py` | A |
| `app/llm/*` | B |
| `app/guardrails.py`, `app/optimizer.py`, `app/verifier.py` | C |
| `harness/*`, `tests/*`, `Dockerfile`, `README.md` | D |
| `app/schemas.py`, `app/config.py` | **frozen** — team decision only |

Need a function from another lane that does not exist yet? Write a stub **in your
own file**, mark it `# STUB — remove at integration`, and move on.

## Conventions

- Python 3.11, FastAPI, Pydantic v2, SciPy HiGHS for the LP.
- `async def` for anything touching the network; the solver runs in a thread.
- Type hints everywhere. Pure functions in `guardrails`, `optimizer`, `verifier`
  — those three make no network calls and must be testable without a key.
- Tests live in `tests/`, offline by default (`ALLOW_NO_LLM=1`).

## Commands

```bash
make install                                      # deps
make dev                                          # uvicorn --reload on :8000
make test                                         # pytest
bash scripts/smoke.sh http://localhost:8000       # health + sample + malformed
python3 -m harness.judge --base-url <url>         # full local score out of 100
make docker-build && make docker-run              # container path
```

## Before you say something works

Run it. `docs/reference/lp_spike.py` is the standard: it is in the repo because
it was executed against all ten public cases and matched the organizer's cost to
`+0.00` on every one. Claims about correctness in this project come with output.
