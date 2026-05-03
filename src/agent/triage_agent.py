# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
Incident triage agent — LangGraph implementation.

Phase 1 graph:
    START → retrieve → generate → END

Phase 2 graph (current):
    START → classify → retrieve → generate → END

The classify node runs first and translates plain-language alerts into
infrastructure vocabulary (families, signals, augmented query). The retrieve
node uses the augmented query when available, falling back to the raw query.
This directly addresses the class of retrieval failures where the alert has
no Kubernetes/infra terminology (e.g. "scoring service unresponsive, health
checks timing out" → "CrashLoopBackOff liveness probe ingress 503").

Phase 3 will add:
    START → classify → retrieve → generate → END
                  ↘ clarify ↗   (conditional on needs_clarification)

The graph is compiled once at module level and reused across requests.
Each invocation is stateless — state is passed through the graph, not stored.

Usage:
    from src.agent.triage_agent import run_triage

    plan = run_triage("Pod has been CrashLoopBackOff for 15 minutes")
    print(plan.severity, plan.likely_cause)
    print(plan.diagnostic_steps)
"""

from __future__ import annotations

import logging
import time
from typing import Optional, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from src.agent.classify import ClassificationResult, make_classify_node
from src.agent.prompts import TRIAGE_PROMPT, TriagePlan, format_runbook_context
from src.config.settings import Settings, get_settings
from src.providers.llm import get_llm_provider
from src.retrieval.retriever import RunbookMatch, RunbookRetriever

logger = logging.getLogger(__name__)


# ── Graph state ──────────────────────────────────────────────────────────────


class TriageState(TypedDict):
    """
    State that flows through the triage graph.

    Each node reads from this dict and returns a partial update.
    LangGraph merges the updates into the running state automatically.

    Fields:
        query            — raw incident alert text from the caller.
        return_k         — number of runbooks to retrieve (default 3).
        classification   — structured output from the classify node; None if
                           classification failed or was skipped.
        runbook_matches  — ranked RunbookMatch objects from the retrieve node.
        context          — full runbook Markdown loaded for the LLM.
        triage_plan      — final structured output from the generate node.
    """

    query: str
    return_k: int
    classification: Optional[ClassificationResult]
    runbook_matches: list[RunbookMatch]
    context: str
    triage_plan: Optional[TriagePlan]


# ── Node functions ───────────────────────────────────────────────────────────


def make_retrieve_node(retriever: RunbookRetriever):
    """
    Return a retrieve node function bound to the given retriever instance.

    Fetches the top-k runbooks for the incident query, then loads the full
    markdown content for each matched runbook to use as LLM context.

    Phase 2: if a ClassificationResult is present in state, uses the
    augmented_query (original text + extracted infrastructure signals) instead
    of the raw query. This improves retrieval for vocabulary-poor alerts.
    """

    def retrieve(state: TriageState) -> dict:
        raw_query = state["query"]
        classification: Optional[ClassificationResult] = state.get("classification")

        # Extract infrastructure signals from classification for BM25 boosting.
        # The original query is always used for semantic search — changing it
        # would distort the embedding for queries that already have good
        # vocabulary. The signals are passed as a separate BM25 query so that
        # exact keyword matching benefits from vocabulary expansion without
        # affecting the semantic branch.
        bm25_query: Optional[str] = None
        family_filter: Optional[list[str]] = None

        if classification and classification.infrastructure_signals:
            signals_text = " ".join(classification.infrastructure_signals)
            bm25_query = f"{raw_query} {signals_text}"
            family_filter = [f.value for f in classification.families]
            logger.info(
                "retrieve: BM25 + family-filtered semantic (families=%s) signals=%s",
                family_filter,
                signals_text,
            )
        else:
            logger.info("retrieve: no classification — using raw query for all signals")

        logger.info("retrieve: semantic query '%s'", raw_query[:120])

        matches = retriever.retrieve(
            raw_query,
            return_k=state.get("return_k", 3),
            bm25_query=bm25_query,
            family_filter=family_filter,
        )

        logger.info(
            "retrieve: matched runbooks: %s",
            [m.runbook for m in matches],
        )

        # Load full runbook content for the matched files.
        # The retriever found which runbooks are relevant; the LLM needs
        # the full content to produce specific diagnostic/resolution steps.
        context = format_runbook_context([m.source for m in matches])

        return {
            "runbook_matches": matches,
            "context": context,
        }

    return retrieve


def make_generate_node(settings: Settings):
    """
    Return a generate node function bound to the given LLM provider.

    Constructs the prompt from the incident query + retrieved runbook context,
    calls the LLM with structured output, and returns the TriagePlan.
    """
    llm = get_llm_provider(settings)
    structured_llm = llm.with_structured_output(TriagePlan)
    chain = TRIAGE_PROMPT | structured_llm

    def generate(state: TriageState) -> dict:
        query = state["query"]
        context = state["context"]

        logger.info("generate: invoking LLM (%s)", settings.llm_provider)

        t0 = time.perf_counter()
        triage_plan = chain.invoke({"query": query, "context": context})
        elapsed_ms = (time.perf_counter() - t0) * 1000

        logger.info(
            "generate: produced plan — severity=%s confidence=%s (%.0fms)",
            triage_plan.severity,
            triage_plan.confidence,
            elapsed_ms,
        )

        return {"triage_plan": triage_plan}

    return generate


# ── Graph assembly ───────────────────────────────────────────────────────────


def build_graph(settings: Settings | None = None) -> StateGraph:
    """
    Assemble and compile the triage LangGraph.

    Phase 2 graph:
        START → classify → retrieve → generate → END

    The classify node translates plain-language alerts into infrastructure
    vocabulary before retrieval. If it fails, retrieval falls back to the
    raw query — the graph never blocks on a classification error.

    Args:
        settings: Application settings. Defaults to get_settings().

    Returns:
        A compiled LangGraph runnable.
    """
    if settings is None:
        settings = get_settings()

    retriever = RunbookRetriever.from_settings(settings)

    classify_node = make_classify_node(settings)
    retrieve_node = make_retrieve_node(retriever)
    generate_node = make_generate_node(settings)

    graph = StateGraph(TriageState)
    graph.add_node("classify", classify_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("generate", generate_node)

    graph.add_edge(START, "classify")
    graph.add_edge("classify", "retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)

    return graph.compile()


# ── Public API ───────────────────────────────────────────────────────────────


# Module-level compiled graph — built once, reused across calls.
# Lazy-initialised on first call to run_triage() / run_triage_full().
# The FastAPI layer builds the graph explicitly at startup via build_graph()
# and passes it in, bypassing this singleton entirely.
_graph = None


def _get_or_build_graph(graph, settings: Settings | None) -> object:
    """Return the provided graph, or build and cache the module-level singleton."""
    global _graph
    if graph is not None:
        return graph
    if _graph is None:
        _graph = build_graph(settings)
    return _graph


def run_triage_full(
    query: str,
    return_k: int = 3,
    graph=None,
    settings: Settings | None = None,
    run_config: RunnableConfig | None = None,
) -> TriageState:
    """
    Run the full triage pipeline and return the complete graph state.

    Use this when you need both the TriagePlan and the retrieval metadata
    (which runbooks were retrieved, their semantic/BM25 ranks).
    The FastAPI layer uses this to populate the full TriageResponse.

    For simple script usage, prefer run_triage() which returns just the plan.

    Args:
        query:      The incident alert text or description.
        return_k:   Number of runbooks to retrieve and use as context (default 3).
        graph:      A pre-built compiled LangGraph. If None, uses the
                    module-level singleton (built on first call).
        settings:   Application settings. Ignored if graph is provided.
        run_config: Optional RunnableConfig with tracing metadata and tags.
                    Build one with src.config.tracing.build_run_config().
                    If None, the graph runs without per-request metadata
                    (tracing still works if LANGCHAIN_TRACING_V2=true,
                    just without custom tags/metadata).

    Returns:
        The final TriageState dict containing 'triage_plan' and
        'runbook_matches'.
    """
    g = _get_or_build_graph(graph, settings)

    initial_state: TriageState = {
        "query": query,
        "return_k": return_k,
        "classification": None,
        "runbook_matches": [],
        "context": "",
        "triage_plan": None,
    }

    return g.invoke(initial_state, config=run_config)


async def run_triage_full_async(
    query: str,
    return_k: int = 3,
    graph=None,
    settings: Settings | None = None,
    run_config: RunnableConfig | None = None,
) -> TriageState:
    """
    Async version of run_triage_full for use in async contexts (e.g. FastAPI).

    Uses ainvoke so the event loop is not blocked during LLM calls and
    retrieval. Accepts the same arguments as run_triage_full.
    """
    g = _get_or_build_graph(graph, settings)

    initial_state: TriageState = {
        "query": query,
        "return_k": return_k,
        "classification": None,
        "runbook_matches": [],
        "context": "",
        "triage_plan": None,
    }

    return await g.ainvoke(initial_state, config=run_config)


def run_triage(
    query: str,
    settings: Settings | None = None,
) -> TriagePlan:
    """
    Run the full triage pipeline for an incident description.

    Args:
        query:    The incident alert text or description.
        settings: Application settings. Defaults to get_settings().

    Returns:
        A populated TriagePlan with severity, steps, and escalation criteria.
    """
    return run_triage_full(query=query, settings=settings)["triage_plan"]
