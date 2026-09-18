"""
Provider-agnostic chat client.  OWNER: Ninad — Lane B.

Why an abstraction: the round is four hours and provider outages happen. The
service must survive its primary model going down without losing the LLM from
the interpretation path, so we support a primary and an optional secondary
provider, both configured purely from environment variables.

Supported `LLM_PROVIDER` values: "openai", "groq", "gemini", "openai_compatible"
(anything exposing /v1/chat/completions; set LLM_BASE_URL).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class LLMError(RuntimeError):
    """Any provider-side failure: timeout, 4xx, 5xx, unparseable body.

    The message must never contain the API key or the raw Authorization header.
    """


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    latency_ms: float


class LLMClient:
    """One configured provider."""

    def __init__(
        self,
        provider: str,
        model: str,
        api_key: str,
        base_url: str = "",
        timeout_s: float = 8.0,
        temperature: float = 0.0,
    ) -> None:
        raise NotImplementedError("TODO(Ninad)")

    async def complete_json(
        self,
        system: str,
        user: str,
        schema: Optional[Dict[str, Any]] = None,
        few_shot: Optional[List[dict]] = None,
    ) -> LLMResult:
        """One JSON-mode completion. Raises LLMError on any failure.

        Use the provider's native structured-output / JSON mode when available
        (OpenAI `response_format`, Gemini `responseSchema`); otherwise fall back
        to a JSON-only instruction plus tolerant extraction in interpreter.py.
        """
        raise NotImplementedError("TODO(Ninad)")


def build_primary() -> Optional[LLMClient]:
    """Client from LLM_* settings, or None when no key is configured."""
    raise NotImplementedError("TODO(Ninad)")


def build_fallback() -> Optional[LLMClient]:
    """Client from LLM_FALLBACK_* settings, or None."""
    raise NotImplementedError("TODO(Ninad)")
