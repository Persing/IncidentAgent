# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
Centralized logging configuration for the Incident Triage Agent.

Call configure_logging() once at application startup (FastAPI lifespan,
CLI entry point, or test fixtures that need structured output).

Third-party loggers (chromadb, httpx, langchain) are quieted to WARNING
so they don't drown out application-level messages at INFO.
"""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    """
    Configure the root logger with a consistent format and level.

    Args:
        level: One of DEBUG, INFO, WARNING, ERROR, CRITICAL.
               Defaults to INFO. Unknown values fall back to INFO.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )

    # Suppress verbose third-party loggers that add noise at INFO.
    for noisy in (
        "chromadb",
        "httpx",
        "httpcore",
        "langchain",
        "langchain_core",
        "openai",
        "anthropic",
        "sentence_transformers",
        "transformers",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
