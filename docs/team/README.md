# The four-person plan

Four hours, four lanes, one service. Each lane is a **separate set of files** so
nobody blocks anybody and nobody merge-conflicts.

| Lane | Owner | Files they own | Points they control |
|---|---|---|---|
| [A — API & orchestration](ANINDYA-lane-a-api-orchestration.md) | **Anindya Kundu** | `app/main.py`, `app/pipeline.py` | 10 schema + most of 10 reliability |
| [B — LLM interpretation](NINAD-lane-b-llm-interpretation.md) | **Muhaiminul Islam Ninad** | `app/llm/*` | **25** interpretation + gates 10 in application |
| [C — Guardrails & optimizer](KABYA-lane-c-guardrails-optimizer.md) | **Kabya Mithun Saha** | `app/guardrails.py`, `app/optimizer.py`, `app/verifier.py` | 20 application + **10** optimization |
| [D — Harness, deploy & docs](FAYEK-lane-d-testing-deploy-docs.md) | **Fayek Ahmed** | `harness/*`, `tests/*`, `Dockerfile`, `README.md` | **30** deployment + docs + reliability |

Open your own brief above. It has your tasks, the traps specific to your lane,
your definition of done, and a prompt to paste into your Claude Code agent.

## Why the lanes fell this way

- **Anindya — A.** Set the repo up and already has the whole specification in
  context, which is exactly what the integrator needs. From T+1:30 they own
  `main` being green and decide what gets stubbed when a lane runs late.
- **Ninad — B.** The highest-variance lane and the only one with a hard external
  dependency: a provider key, a quota, and a model that has to read unseen
  paraphrases correctly. It is also the largest single block of points (25).
- **Kabya — C.** The most completely specified lane in the repo. The LP
  formulation is already written down and verified against all ten public cases
  (`docs/reference/lp_spike.py` matches the organizer's cost at +0.00 on every
  one), so this is careful implementation rather than invention — and it is the
  only lane that is never blocked waiting on anyone else.
- **Fayek — D.** Thirty points: the live URL, the Docker image, the README and
  the judge harness. None of it depends on the other three finishing, so it can
  start at 7:00 PM and be largely done by 8:30. This is **not** the leftover
  lane; it is the biggest one on the board.

**Swapping is fine, but do it now, not at 9 PM.** The one thing worth swapping
for: if someone other than Ninad is clearly stronger at prompting and HTTP
clients, trade B; if someone other than Kabya is clearly stronger at linear
algebra, trade C. Changing an assignment costs two edits (this table and the
header of each brief). Changing it mid-round costs an hour.

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
