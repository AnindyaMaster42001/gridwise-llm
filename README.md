# GridWise LLM

GridWise interprets campus operator notes and schedules grid, solar, and battery
energy over 24 hours. A language model extracts directives; deterministic
guardrails and an optimizer enforce them. The BUP CSE Fest 2026 preliminary
requires a working public API, reproducible container, source, and a video of at
most three minutes.

**Current state:** Lane A is integrated. Lane D provides the judge, tests,
deployment configuration, and documentation. Lane B/C production functions
remain unfinished in this checkout; a successful `/health` or HTTP 200 does
not establish that the solution meets the challenge. See
[integration findings](deploy/INTEGRATION.md) and run the release checks below.
No public deployment, registry digest, or submitted video is claimed yet.

## Architecture and ownership

```text
POST /optimize-energy
  -> operator notes -> Gemini (OpenRouter model fallback)
  -> untrusted structured interpretation
  -> deterministic guardrails -> hourly constraints
  -> SciPy/HiGHS linear program -> replay verifier
  -> interpretation + 24-hour plan + recomputed totals
```

The required LLM step is `app/llm/interpreter.py:interpret_notes`; its output
feeds `app/guardrails.py`, then `app/optimizer.py`. Using a model solely for
`plan_summary` does not qualify. `app/verifier.py` checks the emitted plan;
the local judge additionally audits it against the **case pack's expected
directives**, independently of what the service says it interpreted.

| Owner | Files / responsibility |
|---|---|
| Anindya Kundu | `app/main.py`, `app/pipeline.py`: HTTP and orchestration |
| Muhaiminul Islam Ninad | `app/llm/*`: actual LLM calls, interpretation, failover |
| Kabya Mithun Saha | guardrails, optimizer, verifier |
| Fayek Ahmed | `harness/*`, `tests/*`, Docker, deployment, README, demo |

`app/schemas.py` and `app/config.py` are shared frozen contracts. Lane D does
not implement production code in another member's files.

## Model configuration

The selected primary is **Google Gemini `gemini-2.5-flash`**. The fallback
is **OpenRouter `openrouter/free`**, through the existing `openai_compatible`
provider setting and `https://openrouter.ai/api/v1` base URL. These are configured
in [.env.example](.env.example) and [the deployment template](deploy/runtime.env.example).
Actual provider calls remain Lane B's integration responsibility.

`gemini-2.5-flash` was selected by measurement against the ten public cases, not by
default: it answered 4/4 probes correctly at a 2.86 s median, where both `-flash-lite`
variants returned `[13, 14, 15]` for a 1 PM-3 PM window and so failed the
end-exclusive rule outright, and the newer `gemini-3.x-flash` models were either
shedding load with HTTP 503 or running past 15 s.

> **Provider quota is a release blocker, not a detail.** A free-tier Gemini key is
> capped at **20 requests per day, per model** (`quotaId
> GenerateRequestsPerDayPerProjectPerModel-FreeTier`). That cannot serve a judged
> round: once it is spent every request silently falls back to the deterministic
> backup, which does not satisfy the mandatory-LLM requirement. Enable billing on
> the Google Cloud project, or configure a second independent provider key, before
> submitting. The quota is counted per model, so a fallback pointed at a *different*
> model has its own allowance and buys some headroom, but it is a stopgap and not a
> substitute for billing. `/health` does not detect an exhausted quota.

Google documents the [Gemini model identifier](https://ai.google.dev/gemini-api/docs/models).
OpenRouter documents its [free model router](https://openrouter.ai/openrouter/free)
and [compatible API base URL](https://openrouter.ai/developers).
The free router can choose different underlying models, so it is not a pinned
model and cannot promise repeatable output or availability. Verify both providers
with your accounts and quota before submission; a free fallback still needs an
OpenRouter key. Record the actual responding model in internal, redacted telemetry.

Set keys only in `.env` or your host's secret settings. Never put them in source,
commands shown in the video, Git, or the Docker image. `ALLOW_NO_LLM=0` is required
for the submitted service. The example selects zero per-provider retries so an
8-second primary attempt plus an 8-second fallback leaves room in the 22-second
request budget; measure this after Lane B integrates.

| Environment variable | Purpose |
|---|---|
| `LLM_PROVIDER` | Primary provider adapter, selected `gemini` |
| `LLM_MODEL` | Primary model identifier |
| `LLM_API_KEY` | Primary credential; required for submission |
| `LLM_BASE_URL` | Optional primary compatible endpoint override |
| `LLM_FALLBACK_PROVIDER` | Fallback adapter, selected `openai_compatible` |
| `LLM_FALLBACK_MODEL` | Fallback model or router identifier |
| `LLM_FALLBACK_BASE_URL` | OpenRouter compatible API base URL |
| `LLM_FALLBACK_API_KEY` | Fallback credential |
| `LLM_TIMEOUT_S` | Per-provider timeout in seconds |
| `LLM_MAX_RETRIES` | Retry count before failover |
| `LLM_TEMPERATURE` | Sampling temperature |
| `LLM_CACHE_SIZE` | In-process interpretation cache capacity |
| `REQUEST_BUDGET_S` | Pipeline request budget; official deadline is 30 seconds |
| `PORT` | Container listening port; local CLI also supplies `--port` |
| `LOG_LEVEL` | Runtime logging level |
| `ALLOW_NO_LLM` | Offline development only; never enable for submission |
| `RUN_LIVE_LLM` | Test-only opt-in to calls that consume model quota |

## Local setup (Python 3.11)

The repository must be private during the event and public after the submission
deadline, following the official guide. Access during the private period requires
your team's GitHub credentials.

Windows PowerShell:

```powershell
git clone https://github.com/AnindyaMaster42001/gridwise-llm.git
cd gridwise-llm
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
Copy-Item deploy/runtime.env.example .env
# Edit .env locally: fill LLM_API_KEY and LLM_FALLBACK_API_KEY.
.\.venv\Scripts\python -m uvicorn app.main:app --env-file .env --host 0.0.0.0 --port 8000
```

Linux/macOS:

```bash
git clone https://github.com/AnindyaMaster42001/gridwise-llm.git
cd gridwise-llm
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp deploy/runtime.env.example .env
# Edit .env locally: fill both credentials.
python -m uvicorn app.main:app --env-file .env --host 0.0.0.0 --port 8000
```

`--env-file .env` is intentional: the application configuration reads environment
variables when imported; merely creating `.env` does not load it.

In a second terminal in the repository, select the same virtual environment.
On Windows use `.\.venv\Scripts\python` in place of `python`, and `curl.exe` in
place of `curl` (PowerShell may alias `curl` to another command):

```bash
curl --fail-with-body http://localhost:8000/health
python -m harness.export_sample
curl --fail-with-body -X POST http://localhost:8000/optimize-energy -H "Content-Type: application/json" --data-binary @harness/reports/request.json --output harness/reports/response.json
python -m json.tool harness/reports/response.json
```

Health returns `{"status":"ok"}`. The exporter writes a **complete 24-hour
request**, plus `harness/reports/reference.json` from the organizer pack. For
`SAMPLE-01`, the organizer reference has `total_grid_kwh=2692.5`,
`total_cost_bdt=38365`, and `peak_grid_kwh=175`. These are reference values, not
claims about the unfinished server. Equivalent optimal schedules may have a
different peak/grid total; the objective and all constraints must pass replay.

The current scaffold answers optimization requests through Lane A's fallback
shims. A successful, directive-correct public-sample call requires Lane B/C to
be merged; the judge identifies this rather than hiding it.

## API contract

`GET /health` returns HTTP 200 and a JSON object with `status="ok"`.
`POST /optimize-energy` accepts `scenario_id`, 1–3 non-empty `operator_notes`,
24 unique `hours` numbered 0–23, and `battery` parameters. Use the exported
request above for a runnable full example.

Responses contain `scenario_id`, one `directive_interpretation` per note in
index order, `hourly_plan`, `total_grid_kwh`, `total_cost_bdt`, `peak_grid_kwh`,
and `plan_summary`. Supported directive types are `solar_reduction`,
`minimum_battery_reserve`, `no_charge_window`, `no_discharge_window`,
`max_grid_window`, and `no_op`. See [the schema digest](docs/00-spec-digest.md)
and the canonical PDFs.

Malformed JSON/structural errors must return 400. Semantic errors may return
422. Internal errors must be controlled, with no secrets or stack traces in
responses. Windows use an inclusive start and exclusive end, solar factors are
the fraction **remaining**, numeric tolerance is **absolute 0.01**, and the
battery must end at its initial energy.

## Tests and scoring

```bash
python -m pytest -q
python -m pytest --integration -q
python -m harness.judge --base-url http://localhost:8000 --repeat 3 --output harness/reports/local.json
python -m harness.judge --base-url http://localhost:8000 --json
python -m harness.stress_cases
python -m harness.judge --base-url http://localhost:8000 --cases harness/reports/stress_cases.json
python -m harness.preflight --history
python -m harness.provider_probe
```

Default tests are offline. An exact function-body `NotImplementedError` stub
causes a visibly explained skip in dependent tests; `--integration` turns those
into failures. This prevents a green scaffold run from being called a completed
release. Public ASGI tests inject ground-truth **interpreter fixtures** only;
they exercise the actual solver and verifier once implemented. They do not prove
that a live model understands notes.

`harness.provider_probe` sends one synthetic note directly to each configured
provider and reports status, model, latency, and semantic checks without raw
credentials or provider error bodies. It consumes quota and establishes only
provider connectivity/basic JSON behavior, not production integration.

For live semantic evaluation, after implementing Lane B/C and configuring keys:

```bash
python -m dotenv -f .env run -- python -m pytest tests/test_paraphrase.py -q
```

Set `RUN_LIVE_LLM=1` and `ALLOW_NO_LLM=0` in your local `.env` for that command,
then remove the test-only flag. The bank includes 12 phrasings for each of the
six types (72 total), with midnight wrap, percentage reserve, remainder/reduction,
and energy-related distractors. Live tests reject directives labeled as fallback.

Passing a release means all 10 public cases pass schema, interpretation, expected
directive replay, totals, and optimum-cost checks; all shared-verifier checks pass;
malformed input returns 400; and repeated real-model p95 latency is at most
5 seconds for full latency marks. A fast stub or warmed cache does not establish
cold-model performance.

The judge prints all seven rubric categories. It labels the score as a local
estimate: paraphrase points, startup timing, provider/secret safety, deployment,
and fresh-machine reproduction require separate evidence. Artifact categories
are `UNASSESSED`, never free points. It exits nonzero on failed integration and
supports JSON reports for CI. Optimization averages over **every** case, with
invalid cases contributing zero; if both costs are within 0.01 of zero, quality
is one. This follows Guide §08, correcting the older team notes that incorrectly
said to average only over valid cases.

The manually triggered GitHub Actions `release-check` workflow runs
`pytest --integration` before building and checking the container. It is
expected to fail until the documented integration blockers are fixed.

## Docker fallback

Docker Desktop must use Linux containers on Windows. Build and run locally:

```bash
docker build -t gridwise-llm:lane-d .
python -m harness.container_check --image gridwise-llm:lane-d
docker run --detach --name gridwise-local --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m --cap-drop ALL --security-opt no-new-privileges:true -p 8000:8000 --env-file .env gridwise-llm:lane-d
curl --fail-with-body http://localhost:8000/health
docker inspect --format='{{.State.Health.Status}}' gridwise-local
python -m harness.judge --base-url http://localhost:8000 --repeat 3
docker stop gridwise-local
docker rm gridwise-local
```

Stop the local Uvicorn server first if it uses port 8000. Alternatively,
`docker compose up --build -d` runs the same image with a read-only filesystem,
limited capabilities, and restart policy. `docker compose down` stops it.
The image runs as UID 10001, binds `0.0.0.0`, exposes port 8000, checks the health
JSON, and uses `exec` so shutdown reaches Uvicorn. The Docker build context is
allowlisted to application code, requirements, and optional dependency wheels;
credentials, Git, PDFs, tests, and local reports are excluded.

If Docker's network cannot download dependencies reliably, prepare Linux x86-64
wheels through the host connection, then run the same build again:

```bash
python -m pip download -r requirements.txt uvloop==0.22.1 --dest deploy/wheels --platform manylinux2014_x86_64 --python-version 311 --implementation cp --abi cp311 --only-binary=:all:
docker build -t gridwise-llm:lane-d .
```

BuildKit mounts this ignored wheel directory only during installation; wheels
are not copied into image layers. An empty wheel directory uses normal PyPI
installation. A populated directory requires a complete wheel set and installs
offline, failing clearly if anything is missing. The command above targets
Linux AMD64; download matching wheels for another architecture. Base-image
availability is still required.

**Published pull reference: pending registry access.** `gridwise-llm:lane-d` is a
local tag, not a published image. Local container checks passed (health ready in
1.781 seconds, non-root/read-only execution, optimization response schema,
healthy Docker probe, and clean shutdown); the sample plan still fails ground
truth because production modules are unfinished. To publish, choose your registry namespace,
tag the verified image with the commit ID, push it, and record the resulting
`registry/name@sha256:...` reference in `deploy/submission.json`. The publisher
workflow is in [the deployment runbook](deploy/RUNBOOK.md).

After the actual digest is recorded, the organizer's exact commands are:

```bash
docker pull "$GRIDWISE_IMAGE"
docker run --rm --env-file .env -p 8000:8000 "$GRIDWISE_IMAGE"
```

Set `GRIDWISE_IMAGE` to the submitted immutable reference. This variable is a
deployment command input, not application configuration. A clean-machine pull
test and an external network check remain required before these instructions
can be claimed verified against a registry.

## Deployment

The submitted service runs on **Vercel**. Vercel detects the project as
`framework: fastapi` and serves `app/main.py` directly at the root, so there is
no adapter file, no rewrite and no host-specific code path — the judge
exercises the same application as the container and the test suite.
`vercel.json` raises `maxDuration` to 60 s on that entrypoint, because the
Hobby default of 10 s would time out a request whose measured p95 is 6.6 s.

Full procedure, including the environment variables, the outside-network check
and rollback: **[deploy/VERCEL.md](deploy/VERCEL.md)**.

```bash
BASE=https://<deployment>.vercel.app
curl -sS "$BASE/health"                                  # {"status":"ok"}
python -m harness.judge --base-url "$BASE" --repeat 3     # full local score
```

The container in [deploy/RUNBOOK.md](deploy/RUNBOOK.md) is the fallback
execution path and is not serverless: no duration cap, no cold starts, and an
interpretation cache that survives between requests.

## Video

[The 2:50 demo script](deploy/VIDEO_SCRIPT.md) and
[the recording guide](deploy/VIDEO_GUIDE.md) prepare the tie-break video.
The video carries no base points and is reviewed only when teams finish on the
same total score — which is exactly what happens at a qualification boundary.
Tie-break order after it: application, interpretation, optimization, schema,
reliability and deployment, documentation, exceptional engineering.

## Dependencies, credits, and limits

Python 3.11; FastAPI 0.115.6; Uvicorn 0.34.0; Pydantic 2.10.4; SciPy 1.15.0 with
HiGHS; NumPy 2.2.1; httpx 0.28.1; python-dotenv 1.0.1; pytest 8.3.4 and
pytest-asyncio 0.25.0. Direct dependencies are pinned in the requirements files.
Provider APIs: Google Gemini and OpenRouter. AI coding assistance: Claude Code
for the original scaffold and OpenAI Codex for Lane D implementation and review.
The public sample pack and challenge PDFs are supplied by BUP CSE Fest 2026.

The battery is lossless because the challenge specifies no efficiency term.
Overlapping solar factors multiply per the team's documented convention;
multiple grid caps use the minimum and reserves the maximum. These stress cases
are team-authored robustness checks, not knowledge of hidden tests. No new
directive types or battery rules are invented.

Provider failures, quota, free-router variability, and cold starts can affect
latency and interpretation. In-process caches are not shared across workers.
Fallback shims and relaxed constraints can return plausible but incorrect plans;
the judge treats these as failures under expected directives. A pattern-based
secret scan is only one check: inspect runtime logs and Docker metadata locally
without publishing credentials. Never echo raw provider exceptions or headers.
