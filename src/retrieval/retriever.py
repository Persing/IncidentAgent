# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
Runbook retriever — supports both semantic-only and hybrid retrieval.

Hybrid mode (default) combines:
  1. Semantic search  — ChromaDB vector similarity via embedded query
  2. BM25 keyword     — exact and near-exact term matching over chunk text

The two result lists are fused using Reciprocal Rank Fusion (RRF), which
combines ranked lists without requiring score normalisation.

Why hybrid outperforms semantic-only on this corpus:
  - Incident alerts often contain exact technical identifiers
    (alert rule names, metric names, k8s resource kinds) that are verbatim
    in the runbooks. BM25 matches these precisely; semantic search may not
    rank them highest if the embedding space clusters them with related-but-
    different concepts.
  - Semantic search handles paraphrase well ("stale dashboards" → ELT failure)
    where BM25 has no signal.
  Together they cover the full range of how alerts are actually written.

Retrieval algorithm — fetch-wide, deduplicate-by-best, then fuse:
  For each retrieval signal (semantic, BM25):
    1. Fetch FETCH_K chunk-level results
    2. Group by runbook name, keep best-scoring chunk per runbook
    3. Produce a ranked list of runbook names

  Then:
    4. Apply RRF across the two ranked lists
    5. Return top RETURN_K RunbookMatch objects
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_chroma import Chroma

from src.config.settings import Settings, get_settings
from src.providers.embeddings import get_embedding_provider
from src.retrieval.bm25_index import BM25Index, BM25Result, reciprocal_rank_fusion

logger = logging.getLogger(__name__)

FETCH_K = 20
RETURN_K = 5


# ── Result type ───────────────────────────────────────────────────────────────


@dataclass
class RunbookMatch:
    """A single runbook returned by the retriever, with supporting evidence."""

    runbook: str
    rrf_score: float        # combined RRF score (higher = better)
    semantic_rank: int | None  # rank in the semantic results (None if not found)
    bm25_rank: int | None      # rank in the BM25 results (None if not found)
    family: str
    severity: str
    source: str             # file path to the runbook markdown


# ── Retriever ─────────────────────────────────────────────────────────────────


class RunbookRetriever:
    """
    Hybrid semantic + BM25 retriever over the embedded runbook corpus.

    Usage:
        retriever = RunbookRetriever.from_settings()
        matches = retriever.retrieve("Pod has been CrashLoopBackOff for 15 minutes")
        for m in matches:
            print(m.runbook, m.rrf_score, f"sem={m.semantic_rank} bm25={m.bm25_rank}")
    """

    def __init__(self, vectorstore: Chroma, bm25_index: BM25Index) -> None:
        self._vectorstore = vectorstore
        self._bm25 = bm25_index

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> RunbookRetriever:
        """Construct a retriever from application settings."""
        if settings is None:
            settings = get_settings()

        embeddings = get_embedding_provider(settings)
        vectorstore = Chroma(
            collection_name=settings.chroma_collection_name,
            embedding_function=embeddings,
            persist_directory=settings.chroma_persist_dir,
        )
        bm25_index = BM25Index.from_runbooks_dir(settings.runbooks_dir)

        return cls(vectorstore, bm25_index)

    def retrieve(
        self,
        query: str,
        fetch_k: int = FETCH_K,
        return_k: int = RETURN_K,
        hybrid: bool = True,
        bm25_query: str | None = None,
        family_filter: list[str] | None = None,
    ) -> list[RunbookMatch]:
        """
        Retrieve the most relevant runbooks for an incident description.

        Args:
            query:         Incident alert or description text. Used for the
                           unfiltered semantic search and, if bm25_query is not
                           provided, for BM25 keyword search too.
            fetch_k:       Raw chunks to fetch per signal before deduplication.
            return_k:      Deduplicated runbooks to return.
            hybrid:        If True (default), combine semantic + BM25 via RRF.
                           If False, return semantic-only results.
            bm25_query:    Optional separate query for BM25 keyword search.
                           When the classify node is active, this carries the
                           original query + extracted infrastructure signals so
                           that exact keyword matching benefits from vocabulary
                           expansion without distorting the semantic embedding.
                           If None, BM25 uses the same `query` as semantic.
            family_filter: When the classify node identifies incident families,
                           pass them here (e.g. ["compute", "networking"]).
                           Enables a third retrieval signal: a family-scoped
                           semantic search that forces the vector store to
                           consider only runbooks in those families. This
                           surfaces the correct runbook for vocabulary-poor
                           alerts (e.g. "service unresponsive, health checks
                           timing out" → compute-crashloop) where the plain
                           language query has no semantic overlap with the
                           runbook text but a family-restricted search does.
                           If None, the family-scoped pass is skipped.

        Returns:
            List of RunbookMatch, best match first.
        """
        semantic_ranked = self._semantic_search(query, fetch_k)

        if not hybrid:
            return self._to_matches(
                runbook_order=[rb for rb, _ in semantic_ranked],
                semantic_ranked=semantic_ranked,
                bm25_ranked=[],
                return_k=return_k,
            )

        effective_bm25_query = bm25_query if bm25_query is not None else query
        bm25_ranked = self._bm25_search(effective_bm25_query, fetch_k)

        ranked_lists = [
            [rb for rb, _ in semantic_ranked],
            [rb for rb, _ in bm25_ranked],
        ]

        # Third signal: family-scoped semantic search.
        # Restricts ChromaDB to only the classified families so the semantic
        # search cannot be drowned out by runbooks from other families that
        # happen to score better on the plain-language query text.
        family_sem_ranked: list[tuple[str, float]] = []
        if family_filter:
            family_sem_ranked = self._semantic_search_filtered(
                query, fetch_k, families=family_filter
            )
            ranked_lists.append([rb for rb, _ in family_sem_ranked])
            logger.debug(
                "retrieve: family-filtered semantic (families=%s) top5=%s",
                family_filter,
                [rb for rb, _ in family_sem_ranked[:5]],
            )

        fused = reciprocal_rank_fusion(*ranked_lists)

        logger.debug(
            "retrieve(%r) semantic=%s bm25=%s fused=%s",
            query[:60],
            [rb for rb, _ in semantic_ranked[:5]],
            [rb for rb, _ in bm25_ranked[:5]],
            [rb for rb, _ in fused[:5]],
        )

        return self._to_matches(
            runbook_order=[rb for rb, _ in fused],
            semantic_ranked=semantic_ranked,
            bm25_ranked=bm25_ranked,
            return_k=return_k,
            fused_scores={rb: score for rb, score in fused},
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _semantic_search_filtered(
        self, query: str, fetch_k: int, families: list[str]
    ) -> list[tuple[str, float]]:
        """
        Run semantic search restricted to runbooks in the specified families.

        Uses ChromaDB's metadata filter so only chunks from those families
        are considered. After deduplication, returns one result per runbook.

        This is the third retrieval signal when a classify result is present.
        It prevents high-scoring runbooks from unrelated families from crowding
        out the correct runbook for vocabulary-poor alerts.

        Args:
            query:    The query text (same as the primary semantic search).
            fetch_k:  Number of chunks to fetch before deduplication.
            families: List of family values to restrict to (must match the
                      `family` metadata field in ChromaDB, e.g. "compute",
                      "networking").

        Returns:
            List of (runbook_name, best_distance) tuples, sorted ascending.
        """
        if not families:
            return []

        if len(families) == 1:
            where = {"family": {"$eq": families[0]}}
        else:
            where = {"family": {"$in": families}}

        try:
            raw = self._vectorstore.similarity_search_with_score(
                query, k=fetch_k, filter=where
            )
        except Exception as exc:
            logger.warning("family-filtered semantic search failed (%s) — skipping", exc)
            return []

        best: dict[str, tuple[float, dict]] = {}
        for doc, score in raw:
            rb = doc.metadata["runbook"]
            if rb not in best or score < best[rb][0]:
                best[rb] = (score, doc.metadata)

        return sorted(best.items(), key=lambda x: x[1][0])

    def _semantic_search(
        self, query: str, fetch_k: int
    ) -> list[tuple[str, float]]:
        """
        Run semantic search and deduplicate to one result per runbook.

        Returns a list of (runbook_name, best_distance) tuples, sorted
        by ascending distance (lower = more similar).
        """
        raw = self._vectorstore.similarity_search_with_score(query, k=fetch_k)

        # Keep best (lowest distance) chunk per runbook
        best: dict[str, tuple[float, dict]] = {}
        for doc, score in raw:
            rb = doc.metadata["runbook"]
            if rb not in best or score < best[rb][0]:
                best[rb] = (score, doc.metadata)

        return sorted(best.items(), key=lambda x: x[1][0])

    def _bm25_search(
        self, query: str, fetch_k: int
    ) -> list[tuple[str, float]]:
        """
        Run BM25 keyword search and deduplicate to one result per runbook.

        Returns a list of (runbook_name, best_bm25_score) tuples, sorted
        by descending BM25 score (higher = more relevant).
        """
        raw: list[BM25Result] = self._bm25.search(query, k=fetch_k)

        # Keep best (highest BM25 score) chunk per runbook
        best: dict[str, float] = {}
        for result in raw:
            rb = result.runbook
            if rb not in best or result.score > best[rb]:
                best[rb] = result.score

        return sorted(best.items(), key=lambda x: x[1], reverse=True)

    def _to_matches(
        self,
        runbook_order: list[str],
        semantic_ranked: list[tuple[str, float]],
        bm25_ranked: list[tuple[str, float]],
        return_k: int,
        fused_scores: dict[str, float] | None = None,
    ) -> list[RunbookMatch]:
        """Build RunbookMatch objects preserving rank provenance."""
        semantic_rank_map = {rb: i + 1 for i, (rb, _) in enumerate(semantic_ranked)}
        bm25_rank_map = {rb: i + 1 for i, (rb, _) in enumerate(bm25_ranked)}

        # Source metadata comes from whichever signal found the runbook.
        # Build a lookup: runbook → source path.
        source_map: dict[str, str] = {}
        family_map: dict[str, str] = {}
        severity_map: dict[str, str] = {}
        for doc, _ in self._vectorstore.similarity_search_with_score("", k=1):
            pass  # warm up; real lookup below

        # Get metadata for all runbooks in the result set from the BM25 index
        # (BM25Index keeps RunbookChunk objects with all metadata)
        for chunk in self._bm25._chunks:
            rb = chunk.runbook_name
            if rb not in source_map:
                source_map[rb] = chunk.source_path
                family_map[rb] = chunk.family
                severity_map[rb] = chunk.severity

        matches = []
        for runbook in runbook_order[:return_k]:
            matches.append(
                RunbookMatch(
                    runbook=runbook,
                    rrf_score=round(fused_scores[runbook], 6) if fused_scores else 0.0,
                    semantic_rank=semantic_rank_map.get(runbook),
                    bm25_rank=bm25_rank_map.get(runbook),
                    family=family_map.get(runbook, "unknown"),
                    severity=severity_map.get(runbook, "unknown"),
                    source=source_map.get(runbook, ""),
                )
            )

        return matches
