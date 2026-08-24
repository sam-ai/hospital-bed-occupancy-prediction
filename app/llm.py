"""Shared LLM factory for the hospital AI agent pipeline.

Provides a single get_llm() entry point that returns the first available
LLM instance, preferring the OpenAI-compatible API (Baseten/DeepSeek) and
falling back to Google Gemini. Returns None if no LLM is configured.

Usage:
    from app.llm import get_llm

    llm = get_llm()
    if llm:
        response = await llm.ainvoke("your prompt")
"""

from __future__ import annotations

from typing import Any

from app.config import (
    GOOGLE_API_KEY,
    GOOGLE_MODEL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    USE_LLM,
)

# Module-level cached LLM instance (lazy — only calls API when invoked)
_cached_llm: Any | None = None
_initialized = False


def _build_openai_compatible():
    """Initialize OpenAI-compatible LLM (e.g. Baseten, DeepSeek) if configured."""
    if not USE_LLM or not OPENAI_API_KEY:
        return None
    try:
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=OPENAI_MODEL,
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_BASE_URL,
            temperature=0.1,
        )
    except Exception:
        return None


def _build_gemini():
    """Initialize Gemini LLM if configured and available."""
    if not USE_LLM or not GOOGLE_API_KEY:
        return None
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=GOOGLE_MODEL,
            google_api_key=GOOGLE_API_KEY,
            temperature=0.1,
        )
    except Exception:
        return None


def get_llm():
    """Return the first available LLM, preferring OpenAI-compatible API.

    The result is cached after the first call so subsequent imports across
    agents reuse the same instance.
    """
    global _cached_llm, _initialized

    if _initialized:
        return _cached_llm

    _initialized = True
    _cached_llm = _build_openai_compatible() or _build_gemini()
    return _cached_llm
