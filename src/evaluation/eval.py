# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
Retrieval quality evaluation harness.

Measures how well the retriever surfaces the correct runbooks for a labeled
set of incident descriptions. These metrics — Recall@k and MRR — are the
standard way to evaluate RAG retrieval quality and are what a production
team would track on every change to the embedding model or chunking strategy.

Metrics:
    Recall@k  — fraction of test cases where the primary expected runbook
                appears anywhere in the top-k results. The most intuitive
                measure: "did we find the right runbook?"

    MRR       — Mean Reciprocal Rank. For each case, take 1/rank of the
                first correct result, then average across all cases.
                Penalizes cases where the right answer is buried at rank 5
                vs. rank 1. A perfect retriever has MRR=1.0.

Both metrics are computed against primary_runbook only (the single most
relevant runbook per test case), which gives a clean signal. The
expected_runbooks list is used for a supplementary multi-runbook recall
metric that measures whether ALL expected runbooks were retrieved.

Usage:
    # From the project root:
    python -m src.evaluation.eval

    # With custom k values:
    python -m src.evaluation.eval --k 1 3 5

    # Save results to JSON:
    python -m src.evaluation.eval --output results/eval_$(date +%Y%m%d).json
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from src.config.settings import Settings, get_settings
from src.config.tracing import build_run_config
from src.retrieval.retriever import RunbookRetriever

logger = logging.getLogger(__name__)

DEFAULT_K_VALUES = [1, 3, 5]
TEST_CASES_PATH = "data/test-cases.yaml"


# ── Result data structures ───────────────────────────────────────────────────


@dataclass
class CaseResult:
    """Result for a single test case."""

    id: str
    query_preview: str          # first 80 chars of the alert
    primary_runbook: str        # the single ground-truth runbook
    expected_runbooks: list[str]
    retrieved_runbooks: list[str]  # ordered, best first
    primary_rank: int | None    # rank of primary_runbook in results (1-indexed), None if not found
    all_expected_found: bool    # were ALL expected_runbooks retrieved?
    latency_ms: float
    difficulty: str
    category: str


@dataclass
class EvalReport:
    """Aggregated evaluation results across all test cases."""

    timestamp: str
    embedding_provider: str
    embedding_model: str
    fetch_k: int
    return_k: int
    total_cases: int
    recall_at_k: dict[int, float]        # {1: 0.75, 3: 0.87, 5: 0.91}
    mrr: float
    multi_recall_at_k: dict[int, float]  # recall counting all expected runbooks
    avg_latency_ms: float
    by_difficulty: dict[str, dict]       # recall@5 broken down by easy/medium/hard
    by_category: dict[str, dict]         # recall@5 broken down by category
    cases: list[CaseResult] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "=" * 60,
            "RETRIEVAL EVAL RESULTS",
            "=" * 60,
            f"Provider : {self.embedding_provider} / {self.embedding_model}",
            f"Cases    : {self.total_cases}",
            f"Fetch k  : {self.fetch_k}  →  Return k: {self.return_k}",
            "",
            "── Primary Runbook Recall ──────────────────────────────",
        ]
        for k, r in sorted(self.recall_at_k.items()):
            bar = "█" * int(r * 20)
            lines.append(f"  Recall@{k:<2}  {r:.3f}  {bar}")

        lines += [
            "",
            f"  MRR       {self.mrr:.3f}",
            "",
            "── Multi-Runbook Recall (all expected in top-k) ────────",
        ]
        for k, r in sorted(self.multi_recall_at_k.items()):
            bar = "█" * int(r * 20)
            lines.append(f"  Recall@{k:<2}  {r:.3f}  {bar}")

        lines += [
            "",
            f"  Avg latency: {self.avg_latency_ms:.0f}ms",
            "",
            "── By Difficulty ───────────────────────────────────────",
        ]
        for diff, stats in sorted(self.by_difficulty.items()):
            lines.append(
                f"  {diff:<8}  n={stats['n']}  recall@5={stats['recall_at_5']:.3f}  mrr={stats['mrr']:.3f}"
            )

        lines += ["", "── By Category ─────────────────────────────────────────"]
        for cat, stats in sorted(self.by_category.items()):
            lines.append(
                f"  {cat:<16}  n={stats['n']}  recall@5={stats['recall_at_5']:.3f}  mrr={stats['mrr']:.3f}"
            )

        lines += ["", "── Misses (primary not in top-5) ───────────────────────"]
        misses = [c for c in self.cases if c.primary_rank is None or c.primary_rank > 5]
        if misses:
            for c in misses:
                lines.append(f"  [{c.id}] {c.primary_runbook}")
                lines.append(f"         \"{c.query_preview}\"")
                lines.append(f"         got: {c.retrieved_runbooks}")
        else:
            lines.append("  None — all primary runbooks retrieved in top-5 ✓")

        lines.append("=" * 60)
        return "\n".join(lines)


# ── Core evaluation logic ────────────────────────────────────────────────────


def load_test_cases(path: str = TEST_CASES_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["test_cases"]


def run_eval(
    settings: Settings | None = None,
    k_values: list[int] = DEFAULT_K_VALUES,
    test_cases_path: str = TEST_CASES_PATH,
    with_classify: bool = False,
) -> EvalReport:
    """
    Run the full retrieval evaluation against the labeled test set.

    Args:
        settings:        Application settings.
        k_values:        List of k values to compute Recall@k for.
        test_cases_path: Path to the test-cases YAML file.
        with_classify:   If True, run the LLM classify node before retrieval
                         to translate plain-language alerts into infrastructure
                         vocabulary. Adds ~1-3s per test case (LLM call).
                         Default False keeps the fast embeddings-only path.

    Returns:
        EvalReport with all metrics and per-case results.
    """
    if settings is None:
        settings = get_settings()

    test_cases = load_test_cases(test_cases_path)
    max_k = max(k_values)

    # Build retriever — no LLM needed for the retrieval step itself
    retriever = RunbookRetriever.from_settings(settings)

    # Build the classify chain only if requested (requires LLM)
    classify_chain = None
    if with_classify:
        from src.agent.classify import CLASSIFY_PROMPT, ClassificationResult
        from src.providers.llm import get_llm_provider

        llm = get_llm_provider(settings)
        classify_chain = CLASSIFY_PROMPT | llm.with_structured_output(ClassificationResult)
        logger.info("Classify-augmented eval enabled (LLM: %s)", settings.llm_provider)

    logger.info(
        "Running eval: %d cases, k=%s, provider=%s/%s%s",
        len(test_cases),
        k_values,
        settings.embedding_provider,
        _embedding_model_name(settings),
        " +classify" if with_classify else "",
    )

    case_results: list[CaseResult] = []

    for tc in test_cases:
        tc_id = tc["id"]
        query = tc["alert_description"].strip()
        primary = _normalise_runbook_name(tc["primary_runbook"])
        expected = [_normalise_runbook_name(r) for r in tc["expected_runbooks"]]

        # Build a run config so eval traces are tagged separately from
        # production traffic in LangSmith. Each test case gets its own
        # named trace, making it easy to find and inspect individual cases.
        tags = ["evaluation"]
        if with_classify:
            tags.append("classify")
        run_config = build_run_config(
            settings=settings,
            run_name=f"eval_{tc_id}",
            tags=tags,
            metadata={
                "test_case_id": tc_id,
                "difficulty": tc.get("difficulty", "unknown"),
                "category": tc.get("category", "unknown"),
                "primary_runbook": primary,
                "with_classify": with_classify,
            },
        )

        # Optionally augment BM25 via the classify node.
        # Semantic search always uses the raw query — changing the embedded
        # text would hurt cases that already have good vocabulary. The
        # extracted signals are passed as bm25_query only, boosting keyword
        # matching for vocabulary-poor alerts without touching semantic search.
        bm25_query = None
        family_filter = None
        if classify_chain is not None:
            try:
                result = classify_chain.invoke({"query": query})
                if result.infrastructure_signals:
                    signals_text = " ".join(result.infrastructure_signals)
                    bm25_query = f"{query} {signals_text}"
                    family_filter = [f.value for f in result.families]
                    logger.debug(
                        "[%s] classify: families=%s signals=%s",
                        tc_id,
                        family_filter,
                        result.infrastructure_signals,
                    )
            except Exception as exc:
                logger.warning("[%s] classify failed: %s — using raw query", tc_id, exc)

        t0 = time.perf_counter()
        matches = retriever.retrieve(
            query,
            return_k=max(max_k, 5),
            bm25_query=bm25_query,
            family_filter=family_filter,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

        retrieved = [m.runbook for m in matches]

        # Find rank of primary runbook (1-indexed)
        primary_rank = None
        for i, rb in enumerate(retrieved, 1):
            if rb == primary:
                primary_rank = i
                break

        # Were all expected runbooks retrieved in top-k?
        all_found = all(rb in retrieved[:max_k] for rb in expected)

        case_results.append(
            CaseResult(
                id=tc_id,
                query_preview=query[:80].replace("\n", " "),
                primary_runbook=primary,
                expected_runbooks=expected,
                retrieved_runbooks=retrieved,
                primary_rank=primary_rank,
                all_expected_found=all_found,
                latency_ms=round(elapsed_ms, 1),
                difficulty=tc.get("difficulty", "unknown"),
                category=tc.get("category", "unknown"),
            )
        )

        rank_str = f"rank {primary_rank}" if primary_rank else "MISS"
        logger.debug("[%s] %s → %s (%s)", tc_id, tc_id, rank_str, f"{elapsed_ms:.0f}ms")

    return _aggregate(case_results, k_values, settings)


def _aggregate(
    cases: list[CaseResult],
    k_values: list[int],
    settings: Settings,
) -> EvalReport:
    """Aggregate per-case results into an EvalReport."""
    import datetime

    n = len(cases)

    # Recall@k — fraction where primary is in top k
    recall_at_k = {}
    for k in k_values:
        hits = sum(1 for c in cases if c.primary_rank is not None and c.primary_rank <= k)
        recall_at_k[k] = round(hits / n, 4)

    # MRR — mean of 1/rank for each case (0 if not found)
    mrr = round(
        sum(1 / c.primary_rank if c.primary_rank else 0.0 for c in cases) / n,
        4,
    )

    # Multi-runbook recall: fraction where ALL expected runbooks appear in top-k
    max_k = max(k_values)
    multi_recall_at_k = {}
    for k in k_values:
        hits = sum(
            1 for c in cases
            if all(rb in c.retrieved_runbooks[:k] for rb in c.expected_runbooks)
        )
        multi_recall_at_k[k] = round(hits / n, 4)

    avg_latency = round(sum(c.latency_ms for c in cases) / n, 1)

    # Break down by difficulty
    by_difficulty: dict[str, dict] = {}
    for diff in sorted({c.difficulty for c in cases}):
        subset = [c for c in cases if c.difficulty == diff]
        by_difficulty[diff] = _subset_stats(subset, max_k)

    # Break down by category
    by_category: dict[str, dict] = {}
    for cat in sorted({c.category for c in cases}):
        subset = [c for c in cases if c.category == cat]
        by_category[cat] = _subset_stats(subset, max_k)

    return EvalReport(
        timestamp=datetime.datetime.utcnow().isoformat(),
        embedding_provider=settings.embedding_provider,
        embedding_model=_embedding_model_name(settings),
        fetch_k=20,   # matches FETCH_K in retriever
        return_k=max_k,
        total_cases=n,
        recall_at_k=recall_at_k,
        mrr=mrr,
        multi_recall_at_k=multi_recall_at_k,
        avg_latency_ms=avg_latency,
        by_difficulty=by_difficulty,
        by_category=by_category,
        cases=cases,
    )


def _subset_stats(cases: list[CaseResult], k: int) -> dict:
    n = len(cases)
    if n == 0:
        return {"n": 0, "recall_at_5": 0.0, "mrr": 0.0}
    recall = sum(1 for c in cases if c.primary_rank is not None and c.primary_rank <= k) / n
    mrr = sum(1 / c.primary_rank if c.primary_rank else 0.0 for c in cases) / n
    return {"n": n, "recall_at_5": round(recall, 4), "mrr": round(mrr, 4)}


def _normalise_runbook_name(name: str) -> str:
    """Strip .md extension so comparisons work regardless of how names are stored."""
    return name.removesuffix(".md")


def _embedding_model_name(settings: Settings) -> str:
    if settings.embedding_provider == "openai":
        return settings.openai_embedding_model
    elif settings.embedding_provider == "ollama":
        return settings.ollama_embedding_model
    elif settings.embedding_provider == "huggingface":
        return settings.hf_embedding_model
    return "unknown"


# ── CLI ──────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(description="Run retrieval quality evaluation")
    parser.add_argument(
        "--k",
        nargs="+",
        type=int,
        default=DEFAULT_K_VALUES,
        metavar="K",
        help="k values for Recall@k (default: 1 3 5)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        metavar="PATH",
        help="Optional path to save results as JSON",
    )
    parser.add_argument(
        "--classify",
        action="store_true",
        default=False,
        help=(
            "Run the LLM classify node before retrieval to augment vocabulary-poor "
            "queries. Adds ~1-3s per test case. Requires LLM provider to be configured."
        ),
    )
    args = parser.parse_args()

    report = run_eval(k_values=args.k, with_classify=args.classify)
    print("\n" + report.summary())

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Convert dataclasses to dicts for JSON serialization
        report_dict = asdict(report)
        with open(out_path, "w") as f:
            json.dump(report_dict, f, indent=2, default=str)
        print(f"\nResults saved to {out_path}")
