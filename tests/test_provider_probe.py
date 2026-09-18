import json

import httpx
import pytest

from harness import provider_probe


@pytest.mark.asyncio
async def test_provider_http_error_is_redacted(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "synthetic-local-canary")
    monkeypatch.setenv("LLM_MODEL", "gemini-3.1-flash-lite")
    original=httpx.AsyncClient
    transport=httpx.MockTransport(lambda req:httpx.Response(429,json={"error":"synthetic-local-canary"}))
    monkeypatch.setattr(provider_probe.httpx,"AsyncClient",lambda **kwargs:original(transport=transport,**kwargs))
    result=await provider_probe.probe("gemini")
    assert result["http_status"] == 429 and not result["passed"]
    assert "synthetic-local-canary" not in json.dumps(result)


@pytest.mark.asyncio
async def test_missing_fallback_never_makes_a_request(monkeypatch):
    monkeypatch.delenv("LLM_FALLBACK_API_KEY",raising=False)
    result=await provider_probe.probe("openrouter")
    assert result["status"] == "missing_key"
