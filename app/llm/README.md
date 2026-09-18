# Lane B: LLM operator-note interpretation

Owner: **Muhaiminul Islam Ninad**. Files: `app/llm/*`, `harness/paraphrase_bank.json`.

This is the mandatory step. A language model directly produces the structured
interpretation that reaches the optimizer; the regex module is a degradation
path, not the interpreter. Guide §04 fails a submission where phrase matching is
the *sole* interpreter, and fails one where the model only writes `plan_summary`.

## The call path

```
pipeline.run_pipeline
   └─ interpreter.interpret_notes(notes, battery)      ← one call, all 1-3 notes
        ├─ per-note cache hit?                 → return, no network
        ├─ primary provider  (+ LLM_MAX_RETRIES)
        ├─ fallback provider (+ LLM_MAX_RETRIES)
        └─ raise InterpretationUnavailable
   └─ on that exception only: fallback.rule_based_interpret(notes, battery)
   └─ guardrails.validate_interpretations(raw, notes, battery)   ← Kabya's
```

| File | Does |
|---|---|
| `prompts.py` | system prompt, 8 few-shots, per-request user message, JSON schema |
| `client.py` | one provider (openai / groq / gemini / openai_compatible), redacted errors |
| `interpreter.py` | the LLM step: cache, retry, failover, tolerant JSON, alignment |
| `fallback.py` | deterministic regex backup, used only when every provider is down |
| `bench.py` | offline self-check for this lane (see below) |

## What guardrails receives

`interpret_notes` returns exactly `len(notes)` dicts, in note order, **untrusted
and unvalidated**: hours are not range-checked, factors are not clamped,
percentages are not resolved. Two things Kabya's `validate_interpretations`
should expect:

- `directive_type` may be `None` (the model skipped that note) or a string
  outside the six. Both mean `no_op`.
- there is an extra `"source"` key, `"llm"` or `"fallback"`, for `Directive.source`.

Alignment is the only reshaping done here: entries are placed by `note_index`
when it is usable and positionally otherwise, so the response contract
(one entry per note, ascending) can never be broken by the model.

## Configuration

Two providers from different vendors, both from the environment, no key in code:

```bash
LLM_PROVIDER=openai   LLM_MODEL=gpt-4o-mini            LLM_API_KEY=...
LLM_FALLBACK_PROVIDER=groq LLM_FALLBACK_MODEL=llama-3.3-70b-versatile LLM_FALLBACK_API_KEY=...
```

`openai` and `gemini` get native constrained decoding against
`prompts.response_json_schema()`; `groq` and `openai_compatible` get JSON mode
plus `interpreter.extract_json`. The Gemini key travels in `x-goog-api-key`, not
the query string, so it cannot leak through a URL in a log.

## Self-check: run it after every prompt edit

```bash
python3 -m app.llm.bench bank        # is the bank's own ground truth legal?
python3 -m app.llm.bench selftest    # client, cache, failover, redaction (mocked HTTP)
python3 -m app.llm.bench fallback    # regex interpreter vs the bank
python3 -m app.llm.bench samples     # the 10 public cases, offline
python3 -m app.llm.bench llm         # the real model vs the bank (needs a key)
```

Measured on 2026-09-18, no provider key configured yet:

| Check | Result |
|---|---|
| `bank` | 95 cases, 16-17 per directive type, 13 distractors, every expectation legal |
| `selftest` | 37/37 |
| `fallback` | 95/95 |
| `samples` | 18/18 notes |
| end to end, through lanes A and C | 18/18 interpretations exact, 10/10 costs at +0.00 vs the organizer reference |
| `llm` | **not yet run, needs a key.** This is the number that matters. |

The fallback's 95/95 is **not** a generalisation claim. Those regexes were tuned
against that bank. The one unbiased measurement taken was on 15 wordings held
out before any tuning: the fallback scored **60%** on them, against 100% on the
set it was written for. That gap is the whole argument for the model being the
primary path, and it is why `bench llm` is the only number worth quoting. Every
one of those six failures degraded to `no_op`, which is the safe direction: a
missed directive costs that note's interpretation credit but never produces an
invalid schedule.

## Definition of done

- [x] all 10 public sample notes resolve to the expected type, hours and numbers
- [x] ≥ 12 paraphrases per directive type in the bank (16-17), ≥ 95% correct
- [x] 13 distractors all → `no_op`
- [x] wrap-around window returns ascending hours (`[0, 1, 23]`)
- [x] "80% reduction" and "drops to 20%" both → `0.2`
- [x] "50% of capacity" resolves against the request's `capacity_kwh`
- [x] second identical request is a cache hit, case- and punctuation-insensitive
- [x] no key appears in any log line or error message (`selftest`, four assertions)
- [ ] **a real key, and `bench llm` green**. Nothing else in this lane is real until then
- [x] revoking the primary key still yields a correct interpretation via failover,
      and the dead provider is not retried first (401 is classified permanent, so
      the budget goes to the other vendor). Mocked; the live rehearsal needs keys.
- [ ] both providers down → regex fallback, `source="fallback"`, no exception
      escapes. This half belongs to `pipeline.run_pipeline`, which must catch
      `InterpretationUnavailable`; verified here only up to the raise

## Retry policy

`LLMError` carries a `retryable` flag. 429, 5xx, timeouts and transport errors
are retried once on the same provider (`LLM_MAX_RETRIES`); 401, 403, 404 and 422
are not, because a revoked key or a wrong model name fails identically the second
time. This matters against the merged budget: `pipeline` hands interpretation
`request_budget_s - 3` (about 19 s), and a dead primary that is retried before
failover can eat it all at `LLM_TIMEOUT_S` per attempt.

## Notes for the other lanes

- **Anindya:** the shim works as-is, nothing needed. One small thing still open:
  `lifespan` does not `await app.llm.interpreter.aclose()` on shutdown, so the
  httpx pools are dropped rather than closed. Harmless for scoring, one line if
  you want it clean. Your call, it is your file.
- **Kabya:** see "What guardrails receives" above. I re-probed the merged
  `validate_interpretations` with every shape my module can emit (blank slot,
  unknown type, string hours, out-of-range hours, factor as a percentage,
  reserve above capacity, NaN, missing numeric field). All nine repair or demote
  correctly and none raises, so the seam holds.
- **Fayek:** `bench.py selftest` is the part to lift into `tests/test_llm.py` at
  integration: it needs no key and no network.
