"""
Runtime configuration. Every value comes from an environment variable so that
nothing secret is ever committed. `.env.example` documents the names; the real
`.env` is git-ignored.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # --- service -----------------------------------------------------------
    port: int = _env_int("PORT", 8000)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # --- LLM provider ------------------------------------------------------
    # Primary provider: "openai" | "groq" | "gemini" | "openai_compatible"
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o-mini")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")

    # Optional second provider, tried when the primary fails.
    llm_fallback_provider: str = os.getenv("LLM_FALLBACK_PROVIDER", "")
    llm_fallback_model: str = os.getenv("LLM_FALLBACK_MODEL", "")
    llm_fallback_base_url: str = os.getenv("LLM_FALLBACK_BASE_URL", "")
    llm_fallback_api_key: str = os.getenv("LLM_FALLBACK_API_KEY", "")

    llm_timeout_s: float = _env_float("LLM_TIMEOUT_S", 8.0)
    llm_max_retries: int = _env_int("LLM_MAX_RETRIES", 1)
    llm_temperature: float = _env_float("LLM_TEMPERATURE", 0.0)
    llm_cache_size: int = _env_int("LLM_CACHE_SIZE", 512)

    # --- pipeline budgets --------------------------------------------------
    # Hard wall for the whole POST /optimize-energy handler. The judge fails a
    # request at 30 s, so we must answer with *something* well before that.
    request_budget_s: float = _env_float("REQUEST_BUDGET_S", 22.0)

    # Set to 1 only for offline development when no provider key is available.
    # The Problem Statement requires the LLM in the interpretation path, so this
    # MUST be 0 on the submitted deployment.
    allow_no_llm: bool = _env_bool("ALLOW_NO_LLM", False)

    @property
    def has_primary_llm(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def has_fallback_llm(self) -> bool:
        return bool(self.llm_fallback_api_key and self.llm_fallback_provider)

    def redacted(self) -> dict:
        """Safe-to-log view. Never log the raw Settings object."""
        return {
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "llm_fallback_provider": self.llm_fallback_provider or None,
            "llm_timeout_s": self.llm_timeout_s,
            "request_budget_s": self.request_budget_s,
            "primary_key_present": self.has_primary_llm,
            "fallback_key_present": self.has_fallback_llm,
        }


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


SECRET_ENV_NAMES: List[str] = [
    "LLM_API_KEY",
    "LLM_FALLBACK_API_KEY",
]
