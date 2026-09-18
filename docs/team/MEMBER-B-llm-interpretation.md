# Member B — LLM operator-note interpretation

**You own:** `app/llm/prompts.py`, `app/llm/client.py`, `app/llm/interpreter.py`,
`app/llm/fallback.py`, `harness/paraphrase_bank.json`
**You must not edit:** `app/main.py`, `app/pipeline.py` (A) · `app/guardrails.py`,
`app/optimizer.py`, `app/verifier.py` (C) · `harness/judge.py`, `tests/*` (D) ·
`app/schemas.py`, `app/config.py` (frozen)

**Points you directly control:** all 25 of *LLM Directive Interpretation*, and
you gate the 10 "ground-truth application" points inside category 2 — a note read
wrong fails twice. You also own whether the team satisfies the **mandatory LLM
requirement** at all.

**Read first:** `docs/05-llm-interpretation.md` (your spec),
`docs/00-spec-digest.md` §5–§6.

---

## Do this in the first five minutes

Get a key and make one real call. Nothing else in this repo can go wrong for an
hour; a dead key can.

```bash
cp .env.example .env    # fill LLM_API_KEY
python3 - <<'PY'
import os, httpx
r = httpx.post("https://api.openai.com/v1/chat/completions",
    headers={"Authorization": f"Bearer {os.environ['LLM_API_KEY']}"},
    json={"model": "gpt-4o-mini", "temperature": 0,
          "messages": [{"role": "user", "content": "reply with {\"ok\":true}"}],
          "response_format": {"type": "json_object"}}, timeout=20)
print(r.status_code, r.text[:200])
PY
```

Then configure a **second** provider from a different vendor. A four-hour
judging window is long enough for one outage, and the failover is ten lines.

---

## Task B1 — the prompt (~45 min, highest leverage in the repo)

`docs/05-llm-interpretation.md` lists the eight rules the system prompt must
encode. Three of them cause almost all real errors:

**End-exclusive windows.** "6 PM until 9 PM" is `[18, 19, 20]` — three hours, not
four. Put four worked examples in the prompt, including one wrap-around
(10 PM–2 AM → `[0, 1, 22, 23]`, ascending).

**`factor` is what remains.** "80% reduction" → `0.2`. "Drops to 20%" → also
`0.2`. Show both phrasings side by side so the model sees they collide.

**Percentage reserves resolve against capacity.** "50% of the battery" with a
200 kWh pack is `100`. Pass `capacity_kwh` in every user message.

Few-shots: 4–8, one per directive type plus a distractor, **written by you, not
copied from `tests/data/public_samples.json`**. The hidden notes are paraphrases
you have never seen; few-shots that echo the public pack teach the model the
wrong thing to generalise from.

## Task B2 — client with failover (~30 min)

```python
class LLMClient:
    async def complete_json(self, system, user, schema=None, few_shot=None) -> LLMResult
```

- `httpx.AsyncClient`, timeout `LLM_TIMEOUT_S`, `temperature=0`, small `max_tokens`.
- Native JSON mode where available: OpenAI `response_format={"type":"json_schema", …}`,
  Gemini `responseSchema`. Groq and OpenAI-compatible hosts take
  `{"type": "json_object"}` plus a JSON-only instruction.
- Every failure — timeout, 4xx, 5xx, unparseable body — becomes `LLMError`.
  **The message must never contain the key or the Authorization header.** Log the
  status code and the first 200 characters of the body, nothing more.
- `build_primary()` / `build_fallback()` read `app.config`; return `None` when no
  key is set.

## Task B3 — `interpret_notes` (~30 min)

One call for all 1–3 notes. Order of operations:

1. Cache lookup on `(normalise_cache_key(note) for each note, capacity)`. Hidden
   cases reuse wordings and the judge repeats requests — a hit is free latency.
2. Primary provider, one retry on transient failure.
3. Fallback provider.
4. Raise `InterpretationUnavailable`; Member A catches it and calls your regex
   interpreter.

Return raw dicts exactly as `docs/02-contracts.md` specifies — **do not validate
them**. That is Member C's module, deliberately, so that untrusted output has
exactly one place it can be laundered.

`extract_json` must survive ```` ```json ```` fences, a leading "Here is the
interpretation:", and trailing commentary. Find the outermost balanced `{…}`.

## Task B4 — regex fallback (~30 min, do it after B3 works)

`fallback.rule_based_interpret` keeps the service answering when every provider
is down. Cover:

- windows: `from 2 AM until 5 AM`, `between 13:00 and 15:00`, `1-3 PM`, `noon`,
  `midnight`, bare `14:00`;
- fractions: `80% reduction` → 0.2, `drop to 25%` → 0.25, `half`, `one-fifth`,
  `a quarter`, `two-thirds`;
- verbs: charge/charging → `no_charge_window`, discharge → `no_discharge_window`,
  `keep at least N kWh` / `N% of capacity` → reserve, `must not exceed N kWh` /
  `stay at or below` → `max_grid_window`;
- everything unmatched → `no_op`.

Mark these `source="fallback"` and say so in the explanation. It is a safety net,
not the interpreter — the Guide penalises phrase matching as the *sole* path.

## Task B5 — the paraphrase bank (~ongoing, this is your test set)

`harness/paraphrase_bank.json` ships with six seeds. Grow it to **≥ 12 wordings
per directive type**, plus 8 distractors. Vary: 12-hour vs 24-hour clocks, "from
X to Y" vs "between X and Y" vs "X–Y", percentages vs fractions vs words,
technical register ("PV output", "inverter", "feeder") vs plain campus English.

Run it after **every** prompt edit. A prompt change that fixes one wording and
breaks three is the specific way this category is lost, and you will not notice
without the bank.

---

## Definition of done

- [ ] all 10 public sample notes resolve to the expected type, hours and numbers
- [ ] ≥ 12 paraphrases per directive type in the bank, ≥ 95% correct
- [ ] 8 distractors all → `no_op`
- [ ] wrap-around window returns ascending hours
- [ ] "80% reduction" and "drops to 20%" both → `0.2`
- [ ] "50% of capacity" resolves against the request's `capacity_kwh`
- [ ] revoking the primary key still yields a correct interpretation via failover
- [ ] both providers down → regex fallback, `source="fallback"`, no exception escapes
- [ ] second identical request is a cache hit
- [ ] no key appears in any log line or error message

## Agent prompt

> Read `docs/00-spec-digest.md`, `docs/05-llm-interpretation.md`,
> `docs/02-contracts.md` and `docs/team/MEMBER-B-llm-interpretation.md` in this
> repo. Implement `app/llm/prompts.py`, `app/llm/client.py`,
> `app/llm/interpreter.py` and `app/llm/fallback.py` to the contract there. Only
> edit files under `app/llm/` plus `harness/paraphrase_bank.json`. Do not
> validate or normalise the model output — `app/guardrails.py` owns that and is
> Member C's file. Then extend the paraphrase bank to 12 wordings per directive
> type and report the accuracy per type.
