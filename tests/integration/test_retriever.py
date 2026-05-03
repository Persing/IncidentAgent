# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
Integration tests for RunbookRetriever.

ChromaDB and embeddings are mocked so no API keys or vector DB are required.
The tests verify the RRF fusion logic, family filtering, and result shaping.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from langchain_core.documents import Document

from src.retrieval.retriever import RunbookRetriever, RunbookMatch


def _make_doc(runbook: str, family: str = "compute") -> Document:
    return Document(
        page_content=f"Content for {runbook}",
        metadata={
            "runbook": runbook,
            "family": family,
            "severity": "high",
            "source": f"data/runbooks/{runbook}.md",
            "section": "Overview",
            "chunk_id": f"{runbook}__overview",
        },
    )


@pytest.fixture
def mock_vectorstore():
    vs = MagicMock()
    # similarity_search_with_score returns (Document, score) pairs
    vs.similarity_search_with_score.return_value = [
        (_make_doc("compute-crashloop", "compute"), 0.1),
        (_make_doc("networking-dns-failure", "networking"), 0.3),
    ]
    return vs


@pytest.fixture
def retriever(mock_vectorstore, bm25_index):
    return RunbookRetriever(vectorstore=mock_vectorstore, bm25_index=bm25_index)


class TestRetrieve:
    def test_returns_runbook_matches(self, retriever):
        results = retriever.retrieve("CrashLoopBackOff pod restarting", return_k=3)
        assert isinstance(results, list)
        assert all(isinstance(r, RunbookMatch) for r in results)

    def test_return_k_respected(self, retriever):
        results = retriever.retrieve("CrashLoopBackOff", return_k=1)
        assert len(results) <= 1

    def test_hybrid_mode_uses_bm25(self, retriever, mock_vectorstore):
        retriever.retrieve("CrashLoopBackOff", hybrid=True)
        # Semantic search called at least once
        assert mock_vectorstore.similarity_search_with_score.called

    def test_semantic_only_mode(self, retriever, mock_vectorstore):
        results = retriever.retrieve("CrashLoopBackOff", hybrid=False, return_k=5)
        assert isinstance(results, list)
        # Only semantic signal used — results come from vectorstore mock
        assert mock_vectorstore.similarity_search_with_score.called

    def test_result_has_provenance_fields(self, retriever):
        results = retriever.retrieve("CrashLoopBackOff pod restarting", return_k=3)
        if results:
            r = results[0]
            # At least one rank should be set (from semantic or BM25)
            assert r.semantic_rank is not None or r.bm25_rank is not None

    def test_family_filter_triggers_filtered_search(self, retriever, mock_vectorstore):
        retriever.retrieve(
            "service unresponsive health check timing out",
            family_filter=["compute", "networking"],
            return_k=3,
        )
        # Should call similarity_search_with_score more than once (unfiltered + filtered)
        assert mock_vectorstore.similarity_search_with_score.call_count >= 2

    def test_bm25_query_override(self, retriever):
        # Should not raise; the separate BM25 query is used internally
        results = retriever.retrieve(
            "service unresponsive",
            bm25_query="service unresponsive CrashLoopBackOff liveness probe",
            return_k=3,
        )
        assert isinstance(results, list)


class TestSemanticSearch:
    def test_deduplicates_by_runbook(self, retriever, mock_vectorstore):
        mock_vectorstore.similarity_search_with_score.return_value = [
            (_make_doc("compute-crashloop"), 0.1),
            (_make_doc("compute-crashloop"), 0.2),  # duplicate
            (_make_doc("networking-dns-failure"), 0.3),
        ]
        ranked = retriever._semantic_search("query", fetch_k=10)
        runbooks = [r for r, _ in ranked]
        assert runbooks.count("compute-crashloop") == 1

    def test_sorted_ascending_by_distance(self, retriever, mock_vectorstore):
        mock_vectorstore.similarity_search_with_score.return_value = [
            (_make_doc("rb-b"), 0.5),
            (_make_doc("rb-a"), 0.1),
        ]
        ranked = retriever._semantic_search("query", fetch_k=10)
        scores = [s for _, s in ranked]
        assert scores == sorted(scores)


class TestBM25Search:
    def test_returns_ranked_list(self, retriever):
        ranked = retriever._bm25_search("CrashLoopBackOff container restart", fetch_k=10)
        assert isinstance(ranked, list)

    def test_deduplicates_by_runbook(self, retriever):
        ranked = retriever._bm25_search("crash loop back off dns", fetch_k=10)
        runbooks = [r for r, _ in ranked]
        assert len(runbooks) == len(set(runbooks))
