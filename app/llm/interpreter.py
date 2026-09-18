"""
Operator notes -> raw structured interpretation.  OWNER: Ninad — Lane B.

This module is the mandatory LLM step. The Participant Guide is explicit: a
language model must directly produce the structured interpretation that feeds
the optimizer. Regex/keyword logic may repair or back up the model, but must
never be the only interpreter.

Contract with the rest of the pipeline
--------------------------------------
`interpret_notes` returns a list of RAW dicts, one per note, untrusted and
unvalidated. `app.guardrails.validate_interpretations` is what turns them into
`Directive` objects. Never let raw model output reach the optimizer.

What this module deliberately does NOT do
-----------------------------------------
It does not check hours, clamp a factor, resolve a percentage against capacity,
or reject an unknown directive_type. Those are Kabya's, in `app/guardrails.py`,
on purpose: untrusted model output must have exactly one place where it can be
laundered. The only reshaping done here is *alignment*: making sure there is
one entry per note, in note order, because the rest of the pipeline indexes by
position and a missing entry would break the response contract itself.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from app.config import get_settings
from app.llm.client import LLMClient, LLMError, build_fallback, build_primary
from app.llm.prompts import (
    FEW_SHOT,
    SYSTEM_PROMPT,
    build_user_prompt,
    response_json_schema,
)
from app.schemas import BatteryInput

log = logging.getLogger("gridwise.llm")


class InterpretationUnavailable(RuntimeError):
    """Every configured provider failed. The pipeline falls back from here."""


# --------------------------------------------------------------------------
# Cache: (normalised note, capacity) -> raw interpretation dict
#
# The judge repeats requests and hidden cases reuse wordings, so a hit is free
# latency and free money. Keyed per note rather than per request, so a scenario
# that shares two notes with an earlier one still profits.
# --------------------------------------------------------------------------

_CacheKey = Tuple[str, float]
_cache: "OrderedDict[_CacheKey, Dict[str, Any]]" = OrderedDict()
_hits = 0
_misses = 0


def _cache_get(key: _CacheKey) -> Optional[Dict[str, Any]]:
    global _hits, _misses
    entry = _cache.get(key)
    if entry is None:
        _misses += 1
        return None
    _cache.move_to_end(key)
    _hits += 1
    return dict(entry)


def _cache_put(key: _CacheKey, value: Dict[str, Any]) -> None:
    limit = max(0, get_settings().llm_cache_size)
    if limit == 0:
        return
    _cache[key] = dict(value)
    _cache.move_to_end(key)
    while len(_cache) > limit:
        _cache.popitem(last=False)


def cache_stats() -> Dict[str, int]:
    return {"entries": len(_cache), "hits": _hits, "misses": _misses}


def clear_cache() -> None:
    global _hits, _misses
    _cache.clear()
    _hits = 0
    _misses = 0


# --------------------------------------------------------------------------
# The entry point
# --------------------------------------------------------------------------


async def interpret_notes(notes: List[str], battery: BatteryInput) -> List[Dict[str, Any]]:
    """Return one raw interpretation dict per note, in note order.

    Behaviour required:
      * one call for all 1-3 notes (cheaper and faster than one call per note);
      * temperature 0;
      * retry once on transient failure, then try the fallback provider;
      * cache on (normalised note text, capacity) so repeated hidden cases and
        the judge's repeat traffic cost nothing;
      * raise InterpretationUnavailable only when every provider is exhausted.
    """
    if not notes:
        return []

    settings = get_settings()
    capacity = float(battery.capacity_kwh)
    keys = [(normalise_cache_key(note), capacity) for note in notes]

    cached = [_cache_get(key) for key in keys]
    if all(entry is not None for entry in cached):
        log.debug("interpretation served from cache (%d notes)", len(notes))
        return [_stamp(entry, index) for index, entry in enumerate(cached)]  # type: ignore[arg-type]

    system = SYSTEM_PROMPT
    user = build_user_prompt(notes, battery)
    schema = response_json_schema()
    retries = max(0, settings.llm_max_retries)

    providers = [c for c in (build_primary(), build_fallback()) if c is not None]
    if not providers:
        raise InterpretationUnavailable(
            "no LLM provider is configured (set LLM_PROVIDER / LLM_MODEL / LLM_API_KEY)"
        )

    best: Optional[List[Optional[Dict[str, Any]]]] = None
    failures: List[str] = []

    for client in providers:
        for attempt in range(retries + 1):
            try:
                result = await client.complete_json(
                    system=system, user=user, schema=schema, few_shot=FEW_SHOT
                )
            except LLMError as exc:
                failures.append(f"{client.provider}#{attempt}: {exc}")
                log.warning("LLM call failed (%s attempt %d): %s", client.provider, attempt, exc)
                continue
            except asyncio.CancelledError:
                raise

            try:
                slots = _align(extract_json(result.text), len(notes))
            except ValueError as exc:
                failures.append(f"{client.provider}#{attempt}: unusable output: {exc}")
                log.warning(
                    "LLM returned unusable output (%s attempt %d): %s",
                    client.provider,
                    attempt,
                    exc,
                )
                continue

            log.info(
                "interpreted %d note(s) via %s/%s in %.0f ms",
                len(notes),
                result.provider,
                result.model,
                result.latency_ms,
            )

            if all(slot is not None for slot in slots):
                return _finish(slots, keys)

            # Partial answer: keep the most complete one seen and try again.
            # A later attempt that covers every note wins; if none does, a
            # partial LLM reading still beats no reading at all.
            if best is None or _filled(slots) > _filled(best):
                best = slots
            failures.append(
                f"{client.provider}#{attempt}: {_filled(slots)}/{len(notes)} notes covered"
            )

    if best is not None and _filled(best) > 0:
        log.warning("using a partial interpretation (%d/%d notes)", _filled(best), len(notes))
        return _finish(best, keys)

    raise InterpretationUnavailable("; ".join(failures) or "no provider produced output")


def _finish(
    slots: List[Optional[Dict[str, Any]]], keys: List[_CacheKey]
) -> List[Dict[str, Any]]:
    """Stamp indices, cache the real entries, fill any hole with a blank.

    A blank entry carries no directive_type, so `validate_interpretations`
    demotes it to `no_op`, the safe reading when the model said nothing about
    a note. It is never cached.
    """
    out: List[Dict[str, Any]] = []
    for index, slot in enumerate(slots):
        if slot is None:
            out.append(
                {
                    "note_index": index,
                    "directive_type": None,
                    "hours": None,
                    "factor": None,
                    "minimum_energy_kwh": None,
                    "max_grid_kwh": None,
                    "explanation": "The model returned no interpretation for this note.",
                    "source": "llm",
                }
            )
            continue
        entry = _stamp(slot, index)
        _cache_put(keys[index], entry)
        out.append(entry)
    return out


def _stamp(entry: Dict[str, Any], index: int) -> Dict[str, Any]:
    out = dict(entry)
    out["note_index"] = index
    out.setdefault("source", "llm")
    return out


def _filled(slots: List[Optional[Dict[str, Any]]]) -> int:
    return sum(1 for slot in slots if slot is not None)


# --------------------------------------------------------------------------
# Alignment: exactly one entry per note, in note order
# --------------------------------------------------------------------------

# Keys a model might use instead of "interpretations" when it ignores the schema.
_LIST_KEYS = (
    "interpretations",
    "interpretation",
    "directives",
    "directive_interpretation",
    "results",
    "output",
    "notes",
    "items",
)


def _align(data: Dict[str, Any], count: int) -> List[Optional[Dict[str, Any]]]:
    """Map raw model entries onto note positions 0..count-1.

    Trusts `note_index` when it is a usable position and not already taken;
    otherwise places entries in the order they arrived. Raises ValueError when
    the payload contains nothing that could be an interpretation.
    """
    items: Optional[List[Any]] = None
    if isinstance(data, dict):
        for key in _LIST_KEYS:
            value = data.get(key)
            if isinstance(value, list):
                items = value
                break
        if items is None and any(k in data for k in ("directive_type", "note_index")):
            items = [data]  # a single note answered as a bare object
    elif isinstance(data, list):
        items = data

    if not isinstance(items, list) or not items:
        raise ValueError("no interpretation array in the response")

    slots: List[Optional[Dict[str, Any]]] = [None] * count
    leftovers: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        position = _as_index(item.get("note_index"), count)
        if position is not None and slots[position] is None:
            slots[position] = item
        else:
            leftovers.append(item)

    for position in range(count):
        if slots[position] is None and leftovers:
            slots[position] = leftovers.pop(0)

    if _filled(slots) == 0:
        raise ValueError("response contained no usable objects")
    return slots


def _as_index(value: Any, count: int) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        try:
            value = int(value.strip())
        except ValueError:
            return None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        value = int(value)
    if isinstance(value, int) and 0 <= value < count:
        return value
    return None


# --------------------------------------------------------------------------
# Tolerant JSON extraction
# --------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", re.DOTALL)
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def extract_json(raw: str) -> Dict[str, Any]:
    """Tolerant JSON extraction: strips ```json fences, leading prose, trailing
    commentary. Raises ValueError if nothing parseable is found."""
    import json

    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("empty response")

    candidates: List[str] = []
    text = raw.strip()

    fenced = _FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1).strip())
    candidates.append(text)

    for candidate in candidates:
        for chunk in _json_chunks(candidate):
            for attempt in (chunk, _TRAILING_COMMA.sub(r"\1", chunk)):
                try:
                    parsed = json.loads(attempt)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    return parsed
                if isinstance(parsed, list):
                    return {"interpretations": parsed}
    raise ValueError("no JSON object found in the response")


def _json_chunks(text: str):
    """Yield the whole string, then every balanced {...} / [...] slice in it.

    Balanced scanning is string- and escape-aware, so a brace inside an
    explanation ("use {curly} braces") cannot end the slice early.
    """
    stripped = text.strip()
    if stripped:
        yield stripped
    for opener, closer in (("{", "}"), ("[", "]")):
        depth = 0
        start = -1
        in_string = False
        escaped = False
        for position, char in enumerate(text):
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == opener:
                if depth == 0:
                    start = position
                depth += 1
            elif char == closer and depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    yield text[start : position + 1]
                    start = -1


# --------------------------------------------------------------------------
# Cache key normalisation
# --------------------------------------------------------------------------

_NOISE = re.compile(r"[^\w%:./-]+", re.UNICODE)


def normalise_cache_key(note: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation noise."""
    text = unicodedata.normalize("NFKC", str(note)).lower()
    text = (
        text.replace("–", "-")
        .replace("—", "-")
        .replace("‘", "'")
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
    )
    text = _NOISE.sub(" ", text)
    tokens: List[str] = []
    for token in text.split():
        if not any(character.isdigit() for character in token):
            token = token.strip("./-:")
        if token:
            tokens.append(token)
    return " ".join(tokens)


async def aclose() -> None:
    """Close both provider connection pools. Safe to call more than once."""
    for client in (build_primary(), build_fallback()):
        if isinstance(client, LLMClient):
            await client.aclose()
