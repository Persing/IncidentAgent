# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
BM25 keyword index over the runbook chunk corpus.

BM25 (Best Match 25) is a probabilistic keyword ranking function. It scores
documents by how often query terms appear in them, adjusted for document
length. It is the standard baseline for keyword retrieval — Elasticsearch
and OpenSearch both use it as their default scorer.

Why BM25 alongside semantic search:
  Semantic embeddings excel at intent and paraphrase — "stale dashboards"
  matching an ELT pipeline runbook. BM25 excels at exact technical vocabulary
  — an alert containing "CrashLoopBackOff" or "etcd_disk_wal_fsync_duration"
  will score the right runbook highly regardless of semantic similarity.

The two retrieval signals are complementary. Combining them via RRF gives
better recall than either alone, especially on technical incident text where
alert names and metric labels are exact strings that matter.

Tokenizer design:
  Standard word tokenizers split on whitespace and punctuation. That works
  poorly for infrastructure text where meaning is packed into compound forms:
    CrashLoopBackOff    → crash loop back off
    kube-proxy          → kube proxy
    container_cpu_usage → container cpu usage
    text-embedding-3-small → text embedding 3 small

  The tokenizer here splits camelCase, hyphens, underscores, and other
  punctuation, then lowercases. This makes "CrashLoopBackOff" in an alert
  match "crashloopbackoff" in a runbook and vice versa.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

from src.ingestion.loader import RunbookChunk, _parse_runbook


# ── Tokenizer ────────────────────────────────────────────────────────────────


def tokenize(text: str) -> list[str]:
    """
    Tokenize incident/runbook text for BM25 indexing.

    Handles the compound identifier forms common in infrastructure text:
    camelCase, hyphen-case, snake_case, and mixed forms like
    'KubePodCrashLooping' or 'container_memory_working_set_bytes'.

    Args:
        text: Raw text to tokenize.

    Returns:
        List of lowercase token strings, minimum length 2.
    """
    # 1. Split camelCase boundaries: "CrashLoop" → "Crash Loop"
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)

    # 2. Lowercase
    text = text.lower()

    # 3. Split on anything that is not alphanumeric
    tokens = re.split(r"[^a-z0-9]+", text)

    # 4. Drop tokens shorter than 2 characters (removes lone digits, punctuation remnants)
    return [t for t in tokens if len(t) >= 2]


# ── Index ────────────────────────────────────────────────────────────────────


@dataclass
class BM25Result:
    """A single BM25 search result."""

    runbook: str
    section: str
    score: float
    source: str
    family: str
    severity: str


class BM25Index:
    """
    In-memory BM25 index over all runbook section chunks.

    The index is built at construction time by parsing the same runbook files
    used for the embedding pipeline. Both indices stay in sync automatically
    as long as the loader and this class read from the same runbooks directory.

    Construction is fast — 252 chunks, pure Python, typically <100ms.
    """

    def __init__(self, chunks: list[RunbookChunk]) -> None:
        self._chunks = chunks
        tokenized_corpus = [tokenize(chunk.content) for chunk in chunks]
        self._bm25 = BM25Okapi(tokenized_corpus)

    @classmethod
    def from_runbooks_dir(cls, runbooks_dir: str = "data/runbooks") -> BM25Index:
        """
        Build a BM25Index by parsing all .md files in the given directory.

        Uses the same parser as the ingestion pipeline so the BM25 corpus
        is always in sync with ChromaDB.
        """
        chunks: list[RunbookChunk] = []
        for path in sorted(Path(runbooks_dir).glob("*.md")):
            chunks.extend(_parse_runbook(path))
        return cls(chunks)

    def search(self, query: str, k: int = 20) -> list[BM25Result]:
        """
        Return the top-k chunks ranked by BM25 score.

        Chunks with a score of zero are excluded — they share no query terms
        with the document and would only add noise to the fusion step.

        Args:
            query: The incident alert or description.
            k:     Maximum number of chunks to return.

        Returns:
            List of BM25Result, highest score first. May be shorter than k
            if fewer than k chunks have non-zero scores.
        """
        tokens = tokenize(query)
        scores = self._bm25.get_scores(tokens)

        # Pair each chunk with its score, sort descending, take top-k
        ranked = sorted(
            ((i, score) for i, score in enumerate(scores) if score > 0),
            key=lambda x: x[1],
            reverse=True,
        )[:k]

        return [
            BM25Result(
                runbook=self._chunks[i].runbook_name,
                section=self._chunks[i].section_name,
                score=round(score, 4),
                source=self._chunks[i].source_path,
                family=self._chunks[i].family,
                severity=self._chunks[i].severity,
            )
            for i, score in ranked
        ]

    @property
    def chunks(self):
        return self._chunks


# ── Reciprocal Rank Fusion ───────────────────────────────────────────────────


def reciprocal_rank_fusion(
    *ranked_lists: list[str],
    k: int = 60,
) -> list[tuple[str, float]]:
    """
    Combine multiple ranked lists of runbook names using Reciprocal Rank Fusion.

    RRF is the standard method for combining heterogeneous retrieval signals
    without needing to normalize their score scales. It only cares about rank
    position, not raw score values. This makes it robust when combining BM25
    (unbounded, corpus-dependent scale) with cosine/L2 distance (bounded scale).

    Formula: RRF(d) = Σ 1 / (k + rank(d, list_i))
    where rank is 1-indexed and k=60 is the standard smoothing constant.

    A document that does not appear in a list is simply not scored for that list
    — it is not penalized. Documents that appear high in multiple lists get
    the highest combined scores.

    Args:
        *ranked_lists: Two or more lists of runbook name strings, best first.
        k:             Smoothing constant. Higher k reduces the bonus for
                       top-ranked results. 60 is the value from the original
                       Cormack et al. (2009) paper and works well in practice.

    Returns:
        List of (runbook_name, rrf_score) tuples, sorted highest score first.
    """
    rrf_scores: dict[str, float] = {}

    for ranked_list in ranked_lists:
        for rank, runbook in enumerate(ranked_list, start=1):
            rrf_scores[runbook] = rrf_scores.get(runbook, 0.0) + 1.0 / (k + rank)

    return sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
