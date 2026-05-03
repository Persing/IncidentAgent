# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
Embedding provider factory.

Returns a LangChain-compatible Embeddings instance based on the configured
EMBEDDING_PROVIDER setting. All providers implement the same interface
(langchain_core.embeddings.Embeddings), so the rest of the codebase
is provider-agnostic.

Supported providers:
  openai      — OpenAI text-embedding-3-small (or configured model)
  ollama      — Any Ollama-hosted model (default: nomic-embed-text)
  huggingface — Local sentence-transformers model (default: BAAI/bge-small-en-v1.5)

Provider packages are imported lazily so you only need the package for
the provider you actually use. Install via:
  uv pip install -e ".[openai]"
  uv pip install -e ".[ollama]"
  uv pip install -e ".[huggingface]"
"""

from __future__ import annotations

from langchain_core.embeddings import Embeddings

from src.config.settings import Settings


def get_embedding_provider(settings: Settings) -> Embeddings:
    """
    Instantiate and return the configured embedding provider.

    Args:
        settings: Application settings. Typically obtained via get_settings().

    Returns:
        A LangChain Embeddings instance.

    Raises:
        ImportError:  The required provider package is not installed.
        ValueError:   An unknown provider name was specified.
    """
    provider = settings.embedding_provider

    if provider == "openai":
        return _openai_embeddings(settings)
    elif provider == "ollama":
        return _ollama_embeddings(settings)
    elif provider == "huggingface":
        return _huggingface_embeddings(settings)
    else:
        raise ValueError(
            f"Unknown embedding provider: '{provider}'. "
            "Valid options: openai, ollama, huggingface"
        )


# ── Private provider constructors ────────────────────────────────────────────


def _openai_embeddings(settings: Settings) -> Embeddings:
    try:
        from langchain_openai import OpenAIEmbeddings
    except ImportError as e:
        raise ImportError(
            "langchain-openai is required for EMBEDDING_PROVIDER=openai. "
            "Install it with:  uv pip install -e '.[openai]'"
        ) from e

    return OpenAIEmbeddings(
        model=settings.openai_embedding_model,
        api_key=settings.openai_api_key,
    )


def _ollama_embeddings(settings: Settings) -> Embeddings:
    try:
        from langchain_ollama import OllamaEmbeddings
    except ImportError as e:
        raise ImportError(
            "langchain-ollama is required for EMBEDDING_PROVIDER=ollama. "
            "Install it with:  uv pip install -e '.[ollama]'"
        ) from e

    return OllamaEmbeddings(
        model=settings.ollama_embedding_model,
        base_url=settings.ollama_base_url,
    )


def _huggingface_embeddings(settings: Settings) -> Embeddings:
    try:
        from langchain_huggingface import HuggingFaceEmbeddings
    except ImportError as e:
        raise ImportError(
            "langchain-huggingface and sentence-transformers are required for "
            "EMBEDDING_PROVIDER=huggingface. "
            "Install with:  uv pip install -e '.[huggingface]'"
        ) from e

    # encode_kwargs: normalize embeddings so cosine similarity == dot product,
    # which is what ChromaDB uses by default.
    return HuggingFaceEmbeddings(
        model_name=settings.hf_embedding_model,
        encode_kwargs={"normalize_embeddings": True},
    )
