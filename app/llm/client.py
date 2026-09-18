"""
Provider-agnostic chat client.  OWNER: Ninad — Lane B.

Why an abstraction: the round is four hours and provider outages happen. The
service must survive its primary model going down without losing the LLM from
the interpretation path, so we support a primary and an optional secondary
provider, both configured purely from environment variables.

Supported `LLM_PROVIDER` values: "openai", "groq", "gemini", "openai_compatible"
(anything exposing /v1/chat/completions; set LLM_BASE_URL).

Secret hygiene (Guide §04, Problem Statement §06.1): the API key travels in a
header, never in a URL or a log line, and every error raised from this module is
passed through `_redact` before it leaves. A traceback from here can be logged
safely.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from app.config import get_settings

# One extraction task, at most three short notes. A small cap keeps latency down
# (3 of the 10 reliability points are p95 <= 5 s) and stops a confused model from
# rambling past the JSON.
MAX_TOKENS = 900

# How much of a failing provider response we are willing to put in a log line.
_BODY_SNIPPET = 200

DEFAULT_BASE_URLS: Dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
}

# Providers that speak the OpenAI /chat/completions wire format.
_OPENAI_STYLE = {"openai", "groq", "openai_compatible"}

# Providers with real constrained decoding against a JSON schema. Everywhere
# else we ask for a JSON object and lean on interpreter.extract_json.
_NATIVE_SCHEMA = {"openai", "gemini"}


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
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.provider = (provider or "").strip().lower()
        self.model = (model or "").strip()
        self._api_key = (api_key or "").strip()
        self.timeout_s = float(timeout_s)
        self.temperature = float(temperature)
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None

        if self.provider not in _OPENAI_STYLE and self.provider != "gemini":
            raise LLMError(f"unsupported LLM_PROVIDER: {self.provider!r}")
        if not self.model:
            raise LLMError(f"no model configured for provider {self.provider!r}")
        if not self._api_key:
            raise LLMError(f"no API key configured for provider {self.provider!r}")

        base = (base_url or "").strip() or DEFAULT_BASE_URLS.get(self.provider, "")
        if not base:
            raise LLMError(
                f"provider {self.provider!r} needs LLM_BASE_URL to be set"
            )
        self.base_url = base.rstrip("/")

    # -- lifecycle ---------------------------------------------------------

    def _http(self) -> httpx.AsyncClient:
        """Lazily built, then reused: a warm connection pool is worth ~100 ms
        per request, and the judge sends many requests in a row."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    self.timeout_s, connect=min(5.0, self.timeout_s)
                ),
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    # -- the one public call ----------------------------------------------

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
        if self.provider == "gemini":
            url, headers, payload = self._gemini_request(system, user, schema, few_shot)
        else:
            url, headers, payload = self._openai_request(system, user, schema, few_shot)

        started = time.perf_counter()
        try:
            response = await self._http().post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise LLMError(
                f"{self.provider}: timed out after {self.timeout_s:g}s"
            ) from _scrub(exc, self._api_key)
        except httpx.HTTPError as exc:
            raise LLMError(
                f"{self.provider}: transport error: {_redact(type(exc).__name__ + ': ' + str(exc), self._api_key)}"
            ) from _scrub(exc, self._api_key)
        latency_ms = (time.perf_counter() - started) * 1000.0

        if response.status_code >= 400:
            raise LLMError(
                f"{self.provider}: HTTP {response.status_code}: "
                f"{_redact(response.text[:_BODY_SNIPPET], self._api_key)}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise LLMError(
                f"{self.provider}: response was not JSON: "
                f"{_redact(response.text[:_BODY_SNIPPET], self._api_key)}"
            ) from _scrub(exc, self._api_key)

        text = (
            self._gemini_text(body)
            if self.provider == "gemini"
            else self._openai_text(body)
        )
        if not text.strip():
            raise LLMError(f"{self.provider}: empty completion")

        return LLMResult(
            text=text,
            provider=self.provider,
            model=self.model,
            latency_ms=latency_ms,
        )

    # -- OpenAI / Groq / anything OpenAI-compatible -------------------------

    def _openai_request(
        self,
        system: str,
        user: str,
        schema: Optional[Dict[str, Any]],
        few_shot: Optional[List[dict]],
    ) -> tuple[str, Dict[str, str], Dict[str, Any]]:
        messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
        for shot in few_shot or []:
            messages.append({"role": "user", "content": shot["user"]})
            messages.append({"role": "assistant", "content": shot["assistant"]})
        messages.append({"role": "user", "content": user})

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": MAX_TOKENS,
        }

        if schema is not None and self.provider in _NATIVE_SCHEMA:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "operator_note_interpretations",
                    "strict": True,
                    "schema": schema,
                },
            }
        else:
            # Groq and self-hosted OpenAI-compatible endpoints: plain JSON mode
            # plus the shape spelled out in the system prompt.
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        return f"{self.base_url}/chat/completions", headers, payload

    @staticmethod
    def _openai_text(body: Dict[str, Any]) -> str:
        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected completion shape: {_keys(body)}") from exc
        if message.get("refusal"):
            raise LLMError("provider refused the request")
        content = message.get("content")
        if isinstance(content, list):  # some gateways return content parts
            content = "".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        return content or ""

    # -- Gemini -------------------------------------------------------------

    def _gemini_request(
        self,
        system: str,
        user: str,
        schema: Optional[Dict[str, Any]],
        few_shot: Optional[List[dict]],
    ) -> tuple[str, Dict[str, str], Dict[str, Any]]:
        contents: List[Dict[str, Any]] = []
        for shot in few_shot or []:
            contents.append({"role": "user", "parts": [{"text": shot["user"]}]})
            contents.append({"role": "model", "parts": [{"text": shot["assistant"]}]})
        contents.append({"role": "user", "parts": [{"text": user}]})

        generation: Dict[str, Any] = {
            "temperature": self.temperature,
            "maxOutputTokens": MAX_TOKENS,
            "responseMimeType": "application/json",
        }
        if schema is not None:
            generation["responseSchema"] = to_gemini_schema(schema)

        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": generation,
        }
        # Key goes in a header, never in the query string: URLs end up in logs,
        # proxies and error messages.
        headers = {
            "x-goog-api-key": self._api_key,
            "Content-Type": "application/json",
        }
        return (
            f"{self.base_url}/models/{self.model}:generateContent",
            headers,
            payload,
        )

    @staticmethod
    def _gemini_text(body: Dict[str, Any]) -> str:
        try:
            parts = body["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            feedback = (body or {}).get("promptFeedback") if isinstance(body, dict) else None
            if feedback:
                raise LLMError(f"gemini blocked the prompt: {json.dumps(feedback)[:_BODY_SNIPPET]}") from exc
            raise LLMError(f"unexpected completion shape: {_keys(body)}") from exc
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict))


# --------------------------------------------------------------------------
# Schema translation
# --------------------------------------------------------------------------


def to_gemini_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Translate our JSON Schema into the OpenAPI subset Gemini accepts.

    Gemini has no `additionalProperties` and expresses nullability with a
    `nullable` flag rather than a `["number", "null"]` type union.
    """
    out: Dict[str, Any] = {}
    raw_type = schema.get("type")
    types = [raw_type] if isinstance(raw_type, str) else list(raw_type or [])
    nullable = "null" in types
    concrete = [t for t in types if t != "null"]
    if concrete:
        out["type"] = concrete[0].upper()
    if nullable:
        out["nullable"] = True
    if "enum" in schema:
        out["enum"] = list(schema["enum"])
    if "description" in schema:
        out["description"] = schema["description"]
    if "items" in schema:
        out["items"] = to_gemini_schema(schema["items"])
    if "properties" in schema:
        out["properties"] = {
            name: to_gemini_schema(sub) for name, sub in schema["properties"].items()
        }
        out["propertyOrdering"] = list(schema["properties"].keys())
    if "required" in schema:
        out["required"] = list(schema["required"])
    return out


# --------------------------------------------------------------------------
# Redaction helpers: nothing below may ever emit the key
# --------------------------------------------------------------------------


def _redact(text: str, api_key: str) -> str:
    """Remove the key (and anything that looks like a bearer token) from text."""
    cleaned = " ".join(str(text).split())
    if api_key:
        cleaned = cleaned.replace(api_key, "***")
        if len(api_key) > 8:
            cleaned = cleaned.replace(api_key[:8], "***")
    return cleaned


def _scrub(exc: BaseException, api_key: str) -> BaseException:
    """Return the cause to chain, unless its own text carries the key.

    httpx puts the request URL into some exception strings; if a key ever ends
    up there, we drop the chained cause entirely rather than risk logging it.
    """
    if api_key and api_key in str(exc):
        return None  # type: ignore[return-value]
    return exc


def _keys(body: Any) -> str:
    if isinstance(body, dict):
        return ",".join(sorted(body.keys()))[:_BODY_SNIPPET]
    return type(body).__name__


# --------------------------------------------------------------------------
# Construction from environment
# --------------------------------------------------------------------------

_primary: Optional[LLMClient] = None
_fallback: Optional[LLMClient] = None
_built = False


def build_primary() -> Optional[LLMClient]:
    """Client from LLM_* settings, or None when no key is configured."""
    if not _built:
        _build_clients()
    return _primary


def build_fallback() -> Optional[LLMClient]:
    """Client from LLM_FALLBACK_* settings, or None."""
    if not _built:
        _build_clients()
    return _fallback


def _build_clients() -> None:
    global _primary, _fallback, _built
    settings = get_settings()

    _primary = None
    if settings.has_primary_llm:
        try:
            _primary = LLMClient(
                provider=settings.llm_provider,
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
                timeout_s=settings.llm_timeout_s,
                temperature=settings.llm_temperature,
            )
        except LLMError:
            _primary = None

    _fallback = None
    if settings.has_fallback_llm:
        try:
            _fallback = LLMClient(
                provider=settings.llm_fallback_provider,
                model=settings.llm_fallback_model,
                api_key=settings.llm_fallback_api_key,
                base_url=settings.llm_fallback_base_url,
                timeout_s=settings.llm_timeout_s,
                temperature=settings.llm_temperature,
            )
        except LLMError:
            _fallback = None

    _built = True


def reset_clients() -> None:
    """Drop the cached clients. Used by tests and after a config change."""
    global _primary, _fallback, _built
    _primary = None
    _fallback = None
    _built = False
