# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
Application settings loaded from environment variables / .env file.

Usage:
    from src.config.settings import get_settings

    settings = get_settings()
    print(settings.embedding_provider)

Settings are cached after the first call via @lru_cache, so the .env file
is only read once per process. To reload (e.g., in tests), call
get_settings.cache_clear() before the next call.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Allow extra env vars without raising an error
        extra="ignore",
    )

    # ── Provider selection ────────────────────────────────────
    embedding_provider: Literal["openai", "ollama", "huggingface"] = Field(
        default="openai",
        description="Which provider to use for text embeddings.",
    )
    llm_provider: Literal["openai", "anthropic", "ollama"] = Field(
        default="openai",
        description="Which provider to use for the language model.",
    )

    # ── OpenAI ───────────────────────────────────────────────
    openai_api_key: str = Field(default="", repr=False)
    openai_embedding_model: str = "text-embedding-3-small"
    openai_llm_model: str = "gpt-4o"

    # ── Anthropic ────────────────────────────────────────────
    anthropic_api_key: str = Field(default="", repr=False)
    anthropic_model: str = "claude-3-5-sonnet-20241022"

    # ── Ollama ───────────────────────────────────────────────
    ollama_base_url: str = "http://localhost:11434"
    ollama_embedding_model: str = "nomic-embed-text"
    ollama_llm_model: str = "llama3.1"

    # ── HuggingFace / local sentence-transformers ─────────────
    hf_embedding_model: str = "BAAI/bge-small-en-v1.5"

    # ── ChromaDB ─────────────────────────────────────────────
    chroma_persist_dir: str = "data/chroma_db"
    chroma_collection_name: str = "runbooks"

    # ── Data paths ───────────────────────────────────────────
    runbooks_dir: str = "data/runbooks"

    # ── Logging ──────────────────────────────────────────────
    log_level: str = Field(
        default="INFO",
        description="Root log level: DEBUG, INFO, WARNING, ERROR, CRITICAL.",
    )

    # ── LangSmith ────────────────────────────────────────────
    langchain_tracing_v2: bool = False
    langchain_api_key: str = Field(default="", repr=False)
    langchain_project: str = "incident-triage-agent"

    @model_validator(mode="after")
    def _validate_provider_keys(self) -> Settings:
        """Fail early with a clear message if a required API key is missing."""
        if self.embedding_provider == "openai" and not self.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY is required when EMBEDDING_PROVIDER=openai. "
                "Set it in your .env file or switch to EMBEDDING_PROVIDER=ollama|huggingface."
            )
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY is required when LLM_PROVIDER=openai."
            )
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic. "
                "Set it in your .env file or switch providers."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings singleton."""
    return Settings()
