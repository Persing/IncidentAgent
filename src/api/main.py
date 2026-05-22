# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
Incident Triage Agent — FastAPI application.

Endpoints:
    POST /triage    — Run the triage agent on an incident description.
    GET  /health    — Confirm the agent is warm and report provider info.
    GET  /runbooks  — List the indexed runbook corpus.

The graph (retriever + LLM) is built once at startup via the lifespan
context manager and stored in app.state. Each request reuses it — no
cold-start penalty per request.

Run locally:
    uvicorn src.api.main:app --reload --port 8000

Example request:
    curl -s -X POST http://localhost:8000/triage \
        -H "Content-Type: application/json" \
        -d '{"query": "Pod CrashLoopBackOff, restartCount=14, namespace=ml-serving"}' \
        | python3 -m json.tool
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

from src.agent.classify import ClassificationResult
from src.agent.prompts import TriagePlan
from src.agent.triage_agent import build_graph, run_triage_full_async
from src.config.logging_config import configure_logging
from src.config.settings import Settings, get_settings
from src.config.tracing import build_run_config
from src.plugins.base import TriageResult
from src.retrieval.retriever import RunbookMatch

logger = logging.getLogger(__name__)


# ── Request / response models ─────────────────────────────────────────────────


class TriageRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=10,
        description="The incident alert text or description to triage.",
        examples=["[CRITICAL] KubePodCrashLooping pod=api-server-xyz namespace=prod restartCount=8"],
    )
    return_k: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Number of runbooks to retrieve and use as context (1–10).",
    )


class RetrievedRunbook(BaseModel):
    """Retrieval evidence for a single runbook — included in the response
    so callers can see exactly which runbooks informed the triage plan."""

    runbook: str
    family: str
    severity: str
    rrf_score: float
    semantic_rank: Optional[int] = None
    bm25_rank: Optional[int] = None


class TriageResponse(BaseModel):
    plan: TriagePlan
    retrieval: list[RetrievedRunbook]
    classification: Optional[ClassificationResult]
    latency_ms: float
    embedding_provider: str
    llm_provider: str


class HealthResponse(BaseModel):
    status: str
    embedding_provider: str
    embedding_model: str
    llm_provider: str
    llm_model: str
    runbooks_indexed: int


class RunbookSummary(BaseModel):
    name: str
    family: str
    severity: str
    path: str


# ── Lifespan — build graph once at startup ────────────────────────────────────


@asynccontextmanager
async def lifespan(api_app: FastAPI):
    """
    FastAPI lifespan context manager.

    Builds the retriever (ChromaDB + BM25 index) and LangGraph once at
    startup. Both are stored in app.state and reused across all requests.
    Startup typically takes 1–3 seconds depending on the embedding provider.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    logger.info(
        "Starting up — embedding=%s/%s llm=%s/%s log_level=%s",
        settings.embedding_provider,
        settings.active_embedding_model,
        settings.llm_provider,
        settings.active_llm_model,
        settings.log_level,
    )

    try:
        graph = build_graph(settings)
    except Exception as e:
        logger.error("Failed to build triage graph at startup: %s", e)
        raise

    # Load and cache alert + output plugins (trigger self-registration on import)
    import src.plugins.alerts.webhook  # noqa: F401
    import src.plugins.outputs.webhook  # noqa: F401
    from src.plugins.registry import registry as plugin_registry

    api_app.state.graph = graph
    api_app.state.settings = settings
    api_app.state.alert_plugin = plugin_registry.get_alert(settings.alert_plugin)()
    api_app.state.output_plugin = plugin_registry.get_output(settings.output_plugin)()

    logger.info("Triage agent ready.")
    yield

    # Shutdown — nothing to clean up for local ChromaDB / in-memory BM25
    logger.info("Shutting down.")


# ── App ───────────────────────────────────────────────────────────────────────


app = FastAPI(
    title="Incident Triage Agent",
    description=(
        "RAG-based agent that retrieves relevant runbooks for an incident "
        "alert and produces a structured triage plan."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


# ── Request logging middleware ────────────────────────────────────────────────


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every request with method, path, status code, and latency."""
    t0 = time.perf_counter()
    response = await call_next(request)
    latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    request_id = response.headers.get("X-Request-Id", "-")
    logger.info(
        "%s %s %s %sms request_id=%s",
        request.method,
        request.url.path,
        response.status_code,
        latency_ms,
        request_id,
    )
    return response


# ── Endpoints ─────────────────────────────────────────────────────────────────


@app.post(
    "/triage",
    response_model=TriageResponse,
    summary="Triage an incident",
    response_description="Structured triage plan with retrieval evidence.",
)
async def triage(request_body: TriageRequest, response: Response) -> TriageResponse:
    """
    Run the triage agent on an incident description or alert.

    The agent:
    1. Embeds the query and runs hybrid (semantic + BM25) retrieval
    2. Loads the full content of the top-k matched runbooks
    3. Prompts the LLM with the alert + runbook context
    4. Returns a structured TriagePlan with severity, steps, and escalation criteria

    The `retrieval` field in the response shows which runbooks were retrieved
    and their semantic / BM25 rank — useful for debugging retrieval quality.
    """
    settings: Settings = app.state.settings
    graph = app.state.graph
    alert_plugin = app.state.alert_plugin
    output_plugin = app.state.output_plugin

    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    normalized = alert_plugin.normalize(request_body.model_dump())

    run_config = build_run_config(
        settings=settings,
        tags=["api"],
        metadata={
            "request_id": request_id,
            "query_preview": normalized.raw_text[:120],
            "return_k": normalized.return_k,
        },
    )

    try:
        state = await run_triage_full_async(
            query=normalized.raw_text,
            return_k=normalized.return_k,
            graph=graph,
            run_config=run_config,
        )
    except Exception:
        logger.exception("Triage failed request_id=%s query=%r", request_id, normalized.raw_text[:80])
        raise HTTPException(
            status_code=503,
            detail=f"Triage agent unavailable. Reference ID: {request_id}",
        )

    latency_ms = round((time.perf_counter() - t0) * 1000, 1)

    plan: TriagePlan = state["triage_plan"]
    matches: list[RunbookMatch] = state["runbook_matches"]
    classification: Optional[ClassificationResult] = state.get("classification")

    if plan is None:
        raise HTTPException(
            status_code=503,
            detail="Agent produced no triage plan. Check LLM provider connectivity.",
        )

    # Add headers for easy monitoring/correlation without parsing the body
    response.headers["X-Latency-Ms"] = str(latency_ms)
    response.headers["X-Request-Id"] = request_id

    triage_result = TriageResult(
        alert=normalized,
        plan=plan,
        matches=matches,
        latency_ms=latency_ms,
        request_id=request_id,
    )
    asyncio.create_task(output_plugin.deliver(triage_result, settings.output_plugin_config))

    return TriageResponse(
        plan=plan,
        retrieval=[
            RetrievedRunbook(
                runbook=m.runbook,
                family=m.family,
                severity=m.severity,
                rrf_score=m.rrf_score,
                semantic_rank=m.semantic_rank,
                bm25_rank=m.bm25_rank,
            )
            for m in matches
        ],
        classification=classification,
        latency_ms=latency_ms,
        embedding_provider=settings.embedding_provider,
        llm_provider=settings.llm_provider,
    )


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
)
async def health() -> HealthResponse:
    """
    Confirm the agent is running and report provider configuration.

    Returns 200 if the graph was built successfully at startup.
    The graph being warm means ChromaDB, the BM25 index, and the LLM
    provider connection have all been initialized.
    """
    settings: Settings = app.state.settings
    runbooks_dir = Path(settings.runbooks_dir)
    runbook_count = len(list(runbooks_dir.glob("*.md")))

    return HealthResponse(
        status="ok",
        embedding_provider=settings.embedding_provider,
        embedding_model=settings.active_embedding_model,
        llm_provider=settings.llm_provider,
        llm_model=settings.active_llm_model,
        runbooks_indexed=runbook_count,
    )


@app.get(
    "/runbooks",
    response_model=list[RunbookSummary],
    summary="List indexed runbooks",
)
async def list_runbooks() -> list[RunbookSummary]:
    """
    List all runbooks in the indexed corpus with their family and severity.

    Reads the Tags section from each Markdown file. Useful for understanding
    what the agent knows about and for debugging retrieval misses.
    """
    settings: Settings = app.state.settings
    runbooks_dir = Path(settings.runbooks_dir)

    summaries = []
    tag_pattern = re.compile(r"`(family|severity):\s*([^`]+)`")

    for path in sorted(runbooks_dir.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        tags = {"family": "unknown", "severity": "unknown"}
        for m in tag_pattern.finditer(content):
            tags[m.group(1)] = m.group(2).strip()

        summaries.append(
            RunbookSummary(
                name=path.stem,
                family=tags["family"],
                severity=tags["severity"],
                path=str(path),
            )
        )

    return summaries


