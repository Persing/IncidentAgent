# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
LocalFileSource — loads runbook chunks from local Markdown files on disk.

This wraps the existing parse_runbook() parser from src.ingestion.loader.
It is responsible only for the fetch step; embedding and ChromaDB storage
remain in ingest_runbooks() which calls this plugin.

Config dict keys:
    runbooks_dir (str): path to the directory containing .md files.
                        Defaults to "data/runbooks".
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.ingestion.loader import RunbookChunk, parse_runbook
from src.plugins.base import SourcePlugin
from src.plugins.registry import registry

logger = logging.getLogger(__name__)


@registry.register_source
class LocalFileSource(SourcePlugin):
    name = "local_file"

    def fetch_documents(self, config: dict) -> list[RunbookChunk]:
        runbooks_dir = Path(config.get("runbooks_dir", "data/runbooks"))

        if not runbooks_dir.exists():
            raise FileNotFoundError(
                f"Runbooks directory not found: {runbooks_dir}"
            )

        runbook_files = sorted(runbooks_dir.glob("*.md"))
        if not runbook_files:
            logger.warning("LocalFileSource: no .md files found in %s", runbooks_dir)
            return []

        chunks: list[RunbookChunk] = []
        for path in runbook_files:
            file_chunks = list(parse_runbook(path))
            chunks.extend(file_chunks)
            logger.debug("LocalFileSource: %s → %d chunks", path.name, len(file_chunks))

        logger.info(
            "LocalFileSource: loaded %d chunks from %d files",
            len(chunks),
            len(runbook_files),
        )
        return chunks
