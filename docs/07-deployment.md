# Deployment

Two things must be true at submission: a **public base URL** the judge can call
without any login, and a **pullable image** with an exact tag as the fallback.
Together they are 10 points, and they are the easiest 10 points on the board.

## Do this first, not last

Deploy the skeleton — which already serves `/health` — in the first half hour,
while `/optimize-energy` still 500s. Then every later push is a redeploy of
something already proven reachable, instead of a first-time deploy at 10:45 PM.

## Host choice

| Option | Verdict |
|---|---|
| **A VPS you already own** (`docker run`, port 80/8000) | **best** — always on, no cold start, no platform quota, you control restarts |
| Fly.io | good; `min_machines_running = 1` or it sleeps |
| Railway | good; no sleep on the starter plan |
| Render free tier | **risky** — spins down when idle and cold-starts in ~50 s, against a 60 s health budget |
| Any serverless/Lambda wrapper | risky — cold start plus an 8 s model call is close to the limit |

Whatever you pick: HTTPS or plain HTTP both work, but the URL must answer from
outside your network. Test it from a phone on mobile data — that catches the
"works on my laptop, blocked by my router" failure that is invisible at home.

## Container rules the judge checks

- Binds `0.0.0.0`, not `127.0.0.1` — otherwise the port mapping is dead.
- Exposes the documented port, and the README's `docker run` is the exact command
  that was tested.
- **No baked-in secrets.** Keys arrive via `-e` / `--env-file`. Verify with
  `docker history --no-trunc <image> | grep -i -E 'key|token|secret'` before you
  push, and never `COPY .env`.
- Reaches `/health` on its own: the `HEALTHCHECK` in the Dockerfile proves it.

Publish to Docker Hub or GHCR and record the **exact tag or digest** in the
README. The image must stay pullable through the whole evaluation window.

```bash
docker build -t <user>/gridwise-llm:v1 .
docker push <user>/gridwise-llm:v1
docker inspect --format='{{index .RepoDigests 0}}' <user>/gridwise-llm:v1
```

## Pre-submit verification, from a clean machine

```bash
BASE=https://your-service.example.com
curl -sS "$BASE/health"                                    # {"status":"ok"}
bash scripts/smoke.sh "$BASE"                              # sample + malformed
python3 -m harness.judge --base-url "$BASE" --repeat 3     # full local score
```

Then repeat the entire README quickstart in a fresh directory, as if you were the
judge and had never seen the repo. Every undocumented step you have to remember
from memory is a point lost in category 7.

## Keeping it warm

A 4-hour judging window with an idle service invites a cold start at the worst
moment. Run a cron or a laptop loop hitting `/health` every 60 s. Log nothing
sensitive from it.

## Rollback

Tag every deployed image (`v1`, `v2`, …) and keep the last known-good tag
pullable. If a late push breaks the service, redeploying the previous tag is a
30-second fix — and **never push to production after the last verified judge
run** unless you have time to re-verify.
