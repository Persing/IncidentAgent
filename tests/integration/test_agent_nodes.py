# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
Integration tests for the classify, retrieve, and generate graph nodes.

All LLM calls are mocked — no real API calls are made.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.agent.classify import (
    ClassificationResult,
    make_classify_node,
)
from src.agent.triage_agent import make_generate_node, make_retrieve_node


# ── classify node ─────────────────────────────────────────────────────────────


class TestClassifyNode:
    @pytest.fixture
    def classify(self, test_settings, sample_classification):
        """classify node with LLM chain mocked to return a canned classification."""
        with patch("src.providers.llm.get_llm_provider") as mock_llm:
            # LangChain wraps non-Runnable callables via RunnableLambda, so
            # the mock is called directly: mock_structured(input) → .return_value
            mock_structured = MagicMock(return_value=sample_classification)
            mock_llm_instance = MagicMock()
            mock_llm_instance.with_structured_output.return_value = mock_structured
            mock_llm.return_value = mock_llm_instance

            node = make_classify_node(test_settings)
        return node

    @pytest.fixture
    def classify_failing(self, test_settings):
        """classify node whose chain raises on every invoke."""
        with patch("src.providers.llm.get_llm_provider") as mock_llm:
            mock_structured = MagicMock(side_effect=RuntimeError("LLM unreachable"))
            mock_llm_instance = MagicMock()
            mock_llm_instance.with_structured_output.return_value = mock_structured
            mock_llm.return_value = mock_llm_instance

            node = make_classify_node(test_settings)
        return node

    def test_returns_classification_on_success(self, classify):
        state = {"query": "pod is crash looping in ml-serving namespace"}
        result = classify(state)
        assert "classification" in result
        assert isinstance(result["classification"], ClassificationResult)

    def test_classification_has_families(self, classify):
        state = {"query": "pod crash looping"}
        result = classify(state)
        assert result["classification"].families

    def test_classification_has_signals(self, classify):
        state = {"query": "pod crash looping"}
        result = classify(state)
        assert len(result["classification"].infrastructure_signals) > 0

    def test_failure_returns_none_classification(self, classify_failing):
        result = classify_failing({"query": "some alert query"})
        assert result == {"classification": None}

    def test_augmented_query_contains_signals(self, classify):
        state = {"query": "pod crash looping"}
        result = classify(state)
        cl = result["classification"]
        assert any(sig in cl.augmented_query for sig in cl.infrastructure_signals)


# ── retrieve node ─────────────────────────────────────────────────────────────


class TestRetrieveNode:
    def test_returns_runbook_matches(self, mock_retriever, sample_matches):
        retrieve = make_retrieve_node(mock_retriever)
        state = {"query": "pod is crash looping", "classification": None}
        result = retrieve(state)
        assert "runbook_matches" in result
        assert len(result["runbook_matches"]) > 0

    def test_context_is_string(self, mock_retriever):
        retrieve = make_retrieve_node(mock_retriever)
        state = {"query": "pod is crash looping", "classification": None}
        result = retrieve(state)
        assert isinstance(result["context"], str)

    def test_classification_signals_expand_bm25_query(
        self, mock_retriever, sample_classification
    ):
        retrieve = make_retrieve_node(mock_retriever)
        state = {
            "query": "service is unresponsive",
            "classification": sample_classification,
        }
        retrieve(state)
        call_kwargs = mock_retriever.retrieve.call_args.kwargs
        # When classification is present, bm25_query should be set
        assert call_kwargs.get("bm25_query") is not None

    def test_no_classification_leaves_bm25_query_none(self, mock_retriever):
        retrieve = make_retrieve_node(mock_retriever)
        state = {"query": "raw alert text", "classification": None}
        retrieve(state)
        call_kwargs = mock_retriever.retrieve.call_args.kwargs
        assert call_kwargs.get("bm25_query") is None

    def test_family_filter_set_from_classification(
        self, mock_retriever, sample_classification
    ):
        retrieve = make_retrieve_node(mock_retriever)
        state = {
            "query": "service unresponsive",
            "classification": sample_classification,
        }
        retrieve(state)
        call_kwargs = mock_retriever.retrieve.call_args.kwargs
        assert call_kwargs.get("family_filter") is not None


# ── generate node ─────────────────────────────────────────────────────────────


class TestGenerateNode:
    @pytest.fixture
    def generate(self, test_settings, sample_triage_plan):
        with patch("src.agent.triage_agent.get_llm_provider") as mock_llm:
            mock_structured = MagicMock(return_value=sample_triage_plan)
            mock_llm_instance = MagicMock()
            mock_llm_instance.with_structured_output.return_value = mock_structured
            mock_llm.return_value = mock_llm_instance

            node = make_generate_node(test_settings)
        return node

    def test_returns_triage_plan(self, generate):
        state = {"query": "pod crash looping", "context": "runbook context here"}
        result = generate(state)
        assert "triage_plan" in result

    def test_plan_has_severity(self, generate):
        state = {"query": "pod crash looping", "context": "some context"}
        result = generate(state)
        assert result["triage_plan"].severity is not None

    def test_plan_has_diagnostic_steps(self, generate):
        state = {"query": "pod crash looping", "context": "some context"}
        result = generate(state)
        assert len(result["triage_plan"].diagnostic_steps) > 0

    def test_plan_has_confidence(self, generate):
        state = {"query": "pod crash looping", "context": "some context"}
        result = generate(state)
        assert result["triage_plan"].confidence is not None
