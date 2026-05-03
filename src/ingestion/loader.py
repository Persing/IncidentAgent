# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
Runbook ingestion pipeline.

Loads Markdown runbooks from disk, splits them into section-level chunks,
embeds each chunk, and stores the result in ChromaDB.

Chunking strategy: split on ## headers rather than by token count.
Each section (Overview, Diagnostic Steps, etc.) is a semantically coherent
unit. This preserves meaning at chunk boundaries and produces cleaner
retrieval results than arbitrary token splits.

Chunk IDs are deterministic: {runbook_stem}__{section_slug}
Re-running the loader on an unchanged runbook is idempotent — ChromaDB
upserts by ID so no duplicates accumulate.

Usage:
    # From the project root:
    python -m src.ingestion.loader

    # Or in code:
    from src.ingestion.loader import ingest_runbooks
    from src.config.settings import get_settings

    stats = ingest_runbooks(get_settings())
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from langchain_chroma import Chroma
from langchain_core.documents import Document

from src.config.settings import Settings, get_settings
from src.providers.embeddings import get_embedding_provider

logger = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class RunbookChunk:
    """A single section from a runbook, ready to be embedded and stored."""

    runbook_name: str        # e.g. "compute-high-cpu"
    section_name: str        # e.g. "Diagnostic Steps"
    content: str             # section header + body text
    family: str              # from ## Tags  (e.g. "compute")
    severity: str            # from ## Tags  (e.g. "high")
    services: str            # from ## Tags  (e.g. "any")
    source_path: str         # relative path to the source file

    @property
    def chunk_id(self) -> str:
        """
        Deterministic ID used as the ChromaDB document ID.
        Allows idempotent upserts — re-ingesting the same runbook
        updates the record rather than creating a duplicate.
        """
        section_slug = re.sub(r"[^a-z0-9]+", "_", self.section_name.lower()).strip("_")
        return f"{self.runbook_name}__{section_slug}"

    def to_document(self) -> Document:
        """Convert to a LangChain Document for storage in ChromaDB."""
        return Document(
            page_content=self.content,
            metadata={
                "runbook": self.runbook_name,
                "section": self.section_name,
                "family": self.family,
                "severity": self.severity,
                "services": self.services,
                "source": self.source_path,
                "chunk_id": self.chunk_id,
            },
        )


@dataclass
class IngestionStats:
    """Summary of a completed ingestion run."""

    runbooks_processed: int = 0
    chunks_upserted: int = 0
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        lines = [
            f"Runbooks processed : {self.runbooks_processed}",
            f"Chunks upserted    : {self.chunks_upserted}",
        ]
        if self.errors:
            lines.append(f"Errors             : {len(self.errors)}")
            for e in self.errors:
                lines.append(f"  - {e}")
        return "\n".join(lines)


# ── Parsing ──────────────────────────────────────────────────────────────────


def _parse_tags(content: str) -> dict[str, str]:
    """
    Extract family, severity, and services from the ## Tags section.

    Expected format (backtick-wrapped key: value pairs):
        `family: compute`
        `severity: high`
        `services: any`
    """
    tags: dict[str, str] = {"family": "unknown", "severity": "unknown", "services": "unknown"}
    tag_pattern = re.compile(r"`(family|severity|services):\s*([^`]+)`")

    for match in tag_pattern.finditer(content):
        key, value = match.group(1), match.group(2).strip()
        tags[key] = value

    return tags


def _split_by_sections(markdown: str) -> list[tuple[str, str]]:
    """
    Split a Markdown document into (section_name, section_body) pairs.

    Sections are delimited by ## headers. The document title (# header)
    is treated as a special "Title" section so it's included in retrieval.
    Content before the first ## is grouped with the title.
    """
    sections: list[tuple[str, str]] = []

    # Split on ## headers (not ### or deeper)
    parts = re.split(r"^(## .+)$", markdown, flags=re.MULTILINE)

    # parts alternates: [preamble, "## Header", body, "## Header", body, ...]
    # parts[0] is everything before the first ## header (includes # title)
    preamble = parts[0].strip()
    if preamble:
        # Extract the # title line for the section name
        title_match = re.match(r"^# (.+)", preamble)
        section_name = title_match.group(1).strip() if title_match else "Overview"
        sections.append((section_name, preamble))

    # Process ## header / body pairs
    for i in range(1, len(parts) - 1, 2):
        header = parts[i].strip()          # e.g. "## Diagnostic Steps"
        body = parts[i + 1].strip()        # section body text

        section_name = header.lstrip("#").strip()

        # Skip the Tags section — it's metadata, not retrieval content
        if section_name.lower() == "tags":
            continue

        if body:
            # Include the header in content for semantic context
            sections.append((section_name, f"{header}\n\n{body}"))

    return sections


def _parse_runbook(path: Path) -> Iterator[RunbookChunk]:
    """
    Parse a single runbook Markdown file into RunbookChunk objects.

    Yields one chunk per non-empty section (excluding ## Tags).
    """
    runbook_name = path.stem  # e.g. "compute-high-cpu"
    source_path = str(path)

    try:
        content = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.error("Failed to read %s: %s", path, e)
        return

    tags = _parse_tags(content)
    sections = _split_by_sections(content)

    for section_name, section_body in sections:
        yield RunbookChunk(
            runbook_name=runbook_name,
            section_name=section_name,
            content=section_body,
            family=tags["family"],
            severity=tags["severity"],
            services=tags["services"],
            source_path=source_path,
        )


# ── Ingestion pipeline ───────────────────────────────────────────────────────


def ingest_runbooks(settings: Settings | None = None) -> IngestionStats:
    """
    Full ingestion pipeline: parse → embed → store.

    Reads all .md files from settings.runbooks_dir, splits them into
    section-level chunks, embeds each chunk, and upserts into ChromaDB.

    Args:
        settings: Application settings. Defaults to get_settings().

    Returns:
        IngestionStats summarising the run.
    """
    if settings is None:
        settings = get_settings()

    stats = IngestionStats()
    runbooks_dir = Path(settings.runbooks_dir)

    if not runbooks_dir.exists():
        raise FileNotFoundError(
            f"Runbooks directory not found: {runbooks_dir}. "
            "Make sure you're running from the project root."
        )

    # Collect all runbook files
    runbook_files = sorted(runbooks_dir.glob("*.md"))
    if not runbook_files:
        logger.warning("No .md files found in %s", runbooks_dir)
        return stats

    logger.info("Found %d runbook files in %s", len(runbook_files), runbooks_dir)

    # Build the full list of chunks across all runbooks
    all_chunks: list[RunbookChunk] = []
    for path in runbook_files:
        chunks = list(_parse_runbook(path))
        if not chunks:
            stats.errors.append(f"No chunks produced from {path.name}")
            continue
        all_chunks.extend(chunks)
        stats.runbooks_processed += 1
        logger.debug("Parsed %s → %d chunks", path.name, len(chunks))

    if not all_chunks:
        logger.error("No chunks produced. Aborting ingestion.")
        return stats

    logger.info(
        "Parsed %d runbooks into %d chunks. Embedding and storing...",
        stats.runbooks_processed,
        len(all_chunks),
    )

    # Initialize embedding provider and vector store
    embeddings = get_embedding_provider(settings)
    vectorstore = Chroma(
        collection_name=settings.chroma_collection_name,
        embedding_function=embeddings,
        persist_directory=settings.chroma_persist_dir,
    )

    # Convert to LangChain Documents
    documents = [chunk.to_document() for chunk in all_chunks]
    ids = [chunk.chunk_id for chunk in all_chunks]

    # Upsert — safe to re-run; existing IDs are updated, not duplicated
    vectorstore.add_documents(documents=documents, ids=ids)

    stats.chunks_upserted = len(all_chunks)
    logger.info("Ingestion complete.\n%s", stats)

    return stats


# ── CLI entry point ──────────────────────────────────────────────────────────


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    print("Starting runbook ingestion...\n")
    result = ingest_runbooks()
    print(f"\nDone.\n{result}")
