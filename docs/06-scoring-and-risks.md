# Scoring model, and where the points actually leak

## The 100 points

| # | Category | Pts | Breakdown |
|---|---|---|---|
| 1 | LLM Directive Interpretation | **25** | 5 relevance/`no_op` · 5 type · 5 hours · 5 numeric + shape · 5 paraphrase robustness |
| 2 | Directive Application & Constraint Correctness | **25** | 10 ground-truth application · 5 balance + effective solar · 5 battery transitions/bounds/rates · 5 action consistency + neutrality + non-negativity |
| 3 | Optimization Quality | **10** | `10 × mean(min(1, optimal / ours))` over valid cases |
| 4 | API Contract & Schema | **10** | 2 endpoints/status · 2 request validation · 3 interpretation schema/order · 3 plan + top-level schema + `scenario_id` echo |
| 5 | Performance & Reliability | **10** | 2 health · 3 p95 latency · 3 stability · 2 malformed handling + secret safety |
| 6 | Deployment & Docker Fallback | **10** | 3 live endpoint · 4 pullable image reaching `/health` · 2 clean startup · 1 no judge debugging |
| 7 | Documentation & Local Reproducibility | **10** | 3 clean quickstart · 2 env/model docs · 2 sample-test procedure · 1 architecture · 1 Docker instructions · 1 deps/limits/secrets |

p95 latency: `≤5 s` → 3/3 · `>5–15 s` → 2/3 · `>15–30 s` → 1/3 · `>30 s` → 0 and
the request counts as a failure.

## The shape of this scoreboard

**50 of 100 points are interpretation and application.** Another 30 (categories
4–7) are pure engineering hygiene that does not depend on solving anything
cleverly — a correct schema, a live URL, a pullable image, a README a stranger
can follow. Only 10 points are the optimisation itself, and the LP already
reaches the organizer's optimum on every public case.

The implication for a four-hour round: **finish the boring 30 early.** A team
with a perfect optimizer and no Docker image loses more than a team with a
baseline optimizer and a clean deployment.

## Instant-loss conditions

| Condition | Consequence |
|---|---|
| LLM absent from the note path, or used only for `plan_summary` | fails the mandatory requirement, **not eligible for the shortlist** |
| Ground-truth directive not reflected in `hourly_plan` | that case is invalid: no application credit, no optimization credit |
| Energy-balance failure, battery bound/rate/transition violation, solar overuse | case invalid |
| End-of-day battery ≠ initial | case invalid |
| Reported totals disagree with `hourly_plan` | deduction; repeated invalidity can block qualification |
| Secrets in repo, logs, responses, or image | penalised outright |

Note the asymmetry in row 2: the judge replays with **its** directives, not ours.
A note we read wrong fails twice — once in category 1, again in category 2.

## Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Off-by-one on end-exclusive windows | **high** | ~10 pts | prompt examples + `normalise_hours` tests + paraphrase bank |
| `factor` inverted (0.8 vs 0.2) | **high** | ~5 pts | explicit prompt rule, both-direction unit tests |
| Percentage reserve not resolved against capacity | medium | ~5 pts | capacity is in every user prompt; guardrail clamps, never auto-converts |
| Provider outage mid-judging | medium | up to 25 pts | two providers + regex fallback + cache |
| Free-tier host cold start > 60 s | medium | up to 10 pts | always-on host, keepalive ping, Docker fallback |
| Rounding breaks end-of-day neutrality | medium | whole cases | snap + force `plan[23]` to `initial_energy_kwh` |
| p95 over 5 s from a slow model | medium | 1–3 pts | fast model, single call, cache, temperature 0 |
| Merge conflicts at hour 3 | medium | hours | strict file ownership (`docs/02-contracts.md`) |
| Repo public too early | low | disqualification risk | private until the deadline, then public |

## Tie-breakers, in order

1. the 3-minute video · 2. directive application · 3. interpretation ·
4. optimization · 5. API/schema validity · 6. reliability/deployment ·
7. documentation · 8. exceptional engineering (guardrails, fallbacks, caching,
testing).

The video carries **no base points** and is reviewed only to break a tie — but a
tie at the qualification boundary is exactly where a preliminary round is
decided, so it is worth the 20 minutes it takes.
