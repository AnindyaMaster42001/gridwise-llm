# GridWise LLM — Smart Campus Energy Optimization

**BUP CSE Fest 2026 · Online Preliminary · LLM-Assisted Operator Directive Interpretation**

An HTTP service that reads short natural-language campus operator notes with a
language model, converts them into validated structured directives, and returns
a cost-minimal 24-hour electricity schedule that provably obeys every one of
them.

> **Status: scaffold.** The contracts, specs, optimizer formulation and the
> four-person build plan are complete; the module bodies are implemented during
> the round. Start at [`docs/team/`](docs/team/README.md).
>
> **Judges:** sections marked 🔧 are filled in at submission time.

---

## How it works

```
POST /optimize-energy
        │
        ▼
  operator notes ──► LLM interpretation ──► deterministic guardrails ──► linear program ──► replay verifier ──► JSON
                     (mandatory, untrusted)   (repair / validate)         (exact optimum)    (reject invalid)
```

Human language is never trusted as math. The model's job ends the moment it emits
a structured guess; everything after the guardrails is deterministic, pure and
unit-testable. If the model is slow, wrong or unavailable, the service degrades
through a documented failure ladder and still returns a **valid** schedule.

Full architecture: [`docs/01-architecture.md`](docs/01-architecture.md).

## API

### `GET /health`

```bash
curl -sS http://localhost:8000/health
# {"status":"ok"}
```

### `POST /optimize-energy`

```bash
curl -sS -X POST http://localhost:8000/optimize-energy \
  -H 'Content-Type: application/json' \
  -d @- <<'JSON'
{
  "scenario_id": "GRID-101",
  "operator_notes": [
    "Solar output will drop to about 20% from 1 PM to 3 PM.",
    "The cafeteria menu changes tomorrow."
  ],
  "hours": [ {"hour": 0, "demand_kwh": 180, "solar_kwh": 0, "tariff_bdt_per_kwh": 7} ],
  "battery": {
    "capacity_kwh": 500, "initial_energy_kwh": 200, "minimum_energy_kwh": 50,
    "max_charge_kwh_per_hour": 100, "max_discharge_kwh_per_hour": 100
  }
}
JSON
```

(`hours` must carry all 24 entries; trimmed here for readability.)

Returns `scenario_id`, one `directive_interpretation` entry per note in
`note_index` order, a 24-entry `hourly_plan`, `total_grid_kwh`,
`total_cost_bdt`, `peak_grid_kwh` and `plan_summary`. Full schema:
[`docs/00-spec-digest.md`](docs/00-spec-digest.md) §3–§4.

Status codes: `200` success · `400` malformed or structurally invalid request ·
`500` controlled internal error (never a stack trace, never a secret).

## Quickstart (local, from a clean machine)

```bash
git clone <REPO_URL> && cd gridwise-llm

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env          # then set LLM_API_KEY (see "Configuration")

uvicorn app.main:app --host 0.0.0.0 --port 8000

# in another terminal
curl -sS http://localhost:8000/health
bash scripts/smoke.sh http://localhost:8000
```

## Testing against the public sample pack

The ten public cases ship in `tests/data/public_samples.json`.

```bash
python3 -m pytest -q                                          # offline unit tests
python3 -m harness.judge --base-url http://localhost:8000     # score out of 100
```

`harness/judge.py` replays every returned schedule against the pack's expected
directives — the same thing the organizers' harness does — and prints a
per-category score in the official seven categories.

Expected result: all 10 cases valid, interpretation matching the expected
directives, and `total_cost_bdt` equal to the reference cost. The LP formulation
in [`docs/03-optimizer-formulation.md`](docs/03-optimizer-formulation.md)
reproduces the organizer's reference cost on all ten with a difference of
`+0.00`; the verified spike is at `docs/reference/lp_spike.py`.

## Configuration

Every value comes from an environment variable. **No secret is committed**;
`.env` is git-ignored and `.env.example` documents the names only.

| Variable | Required | Purpose |
|---|---|---|
| `LLM_PROVIDER` | yes | `openai` \| `groq` \| `gemini` \| `openai_compatible` |
| `LLM_MODEL` | yes | model identifier, e.g. `gpt-4o-mini` |
| `LLM_API_KEY` | yes | credential for the primary provider |
| `LLM_BASE_URL` | no | only for `openai_compatible` / self-hosted endpoints |
| `LLM_FALLBACK_PROVIDER` | no | second provider, used when the primary fails |
| `LLM_FALLBACK_MODEL` | no | model for the fallback provider |
| `LLM_FALLBACK_API_KEY` | no | credential for the fallback provider |
| `LLM_TIMEOUT_S` | no | per-call timeout, default `8` |
| `LLM_MAX_RETRIES` | no | retries before failover, default `1` |
| `LLM_TEMPERATURE` | no | default `0` — determinism matters here |
| `LLM_CACHE_SIZE` | no | interpretation cache entries, default `512` |
| `REQUEST_BUDGET_S` | no | whole-request wall, default `22` (judge fails at 30) |
| `PORT` | no | listen port, default `8000` |
| `LOG_LEVEL` | no | default `INFO` |
| `ALLOW_NO_LLM` | no | offline development only; **must be unset in production** |

**Model / provider used for submission:** 🔧 *`<model>` via `<provider>`*

**Where the LLM sits:** `app/llm/interpreter.py` sends all operator notes in one
JSON-mode completion and receives one structured directive per note. That output
is the only source of directives; `app/guardrails.py` then validates it and
`app/optimizer.py` consumes the result. The model is not used for cosmetic text.

## Docker

```bash
docker pull 🔧<registry>/gridwise-llm:🔧<tag>
docker run --rm -p 8000:8000 \
  -e LLM_PROVIDER=openai -e LLM_MODEL=gpt-4o-mini -e LLM_API_KEY=<your-key> \
  🔧<registry>/gridwise-llm:🔧<tag>

curl -sS http://localhost:8000/health   # {"status":"ok"}
```

Binds `0.0.0.0:8000`, exposes `8000`, carries a `HEALTHCHECK`, and contains **no
baked-in credentials** — every secret arrives at runtime via `-e`.

Build locally instead: `make docker-build && make docker-run`.

## Deployed endpoint

🔧 `https://<base-url>` — `GET /health` and `POST /optimize-energy`, no auth.

## Repository layout

```
app/
  main.py          FastAPI endpoints, status codes, controlled errors
  pipeline.py      orchestration and the request time budget
  schemas.py       frozen request/response/internal contracts
  config.py        environment-driven settings, no secrets in code
  llm/             prompts, provider client with failover, interpreter, regex backup
  guardrails.py    untrusted model output -> legal Directive + ConstraintSet
  optimizer.py     linear program (SciPy HiGHS) + safe baseline plan
  verifier.py      independent replay, the same checks the judge runs
harness/           local replica of the organizers' judge, paraphrase bank
tests/             offline unit + sample tests, public sample pack
docs/              spec digest, architecture, contracts, per-member build plan
scripts/           smoke test, sample runner
```

## Dependencies & credits

- [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) — HTTP layer
- [Pydantic v2](https://docs.pydantic.dev/) — schema validation
- [SciPy](https://scipy.org/) `linprog` with the [HiGHS](https://highs.dev/) solver — the LP
- [NumPy](https://numpy.org/) — constraint matrices
- [httpx](https://www.python-httpx.org/) — async provider client
- 🔧 LLM provider: *`<provider>`*
- AI coding assistants (Claude Code) were used during development, as permitted
  by the rulebook. Architecture, formulation and logic are the team's own.

## Known limitations

- The battery is modelled as lossless, matching the Problem Statement, which has
  no round-trip efficiency term. A real installation would need a MILP.
- Interpretation quality is bounded by the chosen model. If every configured
  provider is unreachable, a regex fallback keeps the service answering; it is
  marked `source="fallback"` in the pipeline and is less accurate on unusual
  paraphrases.
- Conflicting hard directives are degraded through a documented ladder rather
  than reported as an error, since organizer scoring scenarios are guaranteed
  feasible.
- The interpretation cache is in-process; it does not survive a restart and is
  not shared across workers.

## Secret handling

- `.env` and `*.key` are git-ignored; only `.env.example` (names, no values) is
  committed.
- No credential is baked into the Docker image — verified with
  `docker history --no-trunc <image>`.
- Error responses carry `{"error": "..."}` only; tracebacks stay server-side.
- Provider errors are logged as a status code and a truncated body, never with
  the request headers.

## Team

| Member | Lane | Responsible for |
|---|---|---|
| **Anindya Kundu** | A | API surface, pipeline orchestration, integration |
| **Muhaiminul Islam Ninad** | B | LLM operator-note interpretation, prompts, provider failover |
| **Kabya Mithun Saha** | C | Deterministic guardrails, linear-program optimizer, replay verifier |
| **Fayek Ahmed** | D | Judge harness, tests, deployment, Docker image, documentation |

Individual build plans: [`docs/team/`](docs/team/README.md).
