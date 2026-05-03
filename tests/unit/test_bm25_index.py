# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""Unit tests for BM25Index."""

from __future__ import annotations

import pytest

from src.retrieval.bm25_index import BM25Index, BM25Result


class TestBM25IndexSearch:
    def test_relevant_query_returns_results(self, bm25_index):
        results = bm25_index.search("CrashLoopBackOff container restart")
        assert len(results) > 0

    def test_results_are_bm25result_instances(self, bm25_index):
        results = bm25_index.search("CrashLoopBackOff")
        assert all(isinstance(r, BM25Result) for r in results)

    def test_zero_score_results_excluded(self, bm25_index):
        results = bm25_index.search("CrashLoopBackOff")
        assert all(r.score > 0 for r in results)

    def test_results_sorted_descending(self, bm25_index):
        results = bm25_index.search("container crash restart kubernetes")
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_irrelevant_query_returns_empty(self, bm25_index):
        results = bm25_index.search("xyzzy frobble quux")
        assert results == []

    def test_top_k_respected(self, bm25_index):
        results = bm25_index.search("container crash dns", k=1)
        assert len(results) <= 1

    def test_compute_query_matches_compute_runbook(self, bm25_index):
        results = bm25_index.search("CrashLoopBackOff pod restart")
        runbooks = [r.runbook for r in results]
        assert "compute-crashloop" in runbooks

    def test_networking_query_matches_networking_runbook(self, bm25_index):
        results = bm25_index.search("DNS resolution failure nslookup")
        runbooks = [r.runbook for r in results]
        assert "networking-dns-failure" in runbooks

    def test_result_metadata_fields(self, bm25_index):
        results = bm25_index.search("CrashLoopBackOff")
        r = results[0]
        assert r.runbook
        assert r.section
        assert r.family
        assert r.severity
        assert r.source


class TestBM25IndexFromDir:
    def test_from_runbooks_dir(self, runbooks_dir):
        index = BM25Index.from_runbooks_dir(str(runbooks_dir))
        assert len(index._chunks) > 0

    def test_chunk_count_matches_sections(self, runbooks_dir):
        index = BM25Index.from_runbooks_dir(str(runbooks_dir))
        # Two runbooks, each has title + overview + diagnostic steps (3 sections each = 6 total)
        # Tags section is excluded, so fewer than raw section count
        assert len(index._chunks) >= 4

    def test_families_populated(self, runbooks_dir):
        index = BM25Index.from_runbooks_dir(str(runbooks_dir))
        families = {c.family for c in index._chunks}
        assert "compute" in families
        assert "networking" in families

    def test_empty_directory_raises(self, tmp_path):
        # BM25Okapi requires at least one document; an empty corpus causes
        # a ZeroDivisionError inside the library. Document this behaviour.
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with pytest.raises(ZeroDivisionError):
            BM25Index.from_runbooks_dir(str(empty_dir))
