"""
Operator notes -> raw structured interpretation.  OWNER: Member B.

This module is the mandatory LLM step. The Participant Guide is explicit: a
language model must directly produce the structured interpretation that feeds
the optimizer. Regex/keyword logic may repair or back up the model, but must
never be the only interpreter.

Contract with the rest of the pipeline
--------------------------------------
`interpret_notes` returns a list of RAW dicts, one per note, untrusted and
unvalidated. `app.guardrails.validate_interpretations` is what turns them into
`Directive` objects. Never let raw model output reach the optimizer.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.schemas import BatteryInput


class InterpretationUnavailable(RuntimeError):
    """Every configured provider failed. The pipeline falls back from here."""


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
    raise NotImplementedError("TODO(Member B): see docs/team/MEMBER-B-llm-interpretation.md")


def extract_json(raw: str) -> Dict[str, Any]:
    """Tolerant JSON extraction: strips ```json fences, leading prose, trailing
    commentary. Raises ValueError if nothing parseable is found."""
    raise NotImplementedError("TODO(Member B)")


def normalise_cache_key(note: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation noise."""
    raise NotImplementedError("TODO(Member B)")
