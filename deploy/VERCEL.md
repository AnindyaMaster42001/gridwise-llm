# Deploying to Vercel

The judge needs one public base URL answering `GET /health` and
`POST /optimize-energy` with no login. This is the exact path to get there.

Vercel auto-detects this project as `framework: fastapi` and serves
`app/main.py` directly at the root. There is **no adapter file and no rewrite** —
the judge exercises the same application the container and the tests do.

> A `rewrites` rule is actively harmful here. It replaces the path the function
> receives, so `/health` arrives as `/api/index/health` and the app correctly
> 404s every route. The framework preset already routes the whole app; adding a
> rewrite on top of it only breaks it.

---

## Read this before you deploy

Vercel is a serverless host, and three of its properties bite this particular
service. All three are handled below, but you should know they exist.

| Property | Effect here | Handling |
|---|---|---|
| Function duration cap | Hobby defaults to **10 s**; a p95 of 6.6 s plus a cold start would time out | `vercel.json` sets `maxDuration: 60` on `app/main.py` — the glob must name the real entrypoint or it silently does nothing |
| Cold starts | SciPy import costs ~0.8 s on top of container start | keep-warm ping below; `/health` never imports the solver |
| No shared memory between invocations | the in-process interpretation cache resets, so more provider calls | matters most against a capped quota — see the blocker below |

Bundle size is fine: the built function measures **71 MB**, well under Vercel's
250 MB uncompressed limit, because `.vercelignore` keeps tests, docs, the PDFs
and the Docker files out of it.

**Deployment Protection must be off.** A new Vercel project enables Vercel
Authentication, which answers anonymous requests with a `302` to
`vercel.com/sso-api`. The judge would see a login redirect, not the API, and the
rules forbid requiring any login. Verify with a plain `curl` from outside your
network — not a browser you are already signed into.

> **Blocker that outranks deployment.** The configured Gemini key is free tier:
> **20 requests per day, per model**. A judged round will exhaust that in
> minutes, after which every request silently falls back to the deterministic
> interpreter — which does not satisfy the mandatory-LLM requirement and costs
> the challenge outright. Enable billing on the Google Cloud project, or add a
> second provider key from a **different vendor**, before you submit. Deploying
> first and fixing the quota afterwards wastes a deploy.

---

## 0. Check your git commit email first

Vercel matches the **commit author email** of the deployed commit to a GitHub
account. If it does not match one, the deployment is rejected with
`Deployment Blocked` and the API reports:

```json
seatBlock: { "blockCode": "TEAM_ACCESS_REQUIRED", "isVerified": false }
```

That message says nothing about email, and the CLI prints a cheerful
`Building…` and then simply stops — so this looks like a quota problem or a
stuck build when it is neither. Check it before you spend time anywhere else:

```bash
git config user.email          # must be an address on your GitHub account
git log -1 --format='%an <%ae>'
```

If it is wrong, set it for this repository and make a fresh commit — Vercel
reads the email of the commit being deployed, so an already-pushed commit with
the wrong address will keep being rejected until a correct one sits on top:

```bash
git config --local user.email "you@example.com"
git config --local user.name  "your-github-username"
```

A machine shared with other accounts is the usual cause: a global
`user.email` belonging to some other service gets picked up silently. Setting
it per-repository avoids changing anything for your other projects.

## 1. Log in and link the project

The CLI login is interactive, so run these yourself:

```bash
npx vercel@latest login
cd ~/gridwise-llm
npx vercel@latest link --yes
```

`link` creates `.vercel/` locally. It is git-ignored and must stay that way.

## 2. Set the environment variables

Never put a key in `vercel.json` or in any committed file. Add them to the
project, for all three environments so a preview URL behaves like production:

```bash
printf '%s' "$GEMINI_KEY"  | npx vercel@latest env add LLM_API_KEY production
printf '%s' "gemini"       | npx vercel@latest env add LLM_PROVIDER production
printf '%s' "gemini-2.5-flash" | npx vercel@latest env add LLM_MODEL production

# Fallback: a DIFFERENT vendor, so it fails independently of the primary.
printf '%s' "$GROQ_KEY"    | npx vercel@latest env add LLM_FALLBACK_API_KEY production
printf '%s' "groq"         | npx vercel@latest env add LLM_FALLBACK_PROVIDER production
printf '%s' "llama-3.3-70b-versatile" | npx vercel@latest env add LLM_FALLBACK_MODEL production
```

Do **not** set `ALLOW_NO_LLM`. It exists for offline development and would let
the service answer without ever calling a model.

## 3. Deploy

```bash
npx vercel@latest --prod
```

Note the URL it prints. That is the submission's `public_base_url`.

## 4. Verify from outside your machine

A deployment that only works from your laptop is worth zero points.

```bash
BASE=https://<your-deployment>.vercel.app

curl -sS "$BASE/health"                      # {"status":"ok"}
bash scripts/smoke.sh "$BASE"                # sample + malformed input
python -m harness.judge --base-url "$BASE" --repeat 3
```

Then open `$BASE/health` on a phone with Wi-Fi **off**. That is the check that
catches a URL which is reachable only from your own network.

Confirm the model really ran: the response `plan_summary` must **not** contain
"deterministic backup interpreter". If it does, the provider is failing and the
mandatory-LLM requirement is not being met.

## 5. Keep it warm

A cold start during judging costs latency points. Ping `/health` every minute
for the duration of the evaluation window:

```bash
while true; do curl -fsS "$BASE/health" >/dev/null 2>&1; sleep 60; done
```

## Rollback

Every deploy keeps its own immutable URL. List them and promote a known-good
one if a late change breaks something:

```bash
npx vercel@latest ls
npx vercel@latest promote <previous-deployment-url>
```

Never push a change after the last verified judge run unless you have time to
re-verify.

## If Vercel does not work out

The container is the fallback path and is not serverless. `deploy/RUNBOOK.md`
covers running it on a plain host, which has no duration cap, no cold starts
and a cache that survives between requests.
