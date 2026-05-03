# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
Shared fixtures for the IncidentAgent test suite.

All fixtures that mock external dependencies (LLM, embeddings, ChromaDB)
live here so individual test modules stay focused on behaviour.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.agent.classify import ClassificationResult, IncidentFamily
from src.agent.prompts import Confidence, Severity, TriagePlan
from src.config.settings import Settings
from src.retrieval.bm25_index import BM25Index
from src.retrieval.retriever import RunbookMatch


# ── Settings ─────────────────────────────────────────────────────────────────


@pytest.fixture
def test_settings(tmp_path):
    """
    Settings that bypass API-key validation (ollama needs no key).
    Overrides runbooks_dir and chroma_persist_dir to avoid touching real data.
    """
    runbooks = tmp_path / "runbooks"
    runbooks.mkdir()
    chroma = tmp_path / "chroma_db"
    chroma.mkdir()

    # Clear lru_cache so each test gets fresh settings
    from src.config.settings import get_settings
    get_settings.cache_clear()

    settings = Settings(
        embedding_provider="ollama",
        llm_provider="ollama",
        runbooks_dir=str(runbooks),
        chroma_persist_dir=str(chroma),
    )
    yield settings
    get_settings.cache_clear()


# ── Minimal runbook markdown ──────────────────────────────────────────────────


MINIMAL_RUNBOOK = """\
# CrashLoopBackOff — Container Repeatedly Crashing

## Tags
`family: compute`
`severity: high`
`services: any`

## Overview
CrashLoopBackOff means the container keeps restarting.

## Diagnostic Steps
Run kubectl logs --previous to see the crash reason.
"""


@pytest.fixture
def runbook_file(tmp_path) -> Path:
    """Write one minimal runbook file and return its path."""
    path = tmp_path / "compute-crashloop.md"
    path.write_text(MINIMAL_RUNBOOK, encoding="utf-8")
    return path


@pytest.fixture
def runbooks_dir(tmp_path) -> Path:
    """Return a directory with two minimal runbook files."""
    rb_dir = tmp_path / "runbooks"
    rb_dir.mkdir()

    (rb_dir / "compute-crashloop.md").write_text(
        """\
# CrashLoopBackOff

## Tags
`family: compute`
`severity: high`
`services: any`

## Overview
CrashLoopBackOff means container keeps restarting.

## Diagnostic Steps
kubectl logs --previous
""",
        encoding="utf-8",
    )

    (rb_dir / "networking-dns-failure.md").write_text(
        """\
# DNS Resolution Failure

## Tags
`family: networking`
`severity: medium`
`services: any`

## Overview
DNS lookup failures block inter-service communication.

## Diagnostic Steps
kubectl exec -it <pod> -- nslookup kubernetes.default
""",
        encoding="utf-8",
    )

    return rb_dir


# ── BM25Index fixture ─────────────────────────────────────────────────────────


@pytest.fixture
def bm25_index(runbooks_dir) -> BM25Index:
    """Real BM25Index built from the two minimal test runbooks."""
    return BM25Index.from_runbooks_dir(str(runbooks_dir))


# ── Canned triage plan ────────────────────────────────────────────────────────


@pytest.fixture
def sample_triage_plan() -> TriagePlan:
    return TriagePlan(
        incident_summary="A pod is crash-looping in the ml-serving namespace.",
        severity=Severity.HIGH,
        likely_cause="OOMKilled — container exceeds memory limit on startup.",
        affected_components=["api-server", "ml-serving namespace"],
        diagnostic_steps=["kubectl logs <pod> --previous", "kubectl describe pod <pod>"],
        resolution_steps=["Increase memory limit in the deployment manifest."],
        escalation_criteria=["Crash persists after memory increase."],
        runbooks_referenced=["compute-crashloop"],
        confidence=Confidence.HIGH,
        confidence_reason="Alert text exactly matches the CrashLoopBackOff runbook.",
    )


@pytest.fixture
def sample_classification() -> ClassificationResult:
    return ClassificationResult(
        families=[IncidentFamily.COMPUTE],
        infrastructure_signals=["CrashLoopBackOff", "liveness probe failure", "OOMKilled"],
        augmented_query=(
            "pod is crash looping [signals: CrashLoopBackOff liveness probe failure OOMKilled]"
        ),
        needs_clarification=False,
        clarification_question=None,
    )


# ── Mocked RunbookRetriever ───────────────────────────────────────────────────


@pytest.fixture
def sample_matches(runbooks_dir) -> list[RunbookMatch]:
    return [
        RunbookMatch(
            runbook="compute-crashloop",
            rrf_score=0.032,
            semantic_rank=1,
            bm25_rank=1,
            family="compute",
            severity="high",
            source=str(runbooks_dir / "compute-crashloop.md"),
        )
    ]


@pytest.fixture
def mock_retriever(sample_matches):
    retriever = MagicMock()
    retriever.retrieve.return_value = sample_matches
    return retriever
