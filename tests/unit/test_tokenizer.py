# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""Unit tests for the BM25 tokenizer."""

import pytest

from src.retrieval.bm25_index import tokenize


class TestTokenize:
    def test_lowercase_plain_words(self):
        assert tokenize("hello world") == ["hello", "world"]

    def test_camel_case_split(self):
        tokens = tokenize("CrashLoopBackOff")
        assert "crash" in tokens
        assert "loop" in tokens
        assert "back" in tokens
        assert "off" in tokens

    def test_consecutive_caps_split(self):
        # "OOMKilled" → OOM + Killed
        tokens = tokenize("OOMKilled")
        assert "oom" in tokens
        assert "killed" in tokens

    def test_hyphen_case(self):
        tokens = tokenize("kube-proxy")
        assert "kube" in tokens
        assert "proxy" in tokens

    def test_snake_case(self):
        tokens = tokenize("container_cpu_usage")
        assert "container" in tokens
        assert "cpu" in tokens
        assert "usage" in tokens

    def test_mixed_compound(self):
        tokens = tokenize("text-embedding-3-small")
        assert "text" in tokens
        assert "embedding" in tokens
        assert "small" in tokens
        # single digits dropped (len < 2)
        assert "3" not in tokens

    def test_drops_short_tokens(self):
        tokens = tokenize("a is the")
        # "a" → 1 char, dropped; "is" → 2 chars kept; "the" → kept
        assert "a" not in tokens
        assert "is" in tokens
        assert "the" in tokens

    def test_empty_string(self):
        assert tokenize("") == []

    def test_only_punctuation(self):
        assert tokenize("!!!---...") == []

    def test_metric_name(self):
        tokens = tokenize("kube_pod_container_status_restarts_total")
        assert "kube" in tokens
        assert "pod" in tokens
        assert "restarts" in tokens
        assert "total" in tokens

    def test_kubernetes_kind(self):
        tokens = tokenize("KubePodCrashLooping")
        assert "kube" in tokens
        assert "pod" in tokens
        assert "crash" in tokens
        assert "looping" in tokens

    def test_no_duplicate_side_effects(self):
        # Calling twice on same input is deterministic
        assert tokenize("CrashLoopBackOff") == tokenize("CrashLoopBackOff")
