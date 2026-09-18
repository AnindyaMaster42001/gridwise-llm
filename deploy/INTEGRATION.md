# Lane D integration findings — Fayek Ahmed

Baseline: `origin/main` commit `2a0f5cc` (Lane A merged), rebased successfully
onto branch `phase-04`. No files under `app/` were changed by Lane D.
Findings below describe this checkout, not future merges.

## Measured localhost result

Command: `python -m harness.judge --base-url http://localhost:8000 --repeat 3
--output harness/reports/localhost.json`.

| Category | Diagnostic points | Maximum |
|---|---:|---:|
| Interpretation | 3.33 | 25 |
| Application / energy correctness | 17.50 | 25 |
| Optimization | 3.67 | 10 |
| API / schema | 10.00 | 10 |
| Measured performance / reliability | 8.00 | 10 |
| Deployment / Docker artifact review | Unassessed | 10 |
| Documentation / fresh-machine review | Unassessed | 10 |

Measured total: **42.50/100**, provisional rather than an official score.
30/30 optimization requests returned 200; 12/30 plans passed independent energy
replay (4 unique cases: 02, 03, 04, 08). This does not mean interpretation passed.
The shared verifier was unimplemented in all runs, so the release gate failed.
p95 was approximately 0.011 seconds on the final repeat, but this measured
**stubs, not model calls**.
The report explicitly withholds untested rubric components.

## Owner action list

| Owner | Finding | Reproduction / required outcome |
|---|---|---|
| Ninad / B | `app/llm/*` still contains stubs | Implement Gemini primary and OpenRouter compatible fallback, raw directive extraction, prompt, caching, safe provider errors. Run the 72 live paraphrases with actual keys. |
| Kabya / C | Guardrails, optimizer, and shared verifier still contain stubs | `python -m pytest tests/test_guardrails.py tests/test_samples.py tests/test_robustness.py --integration -q`. Implement the frozen interfaces; these tests then execute automatically. |
| Anindya + shared-schema owner | Infinite demand is accepted and sanitized into a zero-demand-looking plan | `test_invalid_numeric_input_is_rejected` case 0. Reject non-finite request values before the pipeline; do not silently turn them into zero. |
| Anindya + shared-schema owner | Initial battery energy above capacity and minimum above initial are accepted | Same test cases 2/3. Reject impossible battery configurations with controlled 400/422. |
| Anindya + shared-schema owner | JSON boolean `true` is accepted as hour 1 | Same test case 4. Hours must be integers, not booleans/coercible values. |
| Anindya | Raw interpreter exception text leaks into logs through `log.exception` | `tests/test_release_risks.py::test_provider_exception_does_not_leak_sensitive_text_to_logs`. Synthetic canary is logged. Log sanitized error type/status without raw exception content or credentials. |
| Anindya | `_call_solver` awaits its worker without the remaining deadline | `tests/test_release_risks.py::test_solver_deadline_is_bounded`. A 0.7s worker exceeds a 0.3s remaining budget. Bound waiting and provide solver-side time limits; cancelling a thread await does not stop the underlying solver. |
| Fayek + account owner | No public host/registry selected; OpenRouter fallback key missing | Fill the fallback key in the ignored `.env`, choose the target and registry, publish an immutable image, and run the external judge. |
| Fayek | Final video and fresh-machine walkthrough depend on the completed runtime | Follow `VIDEO_SCRIPT.md`, keep the recording under 180s, verify outsider access, record artifacts in `submission.json`. |

The numeric and failure-path checks are `release` tests: default offline runs
show skips, while `--integration` requires them. The targeted release-risk run
produced **6 failures and 11 passes**: NaN tariff rejection already passes.
Tests use synthetic markers, never actual secrets. Nothing was sent to teammates
automatically; this file is the handoff.

Final offline run: **106 passed, 138 skipped**. Of those skips, 59 depend on
unfinished production functions, seven are release-risk checks, and 72 require
explicit live-model opt-in. Full `--integration` run: **107 passed, 65 failed,
72 skipped** (59 missing implementations plus six reproduced defects). The
extra passing release check is NaN tariff rejection. All required checks are
therefore visible; a green offline subset is not a release approval.

## Canonical-spec corrections

The Participant Guide §08 averages cost quality across **all optimization
cases**, with zero for invalid cases. Older lane briefs/scoring notes say “valid
cases only”; the harness follows the PDF and has a regression test for this.

Problem Statement §09 requires `idle => battery_kwh == 0`. It does not impose
the reverse condition that a zero-magnitude charge/discharge is invalid. The
independent judge therefore accepts it. A service may normalize it to idle.
Likewise, the PDF requires unique plan hours 0–23, but only directive adjustment
hours are explicitly required to be ascending. Replay sorts valid plan rows.

Overlapping reductions multiplying is the team's interpretation of sequential
solar adjustments. It is tested and documented as such; it is not claimed as
an unpublished organizer rule. Hidden cases introduce no new directive types.

## Evidence locations

- `harness/reports/localhost.json`: local live-service report (ignored in Git).
- `harness/reports/stress_cases.json`: generated synthetic cases.
- `python -m harness.preflight --history`: explicit remaining stubs, missing
  configuration/artifacts, and high-confidence secret-pattern scan.
- `python -m pytest -q`: offline judge/API tests and visibly skipped dependencies.
- `python -m pytest --integration -q`: required integration/release gate.

The source/Git-history pattern scan found no credential matches during this run.
This is limited evidence, not a guarantee about future provider logs or images.

Direct Gemini smoke test: after a primary key became available locally,
`gemini-3.1-flash-lite` returned HTTP 200 and the expected `[13,14]` / `0.2`
interpretation in 3.833 seconds. The initial less-explicit smoke prompt failed
the integer-hours check; explicitly requesting integer 24-hour values passed.
This supports using a strict output schema and guardrails, and is only a single
synthetic connectivity test. It does not certify Lane B's unfinished interpreter.
OpenRouter remains untested because its key is absent. See
`harness/reports/providers.json`.

## Verified local Docker fallback

`docker build -t gridwise-llm:lane-d .` succeeded using the documented offline
Linux wheel path after the container's PyPI download timed out. The same
Dockerfile supports a normal online build when the wheel directory is empty.

`python -m harness.container_check --image gridwise-llm:lane-d` passed:

- Health ready in **1.781 seconds** on the final run.
- Containerized optimization returned HTTP 200 with a valid response schema.
- The sample's **ground-truth plan was invalid**, consistent with the documented
  missing production implementations; container mechanics passing is not release
  approval.
- UID **10001**, root filesystem read-only, Docker health **healthy**.
- No credential-pattern matches in image config/history; `.env`, Git, and tests
  absent from `/app`.
- Graceful shutdown exit code **0**; the temporary audit container was removed.

Local image ID:
`sha256:cd92522b919efea3a1192d91850c250aa1412ce45ac3d875dabe9c617324297c`.
This is a **local image**, not a published registry reference. Registry push,
clean-machine pull, public endpoint, and video still require completion.
Full sanitized evidence: `harness/reports/container.json`.
