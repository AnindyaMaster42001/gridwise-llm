# LLM interpretation — the 25-point module

Owner: **Ninad** (Lane B). This is the mandatory step: a language-capable generative
model must directly produce the structured interpretation that reaches the
optimizer. An artifact reviewer may inspect the repo to confirm it.

## Provider choice

Judged on latency (`p95 ≤ 5 s` earns the full 3 points) and availability during
the round. Pick a **fast, cheap, JSON-mode-capable** model, not a frontier one —
this is a short extraction task and a small model at temperature 0 does it well.

| Option | Why |
|---|---|
| `gpt-4o-mini` (OpenAI) | native structured outputs, very reliable JSON, ~1–2 s |
| `llama-3.3-70b-versatile` (Groq) | usually sub-second, excellent fallback |
| `gemini-2.0-flash` | generous free tier, `responseSchema` support |

Configure **two** providers (`LLM_*` and `LLM_FALLBACK_*`). One outage during a
four-hour judging window is a realistic event, and the failover costs ten lines.

## One call, not N

1–3 notes arrive together. Send them in a single completion with explicit
indices and ask for an array back. One round trip instead of three is the single
biggest latency win available, and it also lets the model see that two notes
describe different windows.

## Prompt requirements

The system prompt must state, unambiguously:

1. The six legal `directive_type` values, and that anything else is forbidden.
2. Return exactly one object per note, with the note's zero-based `note_index`.
3. Irrelevant notes → `no_op` with all fields null. Distractors are deliberate:
   cafeteria menus, library hours, registration deadlines, seminar bookings.
4. **Start-inclusive, end-exclusive windows.** Spell out several worked
   examples — "1 PM to 3 PM" → `[13, 14]`, "6 PM until 10 PM" → `[18,19,20,21]`,
   "between 11 AM and 2 PM" → `[11,12,13]`. This is the most common error mode.
5. `hours` ascending, unique, `0..23`; a wrap-around window still sorts
   ascending (`[0, 1, 22, 23]`).
6. `factor` is the fraction **remaining**: "80% reduction" → `0.2`, "drops to
   20%" → `0.2`, "half" → `0.5`, "one-fifth" → `0.2`, "a quarter" → `0.25`.
7. Percentage reserves are a percentage of the battery capacity given in the
   user message. Supply `capacity_kwh` every request so the model can compute it.
8. Never invent demand, solar, tariff or battery numbers.

Then 4–8 few-shot examples covering: a percentage reduction, a "drops to N%"
reduction, a reserve in kWh, a reserve as a percentage, a charge window, a
discharge window, a grid cap, and a distractor. **Do not use the public sample
wordings as your few-shots** — you want the model generalising, and the hidden
notes are paraphrases you have never seen.

## Flat output schema, not a union

```jsonc
{"interpretations": [
  {"note_index": 0, "directive_type": "solar_reduction",
   "hours": [13, 14], "factor": 0.2,
   "minimum_energy_kwh": null, "max_grid_kwh": null,
   "explanation": "…"}
]}
```

One object shape for all six types, unused fields `null`. A `oneOf` union reads
more elegantly and degrades much worse on small models. `guardrails.py`
re-assembles the exact `structured_adjustment` shape the judge wants.

## Reliability requirements

- `temperature = 0`, small `max_tokens`. Determinism matters: the judge may send
  the same paraphrase twice.
- Timeout `LLM_TIMEOUT_S` (default 8 s), one retry, then the fallback provider,
  then `fallback.rule_based_interpret`.
- **Cache** on `(normalised note text, capacity)`. Hidden cases reuse wordings
  and the judge repeats requests; a cache hit is free latency and free money.
- Tolerant JSON extraction: strip ``` fences, leading prose, trailing commentary.
- `LLMError` messages must never contain the key or the Authorization header.

## Honesty about the fallback

The regex interpreter exists so the service degrades instead of 500-ing. Mark it
`source="fallback"` and say so in the `explanation`. Do not let it run when the
model succeeded, and do not present it as the primary path — the Guide penalises
hard-coded phrase matching as the *sole* interpreter.

## How to know it works

`harness/paraphrase_bank.json` is the real test set. Target ≥ 12 wordings per
directive type, none copied from the public pack, each with its ground truth.
Run it after every prompt edit; a prompt change that fixes one wording and breaks
three is the failure mode that loses this category.
