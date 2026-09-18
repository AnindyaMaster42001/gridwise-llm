# The four hours — 7:00 PM to 11:00 PM

Wall-clock plan. `T+` is time since the round opened. Four people, four lanes,
three sync points. The rule that matters: **a submittable service exists from
T+1:30 onward and only ever gets better.**

## T+0:00 → T+0:15 · everyone: align

- Clone, `make install`, read `docs/00-spec-digest.md` and your own brief.
- Confirm file ownership out loud (`docs/02-contracts.md`). Nobody edits
  `app/schemas.py` or `app/config.py`.
- **B starts here, immediately:** get a provider key working and make one
  throwaway call. Key/quota problems are the only thing that can cost an hour,
  so find them at T+0:05, not T+2:00.
- **D starts here, immediately:** push the skeleton to the host. `/health`
  already works; get a live URL before anyone writes real code.

## T+0:15 → T+1:30 · build in parallel

| Lane | Deliverable by T+1:30 |
|---|---|
| **A** | `run_pipeline` end to end against a stubbed interpreter; `summarise_totals` exact; 400/500 handlers verified |
| **B** | prompt v1 + client + `interpret_notes` returning real model output for all 10 public notes |
| **C** | `validate_interpretations` + `build_constraint_set` + `solve` reproducing all 10 reference costs offline |
| **D** | live URL serving `/health`; `harness/judge.py` scoring interpretation + schema; image pushed with tag `v1` |

C is not blocked by B: hand-write `Directive` lists from
`tests/data/public_samples.json` expectations and solve against those.
A is not blocked by B: stub `interpret_notes` to return all-`no_op`.

## T+1:30 · SYNC 1 — first integration (15 min, everyone in one call)

Merge all four branches into `main`. Target: **the real pipeline answers a real
sample end to end**, even imperfectly. Deploy it. From this moment there is
always something submittable.

If a lane is not ready, integrate the other three and stub the fourth. Do not
wait.

## T+1:45 → T+2:45 · harden

| Lane | Focus |
|---|---|
| **A** | time budget, concurrency, fallback wiring, `plan_summary` truthfulness |
| **B** | paraphrase bank to ≥ 12 wordings per type; prompt v2; caching; failover tested by revoking the primary key |
| **C** | `verify()` complete; infeasibility ladder; `safe_baseline_plan`; rounding and neutrality snapping |
| **D** | full judge harness incl. latency + malformed input; README quickstart written and **followed by someone else** |

## T+2:45 · SYNC 2 — score ourselves (15 min)

Run `python3 -m harness.judge --base-url <live> --repeat 3`. Read the seven
category scores out loud. Spend the next hour on the **lowest** category, not on
the most interesting one. Decide together — this is the moment scope gets cut.

## T+3:00 → T+3:30 · fix the worst category, then freeze

Whatever the harness said. Then:

- **Feature freeze at T+3:30.** No new behaviour after this, only fixes to
  things the harness reports as broken.
- D records the 3-minute video (tie-break only, but ties happen at the cutoff).

## T+3:30 → T+3:50 · submit

Run the final checklist in `README.md` top to bottom:

- [ ] `/health` returns `{"status":"ok"}` from an outside network
- [ ] all 10 public samples pass the local judge against the **live** URL
- [ ] malformed JSON → 400, service still up afterwards
- [ ] p95 under 5 s over 3 repeats
- [ ] image pulls and starts from the exact documented tag, reaches `/health`
- [ ] README quickstart followed verbatim by someone who did not write it
- [ ] no secret anywhere: `git log -p | grep -iE 'api[_-]?key|sk-|bearer'`
- [ ] video accessible and ≤ 3:00
- [ ] submission form filled with URL, repo, image tag, video link

## T+3:50 → T+4:00 · hands off

Stop pushing. Keep `/health` warm. Make the repo public **after** the deadline,
not before.

## Standing rules

- **Never break `main`.** If `main` is red, that is the only thing anyone works
  on until it is green.
- **Valid beats cheap, always.** An invalid case loses application *and*
  optimization credit; an expensive valid case keeps 25 of the 35.
- **Say it out loud.** A blocked lane that nobody announced is the expensive
  failure mode in a four-hour round, not a hard bug.
