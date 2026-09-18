# The four-person plan

Four hours, four lanes, one service. Each lane is a **separate set of files** so
nobody blocks anybody and nobody merge-conflicts.

| Lane | Owner | Files | Points controlled |
|---|---|---|---|
| [A — API & orchestration](MEMBER-A-api-orchestration.md) | | `app/main.py`, `app/pipeline.py` | 10 schema + most of 10 reliability |
| [B — LLM interpretation](MEMBER-B-llm-interpretation.md) | | `app/llm/*` | **25** interpretation + gates 10 in application |
| [C — Guardrails & optimizer](MEMBER-C-guardrails-optimizer.md) | | `app/guardrails.py`, `app/optimizer.py`, `app/verifier.py` | 20 application + **10** optimization |
| [D — Harness, deploy & docs](MEMBER-D-testing-deploy-docs.md) | | `harness/*`, `tests/*`, `Dockerfile`, `README.md` | **30** deployment + docs + reliability |

Write your names into that table first thing.

## How to pick

- **B** goes to whoever is most comfortable with prompting and API clients. It is
  the highest-variance lane and the one with a hard external dependency.
- **C** goes to whoever is most comfortable with linear algebra / optimisation.
  The formulation is already verified, so this lane is mostly careful
  implementation — and it is the only lane that is never blocked by anyone.
- **A** goes to whoever integrates well and stays calm; they own `main` being
  green from T+1:30.
- **D** goes to whoever ships. Thirty points, none of them dependent on the other
  three finishing. Do not treat this as the "leftover" lane — it is the biggest.

## Working agreement

- Branch per lane: `feat/a-api`, `feat/b-llm`, `feat/c-solver`, `feat/d-harness`.
- Push often. PR into `main`. Never force-push `main`.
- Need something from another lane that does not exist yet? Stub it **in your own
  file** and delete the stub at integration. Do not edit their file.
- `app/schemas.py` and `app/config.py` are frozen. A genuine need to change them
  is a team decision, announced, made once.
- If `main` is red, that is the only thing anyone works on until it is green.

## The three sync points

**T+1:30 — first integration.** Merge everything, deploy it, make a real sample
work end to end however imperfectly. Something submittable exists from here on.

**T+2:45 — score ourselves.** Run the harness against the live URL, read the
seven category scores aloud, and spend the next hour on the **lowest** one.

**T+3:30 — feature freeze.** Fixes only. D records the video. Run the submission
checklist top to bottom.

Full wall-clock schedule: [`docs/08-timeline.md`](../08-timeline.md).

## Reading order for everyone

1. [`docs/00-spec-digest.md`](../00-spec-digest.md) — the rules, condensed. Read it all.
2. [`docs/01-architecture.md`](../01-architecture.md) — how the pieces fit.
3. [`docs/02-contracts.md`](../02-contracts.md) — the interfaces you must not break.
4. Your own brief above.
5. [`docs/06-scoring-and-risks.md`](../06-scoring-and-risks.md) — where points leak.

## If you are using Claude Code

Every brief ends with an **Agent prompt** — paste it verbatim as your first
message. The repo's `CLAUDE.md` loads the project rules automatically, so the
agent starts with the spec, the contracts and your file boundaries already in
context.
