# Copyright (c) 2026 Nick Persing
# Licensed under the MIT License. See LICENSE for details.

"""
Incident Triage Demo — Streamlit UI wired to the live FastAPI backend.

Start the API first:
    uvicorn src.api.main:app --port 8000

Then run this from the project root:
    streamlit run demo/app.py
"""
from __future__ import annotations

import logging
import os

import requests
import streamlit as st

from src.config.logging_config import configure_logging

configure_logging("INFO")
logger = logging.getLogger(__name__)

API_BASE = os.environ.get("API_BASE", "http://localhost:8000")

st.set_page_config(page_title="Incident Triage Playground", layout="wide")

# ── Scenario presets ──────────────────────────────────────────────────────────

SCENARIOS = {
    "Compute": {
        "Pod CrashLoopBackOff": "[CRITICAL] KubePodCrashLooping pod=inference-worker-7d9f4b8c6-xkp2q namespace=ml-serving restartCount=14\nPod has been crash looping for the last 22 minutes.",
        "Node high CPU": "[WARNING] HighCPUUsage node=worker-03 cpu_pct=94.2 duration=12m\nNode CPU has been above 90% for 12 consecutive minutes.",
        "OOMKilled container": "[CRITICAL] OOMKilled pod=scoring-api-6f8b9d-zq7rx namespace=serving container=api\nContainer terminated: OOMKilled. Memory limit: 4Gi.",
        "Pending pods": "[WARNING] PodsPending namespace=ml-serving pending_count=6 duration=8m\nPods stuck in Pending state, no nodes available.",
        "Resource quota exceeded": "[WARNING] ResourceQuotaExceeded namespace=ml-serving resource=limits.memory\nNamespace has hit its memory quota ceiling.",
    },
    "Networking": {
        "DNS resolution failure": "[CRITICAL] DNSResolutionFailure service=feature-store namespace=ml-serving\nDNS lookups for feature-store.ml-serving.svc.cluster.local timing out.",
        "Ingress 502 errors": "[WARNING] Ingress502Errors ingress=api-gateway error_rate=18% duration=5m\nLoad balancer receiving 502s from upstream pods.",
        "TLS certificate expiry": "[WARNING] CertificateExpiringSoon host=api.internal.example.com days_remaining=6",
        "Network timeout": "[CRITICAL] NetworkTimeout src=scoring-service dst=feature-store timeout_pct=34% duration=4m",
        "Service mesh error": "[WARNING] ServiceMeshError src=api-server dst=inference-worker error_rate=8% circuit_breaker=open",
    },
    "Inference / GPU": {
        "GPU out of memory": "[CRITICAL] GPUOutOfMemory node=gpu-worker-04 gpu=0 allocated_mb=79800 limit_mb=80000\nCUDA OOM errors in inference worker logs.",
        "Request queue depth": "[WARNING] InferenceQueueDepth queue=llm-requests depth=4200 threshold=1000 duration=8m",
        "Model load failure": "[CRITICAL] ModelLoadFailure worker=inference-worker-2 model=llama-70b\nWorker failed to load model weights. Pod restarting.",
        "Throughput drop": "[WARNING] InferenceThroughputDrop service=llm-serving throughput_rps=12 baseline_rps=95 duration=10m",
        "Worker crash": "[CRITICAL] InferenceWorkerCrash worker=inference-worker-3 exit_code=139\nSIGSEGV in CUDA kernel during forward pass.",
    },
    "Data / Pipeline": {
        "Kafka consumer lag": "[WARNING] KafkaConsumerLag consumer_group=feature-pipeline topic=events lag=92000 threshold=10000",
        "DB connection pool exhausted": "[CRITICAL] DBConnectionPoolExhausted service=api-server pool_size=50 active=50 waiting=34",
        "ELT pipeline failure": "[CRITICAL] ELTPipelineFailure pipeline=feature-refresh stage=transform duration_min=47\nPipeline has not produced output in 47 minutes.",
        "DB replication lag": "[WARNING] DBReplicationLag replica=pg-replica-02 lag_seconds=312 threshold_seconds=30",
    },
    "API": {
        "Latency spike (p99)": "[WARNING] APILatencySpike endpoint=POST /v1/completions p99_ms=4800 threshold_ms=2000 duration=6m",
        "Error rate spike (5xx)": "[CRITICAL] APIErrorRateSpike endpoint=POST /v1/completions error_rate=12.4% threshold=1% duration=3m",
    },
    "Storage": {
        "Disk pressure": "[WARNING] NodeDiskPressure node=worker-05 disk_pct=91 threshold=85",
        "PVC mount failure": "[CRITICAL] PVCMountFailure pod=data-loader-abc namespace=pipeline pvc=data-vol\nPod stuck in ContainerCreating — PVC not mounting.",
        "Log volume full": "[WARNING] LogVolumeFull node=worker-02 path=/var/log used_pct=97",
    },
    "Deployment": {
        "Stuck rollout": "[WARNING] DeploymentRolloutStuck deployment=api-server namespace=prod duration=18m\nRollout has not progressed. New pods not becoming Ready.",
        "Image pull error": "[WARNING] ImagePullError pod=api-server-new-6d7f8-xk2qp namespace=prod\nErrImagePull: 401 Unauthorized pulling registry.example.com/api-server:v1.4.2",
    },
}

SEVERITY_BADGE = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
CONFIDENCE_BADGE = {"high": "✅", "medium": "⚠️", "low": "❓"}


# ── API helpers ───────────────────────────────────────────────────────────────

def check_health() -> dict | None:
    logger.debug("health: GET %s/health", API_BASE)
    try:
        r = requests.get(f"{API_BASE}/health", timeout=10)
        if r.ok:
            data = r.json()
            logger.debug(
                "health: ok — llm=%s/%s embedding=%s/%s runbooks=%s",
                data.get("llm_provider"),
                data.get("llm_model"),
                data.get("embedding_provider"),
                data.get("embedding_model"),
                data.get("runbooks_indexed"),
            )
            return data
        logger.warning("health: %s %s", r.status_code, r.text[:120])
        return None
    except requests.RequestException as exc:
        logger.debug("health: unreachable (%s)", exc)
        return None


def run_triage(query: str) -> tuple[dict | None, str | None]:
    """Returns (response_dict, error_message)."""
    logger.debug("triage: POST query=%r", query[:120])
    try:
        r = requests.post(
            f"{API_BASE}/triage",
            json={"query": query},
            timeout=300,
        )
        if r.ok:
            data = r.json()
            logger.debug(
                "triage: ok — latency_ms=%s severity=%s confidence=%s runbooks=%s",
                data.get("latency_ms"),
                data.get("plan", {}).get("severity"),
                data.get("plan", {}).get("confidence"),
                [m["runbook"] for m in data.get("retrieval", [])],
            )
            return data, None
        error = f"API error {r.status_code}: {r.json().get('detail', r.text)}"
        logger.warning("triage: %s", error)
        return None, error
    except requests.ConnectionError as exc:
        msg = "Could not reach the API. Is `uvicorn src.api.main:app --port 8000` running?"
        logger.warning("triage: connection error (%s)", exc)
        return None, msg
    except requests.Timeout:
        logger.warning("triage: timed out after 120s")
        return None, "Request timed out after 300s."


# ── Layout ────────────────────────────────────────────────────────────────────

st.title("Incident Triage Playground")

# API status banner
health = check_health()
if health:
    st.success(
        f"API live — {health['llm_provider']}/{health['llm_model']} · "
        f"embeddings: {health['embedding_provider']}/{health['embedding_model']} · "
        f"{health['runbooks_indexed']} runbooks indexed",
        icon="✅",
    )
else:
    st.error(
        "API offline — start it with: `uvicorn src.api.main:app --port 8000`",
        icon="🔴",
    )

st.divider()

left, right = st.columns([1, 2], gap="large")

# ── Left panel ────────────────────────────────────────────────────────────────
with left:
    st.subheader("Build an Incident")

    family = st.selectbox("Incident family", list(SCENARIOS.keys()))
    scenario = st.selectbox("Scenario", list(SCENARIOS[family].keys()))

    alert_text = st.text_area(
        "Alert text (editable)",
        value=SCENARIOS[family][scenario],
        height=140,
        key=f"alert_{family}_{scenario}",
    )

    run_clicked = st.button(
        "Triage this incident",
        type="primary",
        use_container_width=True,
        disabled=health is None,
    )

# ── Trigger triage ────────────────────────────────────────────────────────────
if run_clicked:
    logger.debug("triage triggered: family=%r scenario=%r query=%r", family, scenario, alert_text[:120])
    st.session_state["triage_loading"] = True
    st.session_state.pop("triage_result", None)

# ── Left panel continued: retrieval metadata (below the button) ───────────────
with left:
    if "triage_result" in st.session_state:
        data = st.session_state["triage_result"]

        st.divider()
        st.subheader("Classification")

        clf = data.get("classification")
        if clf:
            st.markdown("**Families**")
            for f in clf.get("families", []):
                st.markdown(f"- `{f}`")
            st.markdown("**Infrastructure signals**")
            for s in clf.get("infrastructure_signals", []):
                st.markdown(f"- `{s}`")
        else:
            st.caption("Classify node did not run or returned no result.")

        st.divider()
        st.subheader("Runbooks retrieved")
        st.caption("Top results by RRF score")

        for m in data.get("retrieval", []):
            with st.container(border=True):
                st.markdown(f"**{m['runbook']}**")
                st.caption(f"family: {m.get('family', '—')} · severity: {m.get('severity', '—')}")
                cols = st.columns(3)
                cols[0].metric("Semantic", m.get("semantic_rank", "—"))
                cols[1].metric("BM25", m.get("bm25_rank", "—"))
                cols[2].metric("RRF", f"{m['rrf_score']:.4f}")

# ── Right panel: triage plan ──────────────────────────────────────────────────
with right:
    if st.session_state.get("triage_loading"):
        with st.spinner("Running triage agent…"):
            result, error = run_triage(alert_text)
        st.session_state.pop("triage_loading", None)
        if error:
            st.error(error)
        else:
            st.session_state["triage_result"] = result
        st.rerun()

    if "triage_result" not in st.session_state:
        st.info("Select a scenario and click **Triage this incident** to see results.")
    else:
        data = st.session_state["triage_result"]
        plan = data["plan"]

        severity = plan["severity"]
        confidence = plan["confidence"]
        badge = SEVERITY_BADGE.get(severity, "⚪")
        conf_badge = CONFIDENCE_BADGE.get(confidence, "")

        st.subheader(f"{badge} {severity.upper()} — Triage Plan")
        st.caption(
            f"Latency: {data['latency_ms']}ms · "
            f"Confidence: {conf_badge} {confidence} — {plan['confidence_reason']} · "
            f"Provider: {data['llm_provider']}"
        )

        st.markdown(f"**Summary:** {plan['incident_summary']}")
        st.markdown(f"**Likely cause:** {plan['likely_cause']}")
        st.markdown(
            "**Affected components:** "
            + ", ".join(f"`{c}`" for c in plan.get("affected_components", []))
        )

        st.divider()

        dcol, rcol = st.columns(2, gap="medium")

        with dcol:
            st.markdown("**Diagnostic steps**")
            for i, step in enumerate(plan.get("diagnostic_steps", []), 1):
                st.markdown(f"{i}. {step}")

        with rcol:
            st.markdown("**Resolution steps**")
            for i, step in enumerate(plan.get("resolution_steps", []), 1):
                st.markdown(f"{i}. {step}")

        st.divider()
        st.markdown("**Escalation criteria**")
        for criterion in plan.get("escalation_criteria", []):
            st.warning(criterion, icon="⚠️")
