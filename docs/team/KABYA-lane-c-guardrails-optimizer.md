# Lane C — guardrails, optimizer, verifier

**Owner: Kabya Mithun Saha**

**You own:** `app/guardrails.py`, `app/optimizer.py`, `app/verifier.py`
**You must not edit:** `app/main.py`, `app/pipeline.py` (Anindya) ·
`app/llm/*` (Ninad) · `harness/*`, `tests/*` (Fayek) · `app/schemas.py`,
`app/config.py` (frozen)

**Points you directly control:** 20 of the 25 in *Directive Application &
Constraint Correctness* (everything except B's extraction accuracy) and all 10 of
*Optimization Quality*.

**Read first:** `docs/04-guardrails.md` and `docs/03-optimizer-formulation.md` —
those two are your full specification. `docs/reference/lp_spike.py` is a working
implementation of the LP that has already been verified against all ten public
cases.

---

## You are not blocked by anyone

Your whole lane is pure functions over data you can hand-write. Build the
expected `Directive` lists straight from `tests/data/public_samples.json`
(`cases[i].expected_output.directive_interpretation`) and you can implement,
test and finish the entire optimisation half before Ninad's first prompt exists.
Start there.

---

## Task C1 — the optimizer (~45 min, do this first)

`docs/03-optimizer-formulation.md` has the complete formulation and the proof
that a plain LP is exact here (the battery is lossless, so there is no
integrality gap and no heuristic to tune). `docs/reference/lp_spike.py` already
reproduces the organizer's reference cost to `+0.00` on all ten samples.

Your job is to productionise it:

- read bounds off the `ConstraintSet` rather than re-deriving them from directives;
- collapse `c[h] - d[h]` into one action per hour (the doc proves this is always
  legal — no binary variables needed);
- **recompute `battery_energy_after_kwh` by walking the emitted `battery_kwh`
  sequence**, never from the LP's own cumulative values;
- snap `|x| < 1e-9` to zero, clamp negatives, round to 6 decimals;
- force `plan[23].battery_energy_after_kwh == initial_energy_kwh` exactly, and
  fix up `plan[23].battery_kwh` if rounding moved it;
- raise `InfeasibleError` on `status != 0` — never return a partial plan.

**Done when:** solving each public case with its expected directives reproduces
the reference `total_cost_bdt` within 0.01, ten times out of ten.

## Task C2 — the verifier (~30 min)

`verify()` is our copy of the judge. Write it **from `docs/00-spec-digest.md` §7,
not from your optimizer** — a verifier that shares code with the solver will
happily validate the solver's bug.

Return a list of human-readable violations; empty means valid. Check all twelve
items listed in the `app/verifier.py` docstring, every comparison at
`schemas.TOL = 0.01`.

Then point it at the ten **reference** plans in the sample pack. They must all
come back clean. If one does not, your verifier is wrong, not the organizer's —
fix it before you trust it on your own output.

## Task C3 — the safe baseline (~20 min)

`safe_baseline_plan()` is the bottom of the failure ladder and it must never
raise. Grid covers demand, free solar is used up to the effective cap, battery
idle all day: balance holds, the battery never moves, so bounds and neutrality
hold trivially. The only directive that can break it is `max_grid_window`, so
pre-charge before capped hours and discharge inside them by exactly the shortfall.

This is worth real points. An invalid case loses application **and** optimization
credit; a valid expensive case keeps 25 of the 35.

## Task C4 — guardrails (~45 min)

`docs/04-guardrails.md` is the full spec: six stages, the repair table, the
combination rules. The policy in one line — **repair what is unambiguously
repairable, demote everything else to `no_op`, never invent, never drop.**

The two traps worth repeating:

- **The factor direction.** At this layer you cannot tell whether `0.8` meant
  "80% remains" or "80% was lost". Only flip when the model's own explanation
  says "reduction"/"drop of"/"decrease of" *and* it emitted the matching
  percentage. Otherwise trust the number. Unit-test both directions.
- **Never auto-convert a suspicious reserve.** A `1.0` reserve on a 200 kWh
  battery is probably a fraction — log it, return it unchanged. Silent conversion
  turns a correct 1 kWh reserve into a wrong 200 kWh one.

`build_constraint_set` combination rules when two directives hit one hour:
solar factors **multiply**, minimum energy takes the **max**, grid caps take the
**min**, blocked windows are **any**.

## Task C5 — the infeasibility ladder (~15 min)

Expose a helper Anindya can call. Organizer scenarios are guaranteed feasible, so
infeasible means *our interpretation is wrong*:

1. all directives → 2. drop one at a time, `source == "fallback"` before
`"llm"`, hard windows before caps → 3. no directives → 4. `safe_baseline_plan`.

Log which rung fired.

---

## Definition of done

- [ ] all 10 public cases reproduce the reference cost within 0.01
- [ ] `verify()` passes all 10 organizer reference plans
- [ ] `verify()` catches every violation class: balance, bound, rate, transition,
      solar overuse, grid cap, blocked window, neutrality, idle-with-nonzero-kwh
- [ ] every guardrail test in `docs/04-guardrails.md` passes
- [ ] a raw `[]` from the model yields N all-`no_op` directives, no exception
- [ ] contradictory directives degrade through the ladder to a valid plan
- [ ] `safe_baseline_plan` is valid under a `max_grid_window` tight enough to bite
- [ ] end-of-day battery equals initial exactly after rounding, in all 10 cases

## Agent prompt

Paste this verbatim as the first message to your Claude Code agent.

> You are working as **Kabya Mithun Saha**, owner of Lane C (guardrails,
> optimizer and verifier) on a four-person hackathon team.
>
> Read `docs/00-spec-digest.md`, `docs/03-optimizer-formulation.md`,
> `docs/04-guardrails.md` and `docs/team/KABYA-lane-c-guardrails-optimizer.md` in this
> repo, plus the verified spike at `docs/reference/lp_spike.py`. Implement
> `app/optimizer.py`, `app/verifier.py` and `app/guardrails.py` to those specs.
> Only edit those three files. Write `app/verifier.py` from the spec text, not by
> reusing optimizer code. Validate by loading `tests/data/public_samples.json`,
> building directives from each case's `expected_output.directive_interpretation`,
> solving, and comparing your `total_cost_bdt` to the reference — report the
> per-case difference for all ten.
