# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
LangSmith tracing configuration.

LangChain auto-instruments all LLM calls and retrieval operations when
LANGCHAIN_TRACING_V2=true is set in the environment. This module provides
a single helper that builds a RunnableConfig with consistent metadata so
every trace in the LangSmith dashboard is useful rather than anonymous.

LangGraph propagates the config through all nodes in a graph invocation,
meaning every LLM call and retrieval call within a single triage request
shares the same run_name, tags, and metadata. This makes it easy to find
everything that happened for a specific request.

Without this, the dashboard shows:
  "ChatOllama" | no tags | no metadata

With this, it shows:
  "incident_triage" | tags: [ollama, api] | request_id: abc123 | query: "CrashLoop..."

Usage:
    from src.config.tracing import build_run_config
    from src.config.settings import get_settings

    config = build_run_config(
        settings=get_settings(),
        tags=["api"],
        metadata={"request_id": "abc123", "query_preview": "CrashLoop..."},
    )
    graph.invoke(state, config=config)

When LANGCHAIN_TRACING_V2=false (the default), the RunnableConfig is still
returned and passed through, but no data is sent to LangSmith. No code
changes needed to toggle tracing on/off.
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from src.config.settings import Settings


def build_run_config(
    settings: Settings,
    run_name: str = "incident_triage",
    tags: list[str] | None = None,
    metadata: dict | None = None,
) -> RunnableConfig:
    """
    Build a LangChain RunnableConfig with consistent metadata for tracing.

    Every invocation of the triage graph should use a config built here so
    that all traces share the same structure and are filterable in LangSmith.

    Args:
        settings:  Application settings — used to populate provider tags
                   and metadata automatically.
        run_name:  Display name for this run in the LangSmith UI.
                   Use descriptive names: "incident_triage", "eval_tc_001".
        tags:      Additional tags beyond the auto-populated provider tags.
                   Useful for segmenting traffic: ["api"], ["evaluation"],
                   ["manual"].
        metadata:  Per-request key/value metadata. Common fields:
                     request_id  — UUID for correlating with app logs
                     query_preview — first ~100 chars of the alert text
                     test_case_id  — for eval runs

    Returns:
        A RunnableConfig ready to pass as the second argument to
        graph.invoke(state, config=...).
    """
    # Auto-populate provider tags — makes it easy to filter by model in LangSmith
    provider_tags = [
        f"embed:{settings.embedding_provider}",
        f"llm:{settings.llm_provider}",
    ]

    base_metadata: dict = {
        "embedding_provider": settings.embedding_provider,
        "embedding_model": _embedding_model(settings),
        "llm_provider": settings.llm_provider,
        "llm_model": _llm_model(settings),
    }

    return RunnableConfig(
        run_name=run_name,
        tags=provider_tags + (tags or []),
        metadata={**base_metadata, **(metadata or {})},
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _embedding_model(settings: Settings) -> str:
    if settings.embedding_provider == "openai":
        return settings.openai_embedding_model
    if settings.embedding_provider == "ollama":
        return settings.ollama_embedding_model
    if settings.embedding_provider == "huggingface":
        return settings.hf_embedding_model
    return "unknown"


def _llm_model(settings: Settings) -> str:
    if settings.llm_provider == "openai":
        return settings.openai_llm_model
    if settings.llm_provider == "anthropic":
        return settings.anthropic_model
    if settings.llm_provider == "ollama":
        return settings.ollama_llm_model
    return "unknown"
