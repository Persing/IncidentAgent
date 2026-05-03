# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""Unit tests for prompt schema and context formatting."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.agent.prompts import (
    Confidence,
    Severity,
    TriagePlan,
    format_runbook_context,
)


# ── format_runbook_context ────────────────────────────────────────────────────


class TestFormatRunbookContext:
    def test_single_file(self, tmp_path):
        rb = tmp_path / "my-runbook.md"
        rb.write_text("## Overview\n\nContent here.\n\n## Tags\n`family: compute`", encoding="utf-8")
        result = format_runbook_context([str(rb)])
        assert "Content here" in result
        # Tags section stripped
        assert "`family: compute`" not in result

    def test_multiple_files_joined_with_divider(self, tmp_path):
        rb1 = tmp_path / "rb1.md"
        rb2 = tmp_path / "rb2.md"
        rb1.write_text("## Overview\n\nFirst runbook.", encoding="utf-8")
        rb2.write_text("## Overview\n\nSecond runbook.", encoding="utf-8")
        result = format_runbook_context([str(rb1), str(rb2)])
        assert "First runbook" in result
        assert "Second runbook" in result
        assert "─" in result  # divider character

    def test_missing_file_skipped(self, tmp_path):
        real = tmp_path / "real.md"
        real.write_text("## Overview\n\nReal content.", encoding="utf-8")
        result = format_runbook_context([str(real), "/nonexistent/path.md"])
        assert "Real content" in result

    def test_empty_list(self):
        result = format_runbook_context([])
        assert result == "No runbook context available."

    def test_all_missing_files(self):
        result = format_runbook_context(["/no/such/file.md"])
        assert result == "No runbook context available."

    def test_tags_section_stripped(self, tmp_path):
        rb = tmp_path / "tagged.md"
        rb.write_text(
            "## Overview\n\nSome content.\n\n## Tags\n`family: compute`\n`severity: high`\n\n## Diagnostic Steps\n\nDo something.",
            encoding="utf-8",
        )
        result = format_runbook_context([str(rb)])
        assert "Tags" not in result
        assert "`family: compute`" not in result
        assert "Diagnostic Steps" in result


# ── TriagePlan schema ─────────────────────────────────────────────────────────


class TestTriagePlanSchema:
    def _valid_plan(self, **overrides):
        defaults = dict(
            incident_summary="A pod is crash-looping.",
            severity=Severity.HIGH,
            likely_cause="OOMKilled",
            affected_components=["api-server"],
            diagnostic_steps=["kubectl logs --previous"],
            resolution_steps=["Increase memory limit."],
            escalation_criteria=["Persists after fix."],
            runbooks_referenced=["compute-crashloop"],
            confidence=Confidence.HIGH,
            confidence_reason="Exact match.",
        )
        defaults.update(overrides)
        return TriagePlan(**defaults)

    def test_valid_plan_construction(self):
        plan = self._valid_plan()
        assert plan.severity == Severity.HIGH
        assert plan.confidence == Confidence.HIGH

    def test_severity_enum_values(self):
        for value in ("low", "medium", "high", "critical"):
            plan = self._valid_plan(severity=Severity(value))
            assert plan.severity.value == value

    def test_confidence_enum_values(self):
        for value in ("low", "medium", "high"):
            plan = self._valid_plan(confidence=Confidence(value))
            assert plan.confidence.value == value

    def test_lists_preserved(self):
        plan = self._valid_plan(
            diagnostic_steps=["step one", "step two"],
            resolution_steps=["fix one", "fix two"],
        )
        assert len(plan.diagnostic_steps) == 2
        assert len(plan.resolution_steps) == 2

    def test_serialise_to_dict(self):
        plan = self._valid_plan()
        d = plan.model_dump()
        assert d["severity"] == "high"
        assert isinstance(d["diagnostic_steps"], list)
