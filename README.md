# Incident Triage Agent

A RAG-based agent that takes an incident alert or description as input, retrieves the most relevant runbooks from a knowledge base, and produces a structured triage plan: what the issue likely is, what to check first, step-by-step resolution guidance, and escalation criteria.

## Background

I spent 18 months as primary on-call for a ~3,800 GPU, 50-region LLM inference service at Oracle Cloud Infrastructure. I authored runbooks specifically so support staff could triage incidents without escalating to me. This project builds a system that does the same thing: given an alert, find the right runbook and produce an actionable plan.

The goal is not a demo. It is a system engineered with production thinking: typed output, a pluggable provider layer, an eval harness that measures retrieval quality, and a runbook corpus that reflects how incidents actually present — not how textbook examples describe them.

---

## What it does

```
Alert text or incident description
        │
        ▼
  LLM classify node — extracts incident families + infrastructure signals
  (translates "service unresponsive, health checks failing" →
   families=[compute, networking], signals=[CrashLoopBackOff, liveness probe, ...])
        │
        ▼
  Hybrid retrieval — three signals fused via RRF:
    1. Semantic search (original query → embedding model)
    2. BM25 keyword search (query + extracted signals)
    3. Family-filtered semantic search (signals within classified families)
  ChromaDB — 252 section-level chunks from 28 runbooks
  Fetch k=20 chunks → deduplicate by best score per runbook → top 3
        │
        ▼
  Full runbook markdown loaded as context
        │
        ▼
  LLM generate node (mistral-small / gpt-4o / claude-3-5-sonnet — pluggable)
        │
        ▼
  TriagePlan — 10 typed fields, Pydantic-validated
  severity · likely_cause · diagnostic_steps · resolution_steps
  escalation_criteria · runbooks_referenced · confidence
```

---

## Current state

Phase 2 is complete and working end-to-end:

| Component | Status | Notes |
|---|---|---|
| Runbook corpus | ✅ | 28 runbooks, 7 incident families, 8-section schema |
| Ingestion pipeline | ✅ | Section-level chunking, idempotent upsert to ChromaDB |
| Retrieval | ✅ | Fetch-wide / deduplicate-by-best, 50ms avg latency |
| Provider abstraction | ✅ | OpenAI / Anthropic / Ollama — swap via `.env` |
| Triage agent | ✅ | LangGraph three-node graph (classify → retrieve → generate), structured output |
| Eval harness | ✅ | Recall@k and MRR across 24 labeled test cases |
| Hybrid retrieval (BM25 + semantic) | ✅ | RRF fusion, Recall@5 0.917→0.958, MRR 0.802→0.889 |
| FastAPI layer | ✅ | `POST /triage`, `GET /health`, `GET /runbooks` |
| LangSmith tracing | ✅ | Per-request metadata, provider tags, eval separation |
| LangGraph classify node | ✅ | Phase 2 graph: classify → retrieve → generate. Recall@5 1.000, MRR 0.931 |

---

## Retrieval eval results

24 labeled test cases across 4 difficulty categories (direct, semantic, multi-runbook, confusable).
Three-stage progression: semantic-only baseline → hybrid (BM25 + semantic) → runbook content improvement:

```
                Baseline   Hybrid   Phase 2   Delta (total)
Recall@1         0.708     0.833    0.875      +0.167
Recall@3         0.875     0.958    1.000      +0.125
Recall@5         0.917     0.958    1.000      +0.083
MRR              0.802     0.889    0.931      +0.129
Avg latency       49ms      96ms     50ms
```

**By category (Phase 2 — no misses):**

| Category | n | Recall@5 | MRR | Notes |
|---|---|---|---|---|
| direct | 5 | 1.000 | 1.000 | Explicit alert names / metric labels |
| multi_runbook | 6 | 1.000 | 0.917 | Co-triggered incidents, multiple expected runbooks |
| confusable | 7 | 1.000 | 0.833 | Similar-sounding alerts, different correct runbook |
| semantic | 6 | 1.000 | 1.000 | User-language descriptions, no k8s terminology ← was 0.833 |

**How Phase 2 eliminated the last miss** (`tc_005` — "Scoring service unresponsive, health checks timing out"):

The root cause was a vocabulary gap: the plain-language alert had zero semantic overlap with the compute-crashloop runbook, which only described the Kubernetes alert perspective (`CrashLoopBackOff`, `restartCount`). Both BM25 and semantic search failed to surface it.

The fix was a runbook content improvement: the Overview section now explicitly describes the user-facing presentation — "service completely unresponsive, health checks time out, pod does not accept connections, 503 errors from load balancer". Semantic search immediately found it at rank 1.

The lesson: vocabulary-poor retrieval failures often point to vocabulary-poor runbooks. The classify node was built to handle this via query augmentation, but the correct fix was richer runbook content — which also makes the runbook more useful to an on-call engineer reading it under pressure.

The classify node (`START → classify → retrieve → generate`) still ships as Phase 2 infrastructure. It augments BM25 with extracted infrastructure signals and enables family-filtered semantic search. Eval with `--classify` shows Recall@5 1.000, MRR 0.910 — the classify signals improve Recall@1 on edge cases and the family filter provides an additional retrieval pathway for future vocabulary gaps.

---

## Setup

### Prerequisites

- Python 3.9+
- [uv](https://docs.astral.sh/uv/) — `brew install uv` or `pip install uv`
- One of:
  - [Ollama](https://ollama.com) running locally (free, no API key)
  - OpenAI API key
  - Anthropic API key

### Install

```bash
git clone https://github.com/Persing/IncidentAgent
cd incident-triage-agent

# Install with your chosen provider(s)
uv pip install -e ".[ollama]"          # local — no API key needed
uv pip install -e ".[openai]"          # OpenAI
uv pip install -e ".[anthropic,openai]"  # Anthropic LLM + OpenAI embeddings
uv pip install -e ".[all]"             # every provider
```

> **Note:** Anthropic does not offer an embeddings API. If you set `LLM_PROVIDER=anthropic`, you still need a separate embedding provider (`openai` or `ollama` or `huggingface`).

### Configure

```bash
cp .env.example .env
```

Edit `.env`. Minimum required fields depend on your provider choice:

**Ollama (local — no API key):**
```env
EMBEDDING_PROVIDER=ollama
LLM_PROVIDER=ollama
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
OLLAMA_LLM_MODEL=mistral-small3.2  # or any model you have pulled
```

**OpenAI:**
```env
EMBEDDING_PROVIDER=openai
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

**Anthropic LLM + OpenAI embeddings:**
```env
EMBEDDING_PROVIDER=openai
LLM_PROVIDER=anthropic
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
```

### Pull Ollama models (if using Ollama)

```bash
ollama pull nomic-embed-text    # embeddings — 274MB
ollama pull mistral-small3.2    # LLM — 15GB, best quality locally
# or:
ollama pull llama3              # smaller (4.7GB), less reliable on structured output
```

### Ingest runbooks

Loads, chunks, embeds, and stores all 28 runbooks into ChromaDB. Safe to re-run — upserts by deterministic chunk ID, no duplicates.

```bash
python -m src.ingestion.loader
```

Expected output:
```
Runbooks processed : 28
Chunks created     : 252
Chunks skipped     : 0
```

### Verify retrieval quality

```bash
python -m src.evaluation.eval
```

Runs 24 labeled test cases through the retriever only (no LLM, completes in ~1s). Prints Recall@k and MRR. Save results for comparison:

```bash
python -m src.evaluation.eval --output results/eval_$(date +%Y%m%d).json
```

To also test the classify node's vocabulary augmentation (adds one LLM call per test case, ~3–5 minutes total):

```bash
python -m src.evaluation.eval --classify
python -m src.evaluation.eval --classify --output results/eval_classify_$(date +%Y%m%d).json
```

### Run the agent (Python)

```python
from src.agent.triage_agent import run_triage, run_triage_full

# Simple — returns just the TriagePlan
plan = run_triage("""
[CRITICAL] KubePodCrashLooping
pod=inference-worker-7d9f4b8c6-xkp2q namespace=ml-serving
restartCount=14
Pod has been crash looping for the last 22 minutes.
""")

print(f"Severity    : {plan.severity}")
print(f"Likely cause: {plan.likely_cause}")
print(f"Runbooks    : {plan.runbooks_referenced}")

for i, step in enumerate(plan.diagnostic_steps, 1):
    print(f"  {i}. {step}")

# Full state — also returns classification and retrieval metadata
state = run_triage_full("Scoring service completely unresponsive, health checks timing out")

classification = state["classification"]
print(f"Families    : {[f.value for f in classification.families]}")
print(f"Signals     : {classification.infrastructure_signals}")

for m in state["runbook_matches"]:
    print(f"  {m.runbook}  sem={m.semantic_rank}  bm25={m.bm25_rank}  rrf={m.rrf_score:.4f}")
```

### Run the API

```bash
uvicorn src.api.main:app --port 8000
```

The graph (classify node + retriever + LLM) warms up at startup (~2–3s locally). Three endpoints:

**Health check:**
```bash
curl http://localhost:8000/health
```
```json
{
  "status": "ok",
  "embedding_provider": "ollama",
  "embedding_model": "nomic-embed-text",
  "llm_provider": "ollama",
  "llm_model": "mistral-small3.2:24b",
  "runbooks_indexed": 28
}
```

**Triage an incident:**
```bash
curl -s -X POST http://localhost:8000/triage \
  -H "Content-Type: application/json" \
  -d '{"query": "[CRITICAL] KubePodCrashLooping pod=inference-worker namespace=ml-serving restartCount=14"}' \
  | python3 -m json.tool
```

The response includes:
- `plan` — the full `TriagePlan` (severity, likely cause, diagnostic steps, resolution steps, escalation criteria, confidence)
- `retrieval` — which runbooks were retrieved with their semantic rank, BM25 rank, and RRF score
- `classification` — the classify node output: incident families, extracted infrastructure signals, and whether clarification was needed
- `latency_ms` — end-to-end latency including classify + retrieval + generation

The `X-Latency-Ms` and `X-Request-Id` response headers carry those values for easy monitoring without body parsing.

**List indexed runbooks:**
```bash
curl http://localhost:8000/runbooks
```

Interactive API docs (Swagger UI) are available at `http://localhost:8000/docs` when the server is running.

### Run the demo UI

```bash
uv pip install -e ".[demo]"
streamlit run demo/app.py
```

The Streamlit UI connects to the running API. Select an incident family and scenario from the left panel — the alert text is editable so you can modify it before triaging. The left panel shows the classify node output (incident families, extracted signals) and which runbooks were retrieved with their semantic rank, BM25 rank, and RRF score. The right panel shows the full triage plan.

---

## Project structure

```
incident-triage-agent/
│
├── data/
│   ├── runbooks/               # 28 markdown runbooks, 7 incident families
│   │   ├── compute-*.md
│   │   ├── storage-*.md
│   │   ├── network-*.md
│   │   ├── data-*.md
│   │   ├── inference-*.md
│   │   └── deployment-*.md / api-*.md
│   ├── team-directory.md       # Team ownership reference (escalation contacts)
│   └── test-cases.yaml         # 24 labeled eval cases with difficulty/category
│
├── src/
│   ├── config/
│   │   ├── settings.py         # Pydantic settings, loaded from .env
│   │   └── tracing.py          # LangSmith RunnableConfig factory
│   ├── providers/
│   │   ├── embeddings.py       # Embedding provider factory (OpenAI/Ollama/HuggingFace)
│   │   └── llm.py              # LLM provider factory (OpenAI/Anthropic/Ollama)
│   ├── ingestion/
│   │   └── loader.py           # Parse → chunk by ## section → embed → upsert ChromaDB
│   ├── retrieval/
│   │   ├── retriever.py        # Hybrid retriever: semantic + BM25 via RRF; family-filtered pass
│   │   └── bm25_index.py       # BM25 index with camelCase tokenization and RRF fusion
│   ├── agent/
│   │   ├── classify.py         # ClassificationResult schema + classify node (Phase 2)
│   │   ├── prompts.py          # TriagePlan schema + prompt templates
│   │   └── triage_agent.py     # LangGraph graph: classify → retrieve → generate
│   └── evaluation/
│       └── eval.py             # Recall@k and MRR eval harness
│
├── demo/
│   └── app.py                  # Streamlit demo UI — select a scenario, run live triage
├── results/                    # Eval output JSON files (gitignored)
├── .env.example                # All config options documented
├── pyproject.toml              # Optional dep groups per provider
└── README.md
```

---

## Design decisions

### Section-level chunking, not token-count chunking

Each runbook is split on `##` headers rather than by a fixed token count. This produces 9 semantically coherent chunks per runbook — "Diagnostic Steps", "Resolution Steps", etc. — rather than arbitrary splits that cut across ideas mid-sentence.

The retriever then does something slightly non-obvious with these chunks:

### Fetch-wide, deduplicate-by-best

A naive top-k query over section-level chunks has a deduplication problem. If the correct runbook has 9 sections, any of which might be the best match, it can be pushed out of the top-3 results by three other runbooks each contributing one slightly-better-scoring section.

The fix: fetch `k=20` raw chunks, group by runbook name, keep the lowest-distance section per runbook, then return the top-n runbooks by that score. This consistently improves recall with no extra embedding cost.

### Structured output over prose

Every agent response is a `TriagePlan` Pydantic model with typed fields. This is not a stylistic choice — it is what a real system consuming this agent would need. Structured output is serialisable, diffable, and testable. Prose is none of those things.

### Pluggable provider layer

The embedding and LLM providers are resolved at runtime via factory functions that do lazy imports. Swapping from Ollama to OpenAI is a two-line `.env` change. No code changes, no import errors for packages you did not install. The rest of the codebase (`loader.py`, `retriever.py`, `triage_agent.py`) does not know which provider it is using.

### Eval set built alongside the corpus

The 24 labeled test cases were written at the same time as the runbooks, not after. This matters: writing the eval forces you to think about what "correct retrieval" actually means for ambiguous cases. The test set includes confusable pairs (CPU saturation vs. OOMKilled, DNS failure vs. network timeout) specifically to measure precision, not just recall.

### Classify node: vocabulary bridge, not retrieval hack

The one remaining eval miss (`tc_005`) had no infrastructure vocabulary at all — "service unresponsive, health checks timing out" — so both BM25 and semantic search had nothing to anchor on. The Phase 2 classify node addresses this by running a lightweight LLM call before retrieval to extract infrastructure signals (`CrashLoopBackOff`, `liveness probe failure`) from plain-language descriptions.

The implementation uses those signals in two ways: as additional BM25 terms (vocabulary expansion without distorting the semantic embedding), and as the query for a family-filtered semantic search pass (restricts ChromaDB to the classified families so the correct runbook can't be crowded out by unrelated-family results). All three signals are fused via RRF.

The actual fix for `tc_005`, though, was a runbook content improvement: the compute-crashloop Overview now describes the user-facing presentation ("service completely unresponsive, health checks time out, not accepting connections, 503 from load balancer"). Semantic search immediately found it. The lesson: vocabulary-poor retrieval often means vocabulary-poor runbooks. The classify node handles edge cases; richer content handles the root cause.

---

## Runbook corpus

28 runbooks across 7 incident families. Each runbook has a consistent 8-section schema:
`Overview · Alert Signatures · Common Causes · Diagnostic Steps · Resolution Steps · Escalation Criteria · Ownership · Related Runbooks`

The **Ownership** section is non-standard and intentional. It documents which team owns which layer of the incident, where the responsibility boundary is, and how to reach each team's on-call. This is the section an on-call engineer needs most under pressure and is almost always missing from demo runbooks.

| Family | Runbooks | Examples |
|---|---|---|
| Compute | 6 | high-cpu, oom-killed, crashloop, node-not-ready, resource-quota, pending-pods |
| Storage | 4 | disk-pressure, pvc-mount-failure, etcd-disk, log-volume-full |
| Networking | 5 | dns-failure, timeout, certificate-expiry, ingress-502, service-mesh-error |
| Data / Pipeline | 4 | kafka-consumer-lag, elt-pipeline-failure, db-connection-pool, db-replication-lag |
| Inference / GPU | 5 | gpu-oom, worker-crash, model-load-failure, request-queue-depth, throughput-drop |
| Deployment | 2 | stuck-rollout, image-pull-error |
| API | 2 | api-latency-spike, api-error-rate-spike |

---

## Roadmap

### Completed

1. ~~**Hybrid retrieval**~~ ✅ — BM25 + semantic via RRF. Recall@5 0.917 → 0.958, MRR 0.802 → 0.889.
2. ~~**FastAPI layer**~~ ✅ — `POST /triage`, `GET /health`, `GET /runbooks`. Graph warms at startup via lifespan.
3. ~~**LangSmith tracing**~~ ✅ — Per-request metadata, provider tags, eval/production trace separation.
4. ~~**LangGraph classify node**~~ ✅ — `classify → retrieve → generate`. Translates plain-language alerts to infrastructure vocabulary. Recall@5 1.000, MRR 0.931.

### Next

5. **Eval for generation quality** — build a small labeled set of (alert, expected_triage_plan) pairs and score LLM output quality (severity accuracy, step completeness, escalation criteria).
6. **Clarifying-question node** — conditional `classify → clarify → retrieve → generate` path for truly ambiguous alerts where even the classify node outputs `needs_clarification=True`.

---

## Dependencies

Core (always installed):
- `langchain`, `langchain-core`, `langchain-chroma`, `chromadb` — RAG framework and vector store
- `langgraph` — agent graph
- `rank-bm25` — BM25 keyword index for hybrid retrieval
- `pydantic-settings` — typed settings from `.env`
- `fastapi`, `uvicorn` — API layer
- `pyyaml` — test case loading

Provider-specific (install only what you need):
- `langchain-openai` — OpenAI embeddings and GPT-4o
- `langchain-anthropic` — Anthropic Claude
- `langchain-ollama` — local models via Ollama
- `langchain-huggingface` + `sentence-transformers` — fully local embeddings, no network required after download
