# Tie-break video: 2 minutes 50 seconds

Recording prerequisite: all lanes implemented, real LLM verified, public sample
judge passes, and a pullable image is available. This is a script, not a submitted
video. Do not show `.env`, provider dashboards, request headers, or raw error logs.

| Time | Show | Say |
|---|---|---|
| 0:00–0:20 | Title and one operator note | “GridWise plans one day of campus electricity. Our task is to understand the operator's restrictions, obey every energy constraint, and then minimize grid cost.” |
| 0:20–0:50 | README architecture | “Gemini produces structured directives. A second vendor, Groq, is our independent fallback route. Model output is untrusted: deterministic guardrails check types, note mapping, hours, and numbers before the optimizer receives constraints.” |
| 0:50–1:15 | Note: 80% solar reduction, 1–3 PM; validated object | “An eighty percent reduction means a remaining factor of 0.2. The interval includes hours 13 and 14, not 15. We return exactly one entry per note, including explicit no-op entries for irrelevant notes.” |
| 1:15–1:45 | Real `/optimize-energy` request and response | “The solver balances demand, usable solar, grid purchases, and battery action every hour. It respects reserves and rates, blocks prohibited actions, and restores initial battery energy at the end of the day. Totals are recomputed from the returned plan.” |
| 1:45–2:15 | Judge report and a mutation test | “Our judge uses expected directives independently of the service's interpretation. This test corrupts a grid total or ignores a solar limit and loses validity and cost credit. Invalid cases remain in the scoring denominator. We also cover zero-cost days, midnight windows, decimal values, and 72 paraphrases.” |
| 2:15–2:40 | Clean Docker pull/run, health and sample command | “The image runs without root privileges. Keys arrive only at runtime. The README documents the exact model configuration, sample runner, and registry digest so organizers can reproduce the result.” |
| 2:40–2:50 | Public URL, repo, image digest (no secrets) | “These are our verified submission artifacts. Our central rule is to verify the restrictions and energy accounting before awarding optimization credit.” |

Prepare terminals before recording:

```bash
python -m harness.export_sample
python -m pytest tests/test_judge.py -q
python -m harness.judge --base-url PUBLIC_URL --repeat 3 --output harness/reports/video.json
```

Use the actual public URL and immutable digest in the recording. Show one real
request, then the already-finished report to avoid spending video time waiting
for 30 model requests. Do not represent offline fixtures as live model calls.
Export MP4 at 720p or 1080p, ensure readable text and audible narration, check
duration is at most 180 seconds, and test the link without your logged-in session.
