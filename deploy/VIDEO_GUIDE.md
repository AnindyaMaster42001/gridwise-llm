# How to record the 3-minute video

`VIDEO_SCRIPT.md` is **what to say**. This is **how to make it**.

---

## What you are actually being scored on

The video carries **no base points**. It is used only when two or more teams
finish on the same total score — which is precisely what happens at a
qualification boundary, so it is worth the 40 minutes.

Reviewers compare four things, in this order:

1. **Problem understanding** — do you know what the challenge is really asking?
2. **Architecture clarity** — is the LLM → guardrails → optimizer flow legible?
3. **Solution approach** — did you make defensible engineering choices?
4. **Run/testing explanation** — could an organizer reproduce this?

Everything else — music, transitions, a face on camera, production polish — is
explicitly not scored. Do not spend time on it.

**Hard limit: 3:00.** A video of 3:01 risks being discarded. The script targets
2:50 to leave room for slips.

---

## Before you press record

### 1. The pipeline must be genuinely working

Do not record against a broken or quota-exhausted provider. Check:

```bash
BASE=https://<deployment>.vercel.app
curl -sS "$BASE/health"
python -m harness.judge --base-url "$BASE" --repeat 3 --output harness/reports/video.json
```

The response `plan_summary` must **not** contain "deterministic backup
interpreter". If it does, the model is not being reached and you would be
recording a claim that is not true.

### 2. Pre-stage everything slow

Record one **real** request live — that is your evidence the model is in the
path. Everything that takes more than a few seconds should already be finished
and on screen:

```bash
python -m harness.export_sample            # a clean sample request/response
python -m pytest tests/test_judge.py -q     # the mutation tests
```

Have the judge report from step 1 already open in a second pane. Never record
yourself waiting for 30 model calls.

### 3. Open exactly four things, in this order

1. `README.md` scrolled to the architecture diagram
2. A terminal, font size **18pt or larger**, dark theme, window ~1280 wide
3. The `harness/reports/video.json` report
4. The browser on `$BASE/health`

Close Slack, mail, notifications. Turn on Do Not Disturb. A notification popup
during a technical review reads as carelessness.

### 4. Secret hygiene — this is a scored rule, not etiquette

The Guide bars secrets from the repo, logs, **and responses**. Before recording:

- close every `.env` file and provider dashboard tab
- clear your shell history pane of any `export LLM_API_KEY=...`
- check your terminal prompt does not embed a token
- if you show `vercel env ls`, it prints names only — values stay hidden, which
  is fine, but do not run `vercel env pull`

One frame of a visible API key is worse than a missing video: it is a
disqualifying repository/secret-handling violation that you published yourself.

---

## Recording

**Tooling, easiest first**

| Tool | Notes |
|---|---|
| OBS Studio (free, Win/Mac/Linux) | best control; set 1920×1080, 30 fps, MP4 |
| Windows Game Bar (`Win+G`) | already installed, records a window, MP4 out |
| Zoom | start a meeting alone, share screen, record locally |

Record **1080p** if you can, 720p minimum. Text must be readable when the
reviewer watches in a small window — that is why the terminal font goes up.

**Audio matters more than video.** A clear phone-headset mic in a quiet room
beats a laptop mic in a room with a fan. Do a 10-second test and listen back
before committing to a full take.

**Speak at ~140 words per minute.** The script is about 400 words for 2:50. If
you are rushing to fit, cut a sentence rather than talking faster.

**Do it in one take if you can.** Three fluent takes with a stumble beats a
heavily-cut video — and cutting costs you more time than re-recording.

---

## The four beats that matter

If you are short on time, protect these and trim elsewhere.

**1. The core idea (0:20–0:50).** Say the sentence that shows you understood
the challenge:

> "Human notes are never trusted as math. The model's job ends when it emits a
> structured guess; deterministic guardrails validate it, and only then does
> the optimizer see a constraint."

**2. The two interpretation traps (0:50–1:15).** Show that you know where this
challenge is actually won:

> "An eighty percent reduction means a *remaining* factor of 0.2, not 0.8. And
> one PM to three PM is hours 13 and 14 — end-exclusive, not 15."

Point at the returned `structured_adjustment` on screen while you say it.

**3. Application, not just extraction (1:15–1:45).** The judge replays your
plan against *its* directives. Say so:

> "Extracting the directive is not enough — the judge replays our schedule
> against its own ground truth. So we verify our own plan before returning it,
> and if it fails we return a valid expensive plan rather than an invalid cheap
> one."

**4. Reproducibility (2:15–2:40).** Show the container starting and reaching
health, and say keys arrive only at runtime.

---

## Common ways this video goes wrong

| Mistake | Why it costs you |
|---|---|
| Reading the code line by line | Reviewers are comparing *architecture*, not syntax |
| Unreadable 10pt terminal text | Your evidence becomes unverifiable |
| Claiming a fallback result is a live model call | Misrepresentation; reviewers check `plan_summary` |
| Showing `.env` or a dashboard with a key | Secret-handling violation |
| Running out of time before the testing section | Item 4 of the rubric never gets covered |
| 3:20 because "it's only slightly over" | Risks being discarded entirely |

---

## After recording

1. Export **MP4**, H.264. Confirm duration: `ffprobe -i video.mp4 2>&1 | grep Duration`
2. Watch it once, muted, at small size — is every terminal line still legible?
3. Watch it once with your eyes closed — does the narration alone explain the
   architecture?
4. Upload. Google Drive or YouTube **unlisted** both work.
5. **Set sharing to "anyone with the link"** and then open the link in a private
   window with no account signed in. A video the organizers cannot open scores
   the same as no video.
6. Record the URL in `deploy/submission.json` under `video_url`, and the
   duration in seconds under `video_duration_seconds`.

---

## 60-second pre-upload checklist

- [ ] duration ≤ 3:00
- [ ] problem, architecture, LLM → guardrails → optimizer flow, run/test all covered
- [ ] one real request shown, and it was genuinely served by the model
- [ ] no `.env`, no key, no dashboard, no raw traceback in any frame
- [ ] terminal text readable at small window size
- [ ] audio audible end to end
- [ ] link opens in a signed-out private window
- [ ] URL and duration written into `deploy/submission.json`
