# Lane D — judge harness, deployment, documentation, video

**Owner: Fayek Ahmed**

**You own:** `harness/judge.py`, `tests/*`, `Dockerfile`,
`docker-compose.yml`, `README.md`, deploy config, the video
**You must not edit:** `app/*` — if you find a bug there, report it to its owner
(`docs/02-contracts.md` has the map: `main.py`/`pipeline.py` are Anindya's,
`llm/*` is Ninad's, the solver trio is Kabya's). The one exception: you may edit
`harness/paraphrase_bank.json` jointly with Ninad.

**Points you directly control:** all 10 of *Deployment & Docker Fallback*, all 10
of *Documentation & Local Reproducibility*, and most of *Performance &
Reliability*. That is **30 of 100 — the largest single lane on this team**, and
none of it depends on anyone else finishing.

**Read first:** `docs/07-deployment.md`, `docs/06-scoring-and-risks.md`.

---

## Do this in the first fifteen minutes

Deploy the skeleton. `/health` already works — `app/main.py` serves it today,
with `/optimize-energy` still unimplemented.

```bash
docker build -t <user>/gridwise-llm:v0 .
docker run --rm -p 8000:8000 <user>/gridwise-llm:v0
curl localhost:8000/health        # {"status":"ok"}
```

Then push it to the host and get a public URL. Every later deploy is a redeploy
of something already proven reachable, instead of a first-ever deploy at 10:45 PM
with an unfamiliar platform. **This is the single highest-value fifteen minutes
anyone on this team spends tonight.**

Host guidance is in `docs/07-deployment.md` — an always-on VPS beats every free
tier, because Render-style sleep plus a ~50 s cold start runs straight into the
judge's 60 s health budget.

---

## Task D1 — the local judge (~60 min, your main build)

`harness/judge.py` scores a live URL in the organizers' seven categories, so the
team sees the scoreboard before the organizers do.

```bash
python3 -m harness.judge --base-url https://our-service --repeat 3
```

Per case, from `tests/data/public_samples.json`:

- **Interpretation (25)** — one entry per note, `note_index` order, `applies`
  semantics, `directive_type`, hours as a set comparison, numbers within 0.01.
  Ignore `explanation` wording; it is not matched byte-for-byte.
- **Application + validity (25)** — replay `hourly_plan` against the case's
  **expected** directives, not against what the service reported. That is exactly
  what the organizers do, and it is the difference that catches
  "extracted right, scheduled wrong".
- **Optimization (10)** — `min(1, reference_cost / our_recalculated_cost)`,
  averaged over valid cases only.
- **Schema (10)** — required fields, types, `scenario_id` echo, 24 hours.
- **Performance (10)** — p95 latency over `--repeat`, failure rate, `/health`
  readiness, plus a malformed-JSON probe that must return 400 and leave the
  service alive.

Import `app.verifier.verify` for the replay rather than writing a second copy —
if the harness and the service disagree about validity, you will spend an hour
finding out which one is wrong.

Print a per-category table and a total out of 100. `--json` for machine-readable
output.

## Task D2 — tests (~30 min)

- `tests/test_samples.py` — all 10 cases through the app in-process
  (`httpx.ASGITransport`, no network), asserting schema + validity.
- `tests/test_guardrails.py` — the table in `docs/04-guardrails.md`. Write these
  even though Kabya owns the module — tests are yours, and they will thank you.
- `tests/test_paraphrase.py` — drive `harness/paraphrase_bank.json`. Mark it
  `skipif` when no key is configured so CI stays offline and green.

CI (`.github/workflows/ci.yml`) already runs `pytest` with `ALLOW_NO_LLM=1`.

## Task D3 — the README (~40 min, 10 points, write it early)

`README.md` in the repo root is the deliverable, and it is scored against fixed
criteria. It must let an organizer who has never met us run the service from a
clean machine with **no undocumented steps**:

1. What this is, in three sentences.
2. Architecture: LLM → deterministic guardrails → optimizer, with the ASCII
   diagram from `docs/01-architecture.md`.
3. **Model and provider named explicitly** (e.g. `gpt-4o-mini` via OpenAI), and
   exactly where the LLM sits in the interpretation path.
4. Every environment variable **by name**, with a description — and **no values**.
5. Local quickstart, copy-pasteable, from `git clone` to a successful
   `/optimize-energy` call.
6. `curl` examples for `/health` and `/optimize-energy`, with real output.
7. The public-sample test command and what "passing" looks like.
8. Docker: exact `pull` and `run` commands with the exact tag or digest.
9. Dependencies and credits — FastAPI, SciPy/HiGHS, the model provider, and the
   AI coding assistants used. The Guide requires crediting external tools.
10. Known limitations, honestly stated.
11. Secret handling: nothing committed, `.env` git-ignored, no keys in the image.

**Then have someone who did not write it follow it verbatim, in a fresh
directory.** Every step they have to ask about is a point lost in category 7.

## Task D4 — deployment hardening (~30 min)

From `docs/07-deployment.md`:

- binds `0.0.0.0`, documented port exposed, `HEALTHCHECK` passes;
- **no baked-in secrets** — verify before pushing:
  ```bash
  docker history --no-trunc <image> | grep -iE 'key|token|secret'
  git log -p | grep -iE 'api[_-]?key|sk-|bearer'
  ```
- image pushed with an exact tag, digest recorded, stays pullable all evening;
- keepalive pinging `/health` every 60 s so nothing cold-starts under the judge;
- previous known-good tag kept pullable for a 30-second rollback.

## Task D5 — the 3-minute video (~20 min at T+3:00)

No base points — reviewed only to break a tie. Ties happen at the qualification
boundary, which is exactly where this round is decided.

Structure, and keep it under 3:00: problem in 20 s · architecture diagram and the
LLM → guardrails → optimizer flow in 60 s · a real request/response and the
guardrail repairing bad model output in 60 s · how to run and test it in 30 s.
Screen recording is fine; production polish is not scored.

---

## Definition of done

- [ ] public URL answers `/health` from outside our network (test on mobile data)
- [ ] `python3 -m harness.judge --base-url <live>` prints all seven categories
- [ ] all 10 public samples pass against the live URL
- [ ] malformed JSON → 400, service alive afterwards
- [ ] p95 under 5 s over 3 repeats
- [ ] image pulls and starts from the documented tag on a machine that never built it
- [ ] `docker history` and `git log -p` are clean of secrets
- [ ] README followed verbatim by a teammate who did not write it
- [ ] video accessible, ≤ 3:00
- [ ] submission form: URL, repo, image tag/digest, video link

## Agent prompt

Paste this verbatim as the first message to your Claude Code agent.

> You are working as **Fayek Ahmed**, owner of Lane D (judge harness,
> deployment and documentation) on a four-person hackathon team.
>
> Read `docs/00-spec-digest.md`, `docs/06-scoring-and-risks.md`,
> `docs/07-deployment.md` and `docs/team/FAYEK-lane-d-testing-deploy-docs.md` in this
> repo. Implement `harness/judge.py` to score a live base URL in the organizers'
> seven categories, and write `tests/test_samples.py` and
> `tests/test_guardrails.py`. Only edit files under `harness/` and `tests/`, plus
> `README.md` and `Dockerfile`. Do not edit anything under `app/` — report bugs
> there instead. Reuse `app.verifier.verify` for schedule replay. Then run the
> harness against `http://localhost:8000` and report the per-category score.
