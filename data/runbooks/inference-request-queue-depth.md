# Request Queue Depth — Inference Request Queue Backing Up

## Overview
Inference serving systems (vLLM, TGI) maintain an internal request queue for requests that cannot be immediately scheduled onto the GPU (e.g., because all KV cache slots are occupied). A growing queue means the system is accepting requests faster than it can process them. Queued requests see increased latency (time-to-first-token grows) and are at risk of hitting client timeouts before processing begins. A queue that never drains is a sign that the system is fundamentally undersized for the current request rate or request complexity (long context lengths).

## Alert Signatures
- `InferenceQueueDepthHigh` — `vllm:num_requests_waiting > <threshold>` (e.g., > 10 for interactive, > 50 for batch)
- `vllm:time_to_first_token_seconds_p99` elevated
- Client-side: request timeout errors, `504 Gateway Timeout`, or `429 Too Many Requests`
- Alertmanager: `[HIGH] Inference queue depth > <N> for <service>`

## Common Causes
- Insufficient GPU replicas for the current request rate (capacity undersized)
- Long context length requests monopolizing KV cache slots, blocking shorter requests
- A single slow request (very long output) holding a slot for many seconds
- Traffic spike (e.g., a new client integration, a scheduled batch job)
- One or more inference workers crashed, reducing available capacity (see [inference-worker-crash.md](inference-worker-crash.md))
- `max_num_seqs` (maximum concurrent sequences) set too low for available VRAM
- GPU OOM events causing partial request processing and re-queuing
- Batching strategy suboptimal — continuous batching not enabled or misconfigured

## Diagnostic Steps
1. Check current queue depth and running requests:
   ```bash
   # Prometheus metrics
   # vllm:num_requests_waiting  -- queued, not yet scheduled
   # vllm:num_requests_running  -- currently on GPU
   # vllm:num_requests_swapped  -- swapped to CPU (if swap is enabled)
   ```
2. Check GPU utilization to understand if the GPU is the bottleneck:
   ```bash
   kubectl exec -it <pod-name> -n <namespace> -- nvidia-smi
   # If GPU util is 100% and queue is growing, capacity is the constraint
   # If GPU util is low and queue is growing, check for software/config issue
   ```
3. Check time-to-first-token and total request duration metrics:
   - `vllm:time_to_first_token_seconds` histogram (p50, p95, p99)
   - `vllm:e2e_request_latency_seconds` histogram
4. Check if long-context requests are dominating the queue:
   ```bash
   kubectl logs <pod-name> -n <namespace> --tail=200 | grep -E "prompt_tokens|completion_tokens|num_tokens"
   ```
5. Check whether any worker pods are down or in backoff:
   ```bash
   kubectl get pods -n <namespace> -l <inference-label>
   ```
6. Check if there has been a traffic spike (compare current request rate to baseline):
   - `rate(vllm:request_success_total[5m])` vs. historical baseline

## Resolution Steps
1. **If capacity is genuinely insufficient (GPU util at 100%):** Scale up the inference deployment to more GPU replicas:
   ```bash
   kubectl scale deployment <inference-deployment> -n <namespace> --replicas=<N+1>
   ```
   Ensure GPU nodes are available (check [compute-pending-pods.md](compute-pending-pods.md) if new pods stay Pending).
2. **If a worker is down:** Restart it to restore capacity (see [inference-worker-crash.md](inference-worker-crash.md)).
3. **If long-context requests are blocking shorter ones:** Enable priority scheduling or chunked prefill in vLLM:
   ```bash
   # vLLM startup args:
   --enable-chunked-prefill
   --scheduler-policy priority  # if supported in your version
   ```
4. **If `max_num_seqs` is too low:** Increase it (check available VRAM first):
   ```bash
   # vLLM startup: --max-num-seqs 256  (from e.g. 128)
   ```
5. **If traffic is a one-time spike:** Apply rate limiting at the API gateway to shed load and protect the queue:
   ```yaml
   # nginx annotation example
   nginx.ingress.kubernetes.io/limit-rps: "50"
   ```
6. **If the queue never drains and more GPUs aren't immediately available:** Enable request queueing with a client-visible 429 response rather than silently timing out queued requests — gives clients a signal to retry.

## Escalation Criteria
Escalate to the on-call engineer if:
- Queue depth is growing and additional GPU capacity cannot be provisioned within the SLO window
- Requests are being dropped or timing out at the client before reaching the GPU
- The queue growth is caused by a bug in the serving framework (requests never completing)
- A major traffic event is expected to continue (capacity planning required)

## Ownership

| Layer | Team | Contact |
|---|---|---|
| Inference capacity, scaling policy, queue config | **ML Infrastructure** | Slack: `#ml-infra-oncall` · PD: `ml-infrastructure` |
| GPU node provisioning (adding capacity) | **Hardware & Datacenter Ops** (bare-metal) or cloud autoscaler via **ML Infrastructure** | Slack: `#dc-ops-oncall` or `#ml-infra-oncall` |
| API rate limiting, client request rate | **Application Team** (API consumer) or **Platform Engineering** (gateway) | Depends on where rate limiting is applied |

**Boundary:** ML Infrastructure owns queue depth and is responsible for capacity decisions. Adding GPU capacity requires coordination: ML Infrastructure requests nodes from Hardware & Datacenter Ops (bare-metal) or provisions cloud instances (they own that workflow). Rate limiting to protect the queue is ML Infrastructure's call at the inference API layer, or Platform Engineering's call at the ingress/gateway layer.

## Related Runbooks
- [inference-gpu-oom.md](inference-gpu-oom.md) — OOM events reduce effective serving capacity
- [inference-worker-crash.md](inference-worker-crash.md) — worker downtime causes queue to grow
- [inference-throughput-drop.md](inference-throughput-drop.md) — lower throughput with same request rate increases queue
- [api-latency-spike.md](api-latency-spike.md)

## Tags
`family: inference`
`severity: high`
`services: vllm, tgi, inference-serving, gpu`
