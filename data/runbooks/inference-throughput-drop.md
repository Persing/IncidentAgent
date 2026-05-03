# Inference Throughput Drop — Tokens/sec or Req/sec Falls Below Baseline

## Overview
Inference throughput is the primary capacity metric for a model serving system: tokens generated per second (generation throughput) and requests completed per second. A throughput drop without a corresponding reduction in request rate means the system is generating the same output with less efficiency — either individual requests are taking longer, batching is less effective, or GPU utilization has dropped. Throughput drops translate directly to longer user-visible latency and reduced system capacity. They are often silent at first — the system continues serving but with degraded efficiency before request queues begin to back up.

## Alert Signatures
- `InferenceThroughputDrop` — `rate(vllm:generation_tokens_total[5m]) < <baseline_threshold>`
- `vllm:gpu_cache_usage_perc` drops (fewer requests being batched — batch efficiency reduced)
- `vllm:e2e_request_latency_seconds_p50` increasing while request rate stays flat
- Alertmanager: `[HIGH] Inference generation throughput dropped > 20% from baseline`

## Common Causes
- Shift in request distribution — fewer short, batch-friendly requests and more long-context or streaming requests
- One or more GPU replicas down, reducing total capacity without reducing incoming load (see [inference-worker-crash.md](inference-worker-crash.md))
- Continuous batching disrupted — a long-running prefill blocking efficient decode batching
- Thermal throttling on GPU — GPU clock speed reduced due to heat (common on bare-metal or poorly cooled nodes)
- GPU memory pressure causing frequent KV cache eviction or swapping to CPU
- A model or tokenizer update changed the compute characteristics of the workload
- Framework regression after a vLLM version upgrade
- CPU bottleneck in pre/post-processing (tokenization, de-tokenization) limiting the GPU pipeline

## Diagnostic Steps
1. Compare current throughput to historical baseline:
   - `rate(vllm:generation_tokens_total[5m])` — generation tokens/sec
   - `rate(vllm:prompt_tokens_total[5m])` — prefill throughput
   - Check if the drop is sustained or intermittent
2. Check GPU utilization and clock speed:
   ```bash
   kubectl exec -it <pod-name> -n <namespace> -- nvidia-smi -q | grep -E "Utilization|Clocks|Throttle"
   # SM clock throttling reasons: nvidia-smi --query-gpu=clocks_throttle_reasons.active --format=csv
   ```
3. Check request length distribution for shifts:
   - `vllm:request_prompt_tokens` histogram
   - `vllm:request_generation_tokens` histogram
   - A shift toward longer prompts increases prefill time and reduces decode batching efficiency.
4. Check KV cache utilization and swap activity:
   - `vllm:gpu_cache_usage_perc` — if near 100%, KV cache is full and requests are being queued
   - `vllm:num_requests_swapped` — CPU swap usage hurts throughput significantly
5. Check CPU utilization on the inference pod for tokenization bottleneck:
   ```bash
   kubectl top pod <pod-name> -n <namespace> --containers
   kubectl exec -it <pod-name> -n <namespace> -- top -bn1 | head -20
   ```
6. Check if a recent vLLM or model update coincides with the throughput drop:
   ```bash
   kubectl rollout history deployment/<inference-deployment> -n <namespace>
   ```

## Resolution Steps
1. **If a worker is down:** Restore capacity (see [inference-worker-crash.md](inference-worker-crash.md)). Throughput per request is unchanged but total system throughput is reduced by 1/N.
2. **If GPU thermal throttling:** Check node temperatures and ensure cooling is functional. If on cloud instances, the node may need to be replaced:
   ```bash
   kubectl exec -it <pod-name> -n <namespace> -- nvidia-smi -q | grep "GPU 00000000"
   # Look for: "Thermal Violation" in throttle reasons
   ```
   Cordon the affected node and reschedule to a healthy GPU node.
3. **If KV cache is full and requests are swapping:** Reduce `max_num_seqs` or add GPU replicas. Avoid CPU swap — it degrades throughput by 5-10x.
4. **If request distribution shifted to longer contexts:** This is an organic capacity issue. Options: add GPU replicas, implement request length-based routing, or apply a maximum context length limit.
5. **If framework regression after upgrade:** Roll back the vLLM image to the previous version:
   ```bash
   kubectl set image deployment/<inference-deployment> <container>=<previous-image-tag> -n <namespace>
   ```
6. **If CPU is the bottleneck on tokenization:** Increase CPU request/limit for the pod, or offload tokenization to a separate service.

## Escalation Criteria
Escalate to the on-call engineer if:
- Throughput drop is > 40% of baseline and cannot be explained by request distribution shift
- GPU thermal throttling is persistent and node replacement is needed
- A vLLM rollback did not restore throughput (possible deeper model or hardware issue)
- The throughput drop is causing SLO breaches on p95 or p99 latency

## Ownership

| Layer | Team | Contact |
|---|---|---|
| vLLM config, batching strategy, framework version | **ML Infrastructure** | Slack: `#ml-infra-oncall` · PD: `ml-infrastructure` |
| GPU hardware health, thermal management | **Hardware & Datacenter Ops** | Slack: `#dc-ops-oncall` · PD: `datacenter-ops` |
| Request distribution / workload characteristics | **Application Team** (API consumer) | See service `CODEOWNERS` · Slack: `#team-<service>` |

**Boundary:** ML Infrastructure owns throughput and is the first call. Thermal throttling is a joint escalation: ML Infrastructure identifies it via `nvidia-smi`, Hardware & Datacenter Ops physically investigates and remediates cooling. If the throughput drop is caused by a client sending longer requests (workload shift), the application team consuming the inference API is the stakeholder to coordinate with on capacity implications.

## Related Runbooks
- [inference-gpu-oom.md](inference-gpu-oom.md)
- [inference-worker-crash.md](inference-worker-crash.md)
- [inference-request-queue-depth.md](inference-request-queue-depth.md)
- [compute-high-cpu.md](compute-high-cpu.md) — CPU saturation on the inference pod

## Tags
`family: inference`
`severity: high`
`services: vllm, tgi, inference-serving, gpu`
