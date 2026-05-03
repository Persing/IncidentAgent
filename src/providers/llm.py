# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
LLM provider factory.

Returns a LangChain-compatible BaseChatModel instance based on the configured
LLM_PROVIDER setting. All providers implement the same interface
(langchain_core.language_models.BaseChatModel), so the agent and chain code
is provider-agnostic.

Supported providers:
  openai     — OpenAI GPT-4o (or configured model)
  anthropic  — Anthropic Claude 3.5 Sonnet (or configured model)
  ollama     — Any Ollama-hosted model (default: llama3.1)

Provider packages are imported lazily so you only need the package for
the provider you actually use. Install via:
  uv pip install -e ".[openai]"
  uv pip install -e ".[anthropic]"
  uv pip install -e ".[ollama]"
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from src.config.settings import Settings


def get_llm_provider(settings: Settings) -> BaseChatModel:
    """
    Instantiate and return the configured LLM provider.

    Args:
        settings: Application settings. Typically obtained via get_settings().

    Returns:
        A LangChain BaseChatModel instance.

    Raises:
        ImportError:  The required provider package is not installed.
        ValueError:   An unknown provider name was specified.
    """
    provider = settings.llm_provider

    if provider == "openai":
        return _openai_llm(settings)
    elif provider == "anthropic":
        return _anthropic_llm(settings)
    elif provider == "ollama":
        return _ollama_llm(settings)
    else:
        raise ValueError(
            f"Unknown LLM provider: '{provider}'. "
            "Valid options: openai, anthropic, ollama"
        )


# ── Private provider constructors ────────────────────────────────────────────


def _openai_llm(settings: Settings) -> BaseChatModel:
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as e:
        raise ImportError(
            "langchain-openai is required for LLM_PROVIDER=openai. "
            "Install it with:  uv pip install -e '.[openai]'"
        ) from e

    return ChatOpenAI(
        model=settings.openai_llm_model,
        api_key=settings.openai_api_key,
        temperature=0,  # Deterministic output for triage plans
    )


def _anthropic_llm(settings: Settings) -> BaseChatModel:
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as e:
        raise ImportError(
            "langchain-anthropic is required for LLM_PROVIDER=anthropic. "
            "Install it with:  uv pip install -e '.[anthropic]'"
        ) from e

    return ChatAnthropic(
        model=settings.anthropic_model,
        api_key=settings.anthropic_api_key,
        temperature=0,  # Deterministic output for triage plans
        max_tokens=4096,
    )


def _ollama_llm(settings: Settings) -> BaseChatModel:
    try:
        from langchain_ollama import ChatOllama
    except ImportError as e:
        raise ImportError(
            "langchain-ollama is required for LLM_PROVIDER=ollama. "
            "Install it with:  uv pip install -e '.[ollama]'"
        ) from e

    return ChatOllama(
        model=settings.ollama_llm_model,
        base_url=settings.ollama_base_url,
        temperature=0,
    )
