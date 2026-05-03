# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
Integration tests for the FastAPI endpoints.

The LangGraph, ChromaDB, and LLM providers are all mocked so no external
services are required. The lifespan context is patched to inject mock
objects directly into app.state.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.agent.classify import ClassificationResult, IncidentFamily
from src.agent.prompts import Confidence, Severity, TriagePlan
from src.retrieval.retriever import RunbookMatch


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_triage_state(plan, matches, classification=None):
    return {
        "query": "test query",
        "classification": classification,
        "runbook_matches": matches,
        "context": "mock context",
        "triage_plan": plan,
    }


@pytest.fixture
def mock_graph(sample_triage_plan, sample_matches, sample_classification):
    graph = MagicMock()
    graph.invoke.return_value = _make_triage_state(
        plan=sample_triage_plan,
        matches=sample_matches,
        classification=sample_classification,
    )
    return graph


@pytest.fixture
def api_client(mock_graph, tmp_path):
    """TestClient with graph and settings injected — no real startup."""
    from src.config.settings import Settings, get_settings
    get_settings.cache_clear()

    # Build two minimal runbooks in a fresh temp dir for the /runbooks and /health endpoints
    rb_dir = tmp_path / "runbooks"
    rb_dir.mkdir(exist_ok=True)
    (rb_dir / "compute-crashloop.md").write_text(
        "# CrashLoop\n\n## Tags\n`family: compute`\n`severity: high`\n`services: any`\n\n## Overview\nRestarting.",
        encoding="utf-8",
    )
    (rb_dir / "networking-dns-failure.md").write_text(
        "# DNS Failure\n\n## Tags\n`family: networking`\n`severity: medium`\n`services: any`\n\n## Overview\nDNS broken.",
        encoding="utf-8",
    )

    settings = Settings(
        embedding_provider="ollama",
        llm_provider="ollama",
        runbooks_dir=str(rb_dir),
        chroma_persist_dir=str(tmp_path / "chroma_db"),
    )

    with patch("src.api.main.build_graph", return_value=mock_graph), \
         patch("src.api.main.get_settings", return_value=settings):
        with TestClient(app) as client:
            yield client

    get_settings.cache_clear()


# ── /health ───────────────────────────────────────────────────────────────────


class TestHealthEndpoint:
    def test_returns_200(self, api_client):
        response = api_client.get("/health")
        assert response.status_code == 200

    def test_status_ok(self, api_client):
        data = api_client.get("/health").json()
        assert data["status"] == "ok"

    def test_provider_fields_present(self, api_client):
        data = api_client.get("/health").json()
        assert "embedding_provider" in data
        assert "llm_provider" in data
        assert "runbooks_indexed" in data

    def test_runbooks_indexed_count(self, api_client):
        data = api_client.get("/health").json()
        # Two runbook files in the runbooks_dir fixture
        assert data["runbooks_indexed"] == 2


# ── /runbooks ─────────────────────────────────────────────────────────────────


class TestRunbooksEndpoint:
    def test_returns_200(self, api_client):
        response = api_client.get("/runbooks")
        assert response.status_code == 200

    def test_returns_list(self, api_client):
        data = api_client.get("/runbooks").json()
        assert isinstance(data, list)

    def test_runbook_entries_have_required_fields(self, api_client):
        data = api_client.get("/runbooks").json()
        assert len(data) > 0
        for entry in data:
            assert "name" in entry
            assert "family" in entry
            assert "severity" in entry
            assert "path" in entry

    def test_runbook_families_populated(self, api_client):
        data = api_client.get("/runbooks").json()
        families = {entry["family"] for entry in data}
        assert "compute" in families
        assert "networking" in families


# ── /triage ───────────────────────────────────────────────────────────────────


class TestTriageEndpoint:
    def test_returns_200(self, api_client):
        response = api_client.post(
            "/triage", json={"query": "Pod is crash looping in ml-serving namespace"}
        )
        assert response.status_code == 200

    def test_response_has_plan(self, api_client):
        data = api_client.post(
            "/triage", json={"query": "Pod is crash looping in ml-serving namespace"}
        ).json()
        assert "plan" in data
        assert data["plan"]["severity"] in ("low", "medium", "high", "critical")

    def test_response_has_retrieval(self, api_client):
        data = api_client.post(
            "/triage", json={"query": "Pod is crash looping in ml-serving namespace"}
        ).json()
        assert "retrieval" in data
        assert isinstance(data["retrieval"], list)

    def test_response_has_latency(self, api_client):
        data = api_client.post(
            "/triage", json={"query": "Pod is crash looping in ml-serving namespace"}
        ).json()
        assert "latency_ms" in data
        assert data["latency_ms"] >= 0

    def test_response_headers(self, api_client):
        response = api_client.post(
            "/triage", json={"query": "Pod is crash looping in ml-serving namespace"}
        )
        assert "X-Request-Id" in response.headers
        assert "X-Latency-Ms" in response.headers

    def test_query_too_short_returns_422(self, api_client):
        response = api_client.post("/triage", json={"query": "short"})
        assert response.status_code == 422

    def test_missing_query_returns_422(self, api_client):
        response = api_client.post("/triage", json={})
        assert response.status_code == 422

    def test_return_k_field(self, api_client):
        response = api_client.post(
            "/triage",
            json={"query": "Pod is crash looping in the ml-serving namespace", "return_k": 5},
        )
        assert response.status_code == 200

    def test_return_k_out_of_range_returns_422(self, api_client):
        response = api_client.post(
            "/triage",
            json={"query": "Pod is crash looping in the ml-serving namespace", "return_k": 0},
        )
        assert response.status_code == 422

    def test_agent_error_returns_503(self, api_client, mock_graph):
        mock_graph.invoke.side_effect = RuntimeError("LLM unreachable")
        response = api_client.post(
            "/triage", json={"query": "Pod is crash looping in ml-serving namespace"}
        )
        assert response.status_code == 503

    def test_classification_included_in_response(self, api_client):
        data = api_client.post(
            "/triage", json={"query": "Pod is crash looping in ml-serving namespace"}
        ).json()
        # classification may be None or populated; key must be present
        assert "classification" in data
