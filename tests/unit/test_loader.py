# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""Unit tests for the runbook ingestion / parsing logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.ingestion.loader import (
    IngestionStats,
    RunbookChunk,
    _parse_runbook,
    _parse_tags,
    _split_by_sections,
)


# ── _parse_tags ───────────────────────────────────────────────────────────────


class TestParseTags:
    def test_all_tags_present(self):
        content = "`family: compute`\n`severity: high`\n`services: api-server`"
        tags = _parse_tags(content)
        assert tags["family"] == "compute"
        assert tags["severity"] == "high"
        assert tags["services"] == "api-server"

    def test_missing_tags_default_to_unknown(self):
        tags = _parse_tags("no tags here")
        assert tags["family"] == "unknown"
        assert tags["severity"] == "unknown"
        assert tags["services"] == "unknown"

    def test_partial_tags(self):
        content = "`family: networking`"
        tags = _parse_tags(content)
        assert tags["family"] == "networking"
        assert tags["severity"] == "unknown"

    def test_whitespace_trimmed(self):
        content = "`family:   storage  `"
        tags = _parse_tags(content)
        assert tags["family"] == "storage"

    def test_tags_in_markdown_section(self):
        md = "## Tags\n`family: data`\n`severity: medium`\n`services: kafka`"
        tags = _parse_tags(md)
        assert tags == {"family": "data", "severity": "medium", "services": "kafka"}


# ── _split_by_sections ────────────────────────────────────────────────────────


class TestSplitBySections:
    def test_basic_split(self):
        md = "# My Runbook\n\nIntro text.\n\n## Section One\n\nBody one.\n\n## Section Two\n\nBody two."
        sections = _split_by_sections(md)
        names = [s[0] for s in sections]
        assert "My Runbook" in names
        assert "Section One" in names
        assert "Section Two" in names

    def test_title_in_preamble(self):
        md = "# Title Here\n\nSome intro.\n\n## Details\n\nContent."
        sections = _split_by_sections(md)
        assert sections[0][0] == "Title Here"

    def test_tags_section_excluded(self):
        md = "# Run\n\n## Tags\n`family: compute`\n\n## Overview\n\nContent."
        sections = _split_by_sections(md)
        names = [s[0] for s in sections]
        assert "Tags" not in names

    def test_empty_section_body_excluded(self):
        md = "# Run\n\n## Empty\n\n## Has Content\n\nActual content here."
        sections = _split_by_sections(md)
        names = [s[0] for s in sections]
        assert "Empty" not in names
        assert "Has Content" in names

    def test_header_included_in_section_content(self):
        md = "# R\n\n## Diagnostic Steps\n\nRun kubectl logs."
        sections = _split_by_sections(md)
        diag = next(s for s in sections if s[0] == "Diagnostic Steps")
        assert "## Diagnostic Steps" in diag[1]

    def test_no_h2_sections(self):
        md = "# Just a title\n\nOnly preamble content."
        sections = _split_by_sections(md)
        assert len(sections) == 1
        assert sections[0][0] == "Just a title"

    def test_no_title_defaults_to_overview(self):
        md = "Just some content without a title.\n\n## Section\n\nBody."
        sections = _split_by_sections(md)
        assert sections[0][0] == "Overview"


# ── RunbookChunk ──────────────────────────────────────────────────────────────


class TestRunbookChunk:
    def _make_chunk(self, runbook="compute-crashloop", section="Diagnostic Steps"):
        return RunbookChunk(
            runbook_name=runbook,
            section_name=section,
            content="## Diagnostic Steps\n\nkubectl logs --previous",
            family="compute",
            severity="high",
            services="any",
            source_path="data/runbooks/compute-crashloop.md",
        )

    def test_chunk_id_format(self):
        chunk = self._make_chunk()
        assert chunk.chunk_id == "compute-crashloop__diagnostic_steps"

    def test_chunk_id_slugify(self):
        chunk = self._make_chunk(section="Alert Signatures & Examples")
        assert chunk.chunk_id == "compute-crashloop__alert_signatures_examples"

    def test_to_document_metadata(self):
        chunk = self._make_chunk()
        doc = chunk.to_document()
        assert doc.metadata["runbook"] == "compute-crashloop"
        assert doc.metadata["family"] == "compute"
        assert doc.metadata["severity"] == "high"
        assert doc.metadata["chunk_id"] == chunk.chunk_id

    def test_to_document_page_content(self):
        chunk = self._make_chunk()
        doc = chunk.to_document()
        assert "kubectl logs" in doc.page_content


# ── _parse_runbook ────────────────────────────────────────────────────────────


class TestParseRunbook:
    def test_yields_chunks(self, runbook_file):
        chunks = list(_parse_runbook(runbook_file))
        assert len(chunks) >= 1

    def test_chunk_inherits_tags(self, runbook_file):
        chunks = list(_parse_runbook(runbook_file))
        for chunk in chunks:
            assert chunk.family == "compute"
            assert chunk.severity == "high"

    def test_tags_section_not_yielded(self, runbook_file):
        chunks = list(_parse_runbook(runbook_file))
        section_names = [c.section_name for c in chunks]
        assert "Tags" not in section_names

    def test_source_path_set(self, runbook_file):
        chunks = list(_parse_runbook(runbook_file))
        assert all(c.source_path == str(runbook_file) for c in chunks)

    def test_runbook_name_is_stem(self, runbook_file):
        chunks = list(_parse_runbook(runbook_file))
        assert all(c.runbook_name == "compute-crashloop" for c in chunks)

    def test_missing_file_yields_nothing(self, tmp_path):
        missing = tmp_path / "does-not-exist.md"
        chunks = list(_parse_runbook(missing))
        assert chunks == []


# ── IngestionStats ────────────────────────────────────────────────────────────


class TestIngestionStats:
    def test_str_no_errors(self):
        stats = IngestionStats(runbooks_processed=5, chunks_created=30, chunks_skipped=2)
        text = str(stats)
        assert "5" in text
        assert "30" in text
        assert "Errors" not in text

    def test_str_with_errors(self):
        stats = IngestionStats(errors=["bad file"])
        text = str(stats)
        assert "Errors" in text
        assert "bad file" in text
