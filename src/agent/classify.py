# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
Classification node for the incident triage agent.

This node runs before retrieval and translates plain-language incident
descriptions into infrastructure vocabulary. It solves a specific class of
retrieval failure: alerts written in user/business language ("scoring service
unresponsive") rather than Kubernetes/infra terminology ("CrashLoopBackOff",
"liveness probe", "PVC").

Without this step, both BM25 and semantic search struggle on vocabulary-poor
alerts. BM25 has nothing to match. Semantic search floats close matches from
multiple families and can misorder them.

With this step, the retrieved query carries extracted signals ("liveness probe",
"pod restart", "ingress 503") that anchor both retrieval signals to the correct
runbook family.

The classify node is intentionally lightweight:
    - One short LLM call (no retrieval needed)
    - Produces: incident families, extracted signals, augmented query
    - Falls back gracefully — if classification fails, retrieval uses raw query

The optional needs_clarification / clarification_question fields are reserved
for Phase 2.2 when the agent gains the ability to ask the caller a follow-up
before attempting triage.
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import List, Optional

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Classification schema ────────────────────────────────────────────────────


class IncidentFamily(str, Enum):
    """The seven runbook families in the corpus.

    Values match the `family:` tag used in the runbook Markdown files and
    stored in ChromaDB metadata — used for metadata-filtered retrieval.
    """
    COMPUTE = "compute"
    STORAGE = "storage"
    NETWORKING = "networking"
    DATA = "data"
    INFERENCE = "inference"
    DEPLOYMENT = "deployment"
    API = "api"


class ClassificationResult(BaseModel):
    """
    Structured output of the classify node.

    The key field is augmented_query — it is what the retriever actually uses
    for both BM25 and semantic search when this classification is present.
    """

    families: List[IncidentFamily] = Field(
        description=(
            "One or more incident families this alert most likely belongs to. "
            "Use COMPUTE for pod/container/node issues, STORAGE for disk/PVC/etcd, "
            "NETWORKING for DNS/ingress/TLS/timeout/service-mesh/load-balancer, "
            "DATA for Kafka/database/pipeline, "
            "INFERENCE for GPU/model/worker/queue, "
            "DEPLOYMENT for rollout/image-pull/config issues, "
            "API for error-rate or latency spikes at the API layer. "
            "Include multiple families if the alert spans more than one layer."
        )
    )
    infrastructure_signals: List[str] = Field(
        description=(
            "Specific infrastructure terms and Kubernetes concepts implied by the "
            "alert, even if not stated explicitly. Examples: 'CrashLoopBackOff', "
            "'liveness probe failure', 'OOMKilled', 'PVC mount failure', "
            "'ingress 503', 'certificate expiry', 'kafka consumer lag'. "
            "Extract the vocabulary that a Kubernetes operator would use to describe "
            "what this alert is actually reporting. 3-8 terms."
        )
    )
    augmented_query: str = Field(
        description=(
            "The original alert text with the extracted infrastructure signals "
            "appended. Keep the full original text first, then add the signals. "
            "This is what retrieval will use. Example format: "
            "'<original alert> [signals: CrashLoopBackOff liveness probe failure "
            "pod restart ingress 503 recent deployment]'"
        )
    )
    needs_clarification: bool = Field(
        default=False,
        description=(
            "True only if the alert is so ambiguous that retrieval will produce "
            "low-quality results and a single targeted question would significantly "
            "improve the triage. False for almost all alerts — only set True if "
            "there are truly no infrastructure signals to work with."
        )
    )
    clarification_question: Optional[str] = Field(
        default=None,
        description=(
            "If needs_clarification is True: one specific question whose answer "
            "would let classification proceed. Otherwise null."
        )
    )


# ── Prompt ───────────────────────────────────────────────────────────────────


_CLASSIFY_SYSTEM = """\
You are an infrastructure incident classification assistant.

Your job is to read an incident alert — which may be written in plain business
language or in Kubernetes/infrastructure terminology — and extract the
underlying infrastructure concepts and failure modes.

Runbook families available (use exact values shown):
  compute    — Pod/container/node issues: CrashLoopBackOff, OOMKilled, high CPU,
               node not ready, resource quota, pending pods, liveness/readiness probes
  storage    — Disk pressure, PVC mount failure, etcd disk, log volume
  networking — DNS failure, ingress 5xx, TLS/certificate expiry, timeout,
               service mesh errors, load balancer
  data       — Kafka consumer lag, ELT pipeline failure, DB connection pool,
               DB replication lag
  inference  — GPU OOM, model load failure, request queue depth, throughput drop,
               ML worker crash
  deployment — Stuck rollout, image pull error, config/secret issues
  api        — API error rate spikes, API latency spikes, request routing issues

You must translate user-language descriptions into the infrastructure vocabulary
that would appear in Kubernetes alerts and runbooks.

IMPORTANT rules for signal extraction:
- Only extract signals that the alert DIRECTLY implies. Do not speculate.
- "pod appears to be running but not accepting connections" → DIRECTLY implies
  liveness probe failure, CrashLoopBackOff, container restart
- "last deploy was N minutes ago" → this is context/timeline information.
  Do NOT add stuck-rollout or image-pull-error signals unless the alert also
  explicitly says the deploy is stuck, pods are failing to start, or images
  are being pulled. A recent deploy is timing context, not a signal.
- Focus on the observable failure mode described, not possible causes.

Examples of correct translation:
  "service is unresponsive, health checks timing out, pod appears running" →
      families: [compute, network], signals: [liveness probe failure, CrashLoopBackOff,
      readiness probe timeout, pod not accepting connections, ingress 503]
      NOTE: do NOT add stuck-rollout signals just because a recent deploy is mentioned.

  "database queries are very slow since this morning" →
      families: [data], signals: [db connection pool exhaustion, query latency,
      replication lag, database saturation]

  "GPUs are throwing errors and jobs are failing" →
      families: [inference], signals: [GPU OOM, CUDA error, model load failure,
      worker crash, VRAM exhaustion]
"""

_CLASSIFY_HUMAN = """\
Incident alert:

{query}

Classify this incident and extract infrastructure vocabulary that will improve \
runbook retrieval.
"""

CLASSIFY_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", _CLASSIFY_SYSTEM),
        ("human", _CLASSIFY_HUMAN),
    ]
)


# ── Node factory ─────────────────────────────────────────────────────────────


def make_classify_node(settings):
    """
    Return a classify node function bound to the given LLM provider.

    The classify node runs first in the graph, before retrieval.
    It translates the raw query into structured classification + augmented query.

    On any failure (LLM error, parse error), it logs and returns gracefully
    so the retrieve node falls back to the raw query.
    """
    from src.providers.llm import get_llm_provider

    llm = get_llm_provider(settings)
    structured_llm = llm.with_structured_output(ClassificationResult)
    chain = CLASSIFY_PROMPT | structured_llm

    def classify(state: dict) -> dict:
        query = state["query"]
        logger.info("classify: analyzing query '%s'", query[:80])

        t0 = time.perf_counter()
        try:
            result: ClassificationResult = chain.invoke({"query": query})
        except Exception as exc:
            logger.warning(
                "classify: failed after %.0fms (%s) — retrieval will use raw query",
                (time.perf_counter() - t0) * 1000,
                exc,
            )
            return {"classification": None}
        elapsed_ms = (time.perf_counter() - t0) * 1000

        logger.info(
            "classify: families=%s signals=%s (%.0fms)",
            [f.value for f in result.families],
            result.infrastructure_signals,
            elapsed_ms,
        )

        if result.needs_clarification:
            logger.info(
                "classify: needs clarification — %s", result.clarification_question
            )

        return {"classification": result}

    return classify
