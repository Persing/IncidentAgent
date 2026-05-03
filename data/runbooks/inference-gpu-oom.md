# GPU Out of Memory — GPU OOM During Model Serving

## Overview
A GPU OOM error occurs when the CUDA runtime or the inference framework (vLLM, TGI, TensorRT-LLM) attempts to allocate more GPU memory than is available on the device. This causes the worker process to crash or the request to be rejected with an OOM error. Unlike CPU OOM, which kills the container, GPU OOM usually manifests as a Python/CUDA exception that crashes the serving process, which then causes the container to enter CrashLoopBackOff. GPU memory is a hard ceiling — there is no swap. The memory consumption of an LLM is primarily determined by model weights (fixed) plus the KV cache (variable, grows with concurrent requests and context length).

## Alert Signatures
- `InferenceGPUMemoryUtilizationHigh` — `nvidia_smi_memory_used_bytes / nvidia_smi_memory_total_bytes > 0.95`
- Container logs: `torch.cuda.OutOfMemoryError: CUDA out of memory`
- Container logs: `CUDA error: out of memory` or `RuntimeError: CUDA out of memory`
- vLLM logs: `GPU KV cache OOM`, `Failed to allocate KV cache`
- Pod in CrashLoopBackOff after an OOM event
- Alertmanager: `[CRITICAL] GPU memory utilization > 95% on <node>`

## Common Causes
- Model weights too large for the available GPU VRAM (wrong GPU type for the model)
- KV cache growing beyond available memory due to long context length requests or high concurrency
- Multiple model replicas on the same GPU (model parallelism misconfigured, or multiple workers sharing a GPU unintentionally)
- vLLM `gpu_memory_utilization` set too high (default 0.9), leaving insufficient headroom for peak KV cache
- A request with an abnormally long prompt or expected completion that exceeds the reserved KV cache space
- Memory leak in the inference framework — GPU memory not freed between requests over time
- Tensor parallelism misconfigured — model shards not distributed across GPUs as expected

## Diagnostic Steps
1. Check GPU memory usage per device:
   ```bash
   kubectl exec -it <pod-name> -n <namespace> -- nvidia-smi
   # or via daemonset metrics
   kubectl exec -it <node-exporter-pod> -n monitoring -- nvidia-smi --query-gpu=index,name,memory.used,memory.free,memory.total --format=csv
   ```
2. Check container logs for the OOM error and the triggering request context:
   ```bash
   kubectl logs <pod-name> -n <namespace> --previous --tail=300 | grep -E "OOM|out of memory|CUDA error"
   ```
3. Check vLLM startup configuration:
   ```bash
   kubectl describe pod <pod-name> -n <namespace> | grep -A20 "Args\|Command\|Env"
   # Look for: --gpu-memory-utilization, --max-model-len, --tensor-parallel-size
   ```
4. Check model size vs. GPU VRAM:
   - FP16 weight size ≈ `num_params * 2 bytes` (e.g., 70B model ≈ 140GB, needs 2x H100s minimum)
   - Check which GPU type is on the node: `nvidia-smi -L`
5. Check for GPU memory leak (memory usage growing across requests without a spike):
   ```bash
   # Watch GPU memory over time
   kubectl exec -it <pod-name> -n <namespace> -- watch -n 5 nvidia-smi
   ```
6. Check current concurrency and request queue depth:
   - `vllm:num_requests_running`, `vllm:num_requests_waiting` in Prometheus

## Resolution Steps
1. **If KV cache OOM from long context requests:** Reduce `--max-model-len` in the vLLM startup args to cap the maximum context window. Redeploy:
   ```bash
   kubectl set env deployment/<deployment-name> -n <namespace> \
     MAX_MODEL_LEN=<reduced-value>
   kubectl rollout restart deployment/<deployment-name> -n <namespace>
   ```
2. **If gpu_memory_utilization too high:** Lower it to give more headroom for KV cache spikes:
   ```bash
   # In vLLM startup: --gpu-memory-utilization 0.80 (was 0.90)
   ```
3. **If high concurrency is exhausting KV cache:** Reduce `--max-num-seqs` (maximum concurrent sequences) or enable paged attention if not already active.
4. **If model weights don't fit on available GPUs:** Enable tensor parallelism across multiple GPUs or migrate to a node with more VRAM:
   ```bash
   # vLLM startup: --tensor-parallel-size <num-gpus>
   ```
5. **Immediate recovery:** Restart the crashed pod (it will restart automatically via CrashLoopBackOff backoff, but you can speed this up):
   ```bash
   kubectl delete pod <pod-name> -n <namespace>
   ```
6. **If GPU memory leak suspected:** Roll out a pod restart schedule as a mitigation while the framework issue is investigated.

## Escalation Criteria
Escalate to the on-call engineer if:
- OOM persists after reducing `gpu_memory_utilization` and `max_model_len`
- All GPU pods on all nodes are OOMing simultaneously (possible framework bug or configuration regression)
- The model cannot be served on any available GPU type (capacity planning issue)
- Tensor parallelism is required but more GPU nodes are not available

## Ownership

| Layer | Team | Contact |
|---|---|---|
| vLLM/TGI configuration, gpu_memory_utilization, KV cache tuning | **ML Infrastructure** | Slack: `#ml-infra-oncall` · PD: `ml-infrastructure` |
| GPU node pool, GPU scheduling, CUDA driver version | **ML Infrastructure** | Slack: `#ml-infra-oncall` · PD: `ml-infrastructure` |
| Physical GPU hardware, NVLink, cooling | **Hardware & Datacenter Ops** | Slack: `#dc-ops-oncall` · PD: `datacenter-ops` |
| Request payload size / context length (if client-driven) | **Application Team** (API consumer) | See service `CODEOWNERS` · Slack: `#team-<service>` |

**Boundary:** ML Infrastructure owns the inference serving configuration and is first responder for GPU OOM. If the OOM is caused by a client sending abnormally large requests, coordinate with the application team consuming the inference API to add payload validation. Physical GPU hardware failures (GPU not enumerated, NVLink errors, thermal shutdown) escalate to Hardware & Datacenter Ops — do not attempt physical intervention.

## Related Runbooks
- [inference-worker-crash.md](inference-worker-crash.md) — GPU OOM usually leads to worker crash
- [inference-request-queue-depth.md](inference-request-queue-depth.md) — queue buildup increases KV cache pressure
- [compute-oom-killed.md](compute-oom-killed.md) — CPU-side OOM behavior for comparison
- [inference-throughput-drop.md](inference-throughput-drop.md)

## Tags
`family: inference`
`severity: critical`
`services: vllm, tgi, tensorrt-llm, gpu-serving`
