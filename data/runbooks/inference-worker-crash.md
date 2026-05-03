# Inference Worker Crash — vLLM or Inference Worker Process Crash

## Overview
An inference worker crash means the primary serving process (vLLM engine, TGI worker, or a custom inference server) has exited unexpectedly. In-flight requests are immediately dropped. The container enters CrashLoopBackOff. During the restart window (which can be minutes due to model loading time), the service is unavailable. Worker crashes are a higher-urgency signal than most crashes because model loading is slow — a 70B model on a GPU can take 5-15 minutes to load, meaning recovery is not fast even after the container restarts. Prevention through root cause resolution is more important than fast restart.

## Alert Signatures
- `InferenceWorkerDown` — `up{job="vllm"} == 0`
- Pod status `CrashLoopBackOff` for inference pods
- Requests returning 503 or connection refused for the duration of the restart
- `vllm:num_requests_running` dropping to 0 unexpectedly
- Alertmanager: `[CRITICAL] Inference worker <pod-name> is down`

## Common Causes
- GPU OOM causing CUDA exception that kills the process (see [inference-gpu-oom.md](inference-gpu-oom.md))
- Uncaught exception in the inference framework — Python runtime exception killing the server
- NCCL communication error during tensor-parallel inference (multi-GPU setup)
- A malformed or adversarial request triggering a bug in the tokenizer or model forward pass
- Liveness probe misconfigured — killing a worker that is simply slow to respond under load
- SIGKILL from the OS due to CPU OOM (host-level memory pressure)
- Model weights corrupted on the shared volume — model loading fails on startup
- Timeout on model load causing the init container or startup probe to fail

## Diagnostic Steps
1. Check the pod status and restart count:
   ```bash
   kubectl get pods -n <namespace> -l <inference-app-label>
   kubectl describe pod <pod-name> -n <namespace> | grep -A5 "Last State\|Exit Code\|Reason"
   ```
2. Read logs from the crashed instance:
   ```bash
   kubectl logs <pod-name> -n <namespace> --previous --tail=500
   ```
   Look for: CUDA error, Python traceback, NCCL error, uncaught exception.
3. Check the exit code:
   - `137` = SIGKILL (OOM or liveness probe)
   - `1` = Python exception
   - `139` = segfault
   - `134` = SIGABRT (abort, often CUDA runtime)
4. Check GPU state on the node (CUDA errors can leave the GPU in a bad state):
   ```bash
   kubectl exec -it <another-pod-on-same-node> -- nvidia-smi
   # Look for "ERR!" in the process table
   ```
5. Check for NCCL errors (multi-GPU tensor parallel):
   ```bash
   kubectl logs <pod-name> -n <namespace> --previous | grep -i "NCCL\|nccl_error\|CUDA error"
   ```
6. Check liveness probe configuration:
   ```bash
   kubectl describe pod <pod-name> -n <namespace> | grep -A10 "Liveness"
   ```
   Is `timeoutSeconds` too low for a model under load?
7. Verify model weight integrity on the volume:
   ```bash
   kubectl exec -it <pod-name> -n <namespace> -- ls -lh /models/<model-name>/
   # Check for incomplete downloads (size mismatch vs expected)
   ```

## Resolution Steps
1. **If GPU OOM caused the crash:** See [inference-gpu-oom.md](inference-gpu-oom.md). Fix GPU memory configuration before allowing the pod to restart, otherwise it will crash again immediately.
2. **If CUDA error left GPU in a bad state:** Restart the node's GPU driver or cordon the node and reschedule to a healthy GPU node:
   ```bash
   kubectl cordon <node-name>
   kubectl delete pod <pod-name> -n <namespace>  # reschedule to another GPU node
   ```
3. **If NCCL error in multi-GPU setup:** Verify GPU interconnect (NVLink, PCIe) is healthy. Check if other tensor-parallel ranks are still alive. Often requires all ranks to restart together:
   ```bash
   kubectl rollout restart deployment/<inference-deployment> -n <namespace>
   ```
4. **If liveness probe is killing a healthy but slow pod:** Increase `timeoutSeconds` and `failureThreshold` on the liveness probe.
5. **If model weights are corrupted:** Delete the PVC contents and re-download the model:
   ```bash
   kubectl exec -it <init-pod> -- rm -rf /models/<model-name>/
   # Trigger model download job or restart with model pull enabled
   ```
6. **If recurring crash from a specific request pattern:** Add request validation to reject malformed inputs before they reach the model forward pass.

## Escalation Criteria
Escalate to the on-call engineer if:
- The worker crashes immediately after restart (boot loop before any requests are served)
- All inference pods are crashing simultaneously
- The GPU is in an unrecoverable error state and node restart is needed
- Model loading fails after a clean restart (corrupted weights or storage issue)

## Ownership

| Layer | Team | Contact |
|---|---|---|
| Inference serving process, vLLM config, liveness probes | **ML Infrastructure** | Slack: `#ml-infra-oncall` · PD: `ml-infrastructure` |
| GPU hardware health, driver state, NCCL interconnect | **Hardware & Datacenter Ops** | Slack: `#dc-ops-oncall` · PD: `datacenter-ops` |
| Kubernetes pod scheduling and restart policy | **Platform Engineering** | Slack: `#platform-oncall` · PD: `platform-engineering` |

**Boundary:** ML Infrastructure owns the inference serving layer end-to-end and is incident commander. Hardware & Datacenter Ops is called in when the crash trace points to a hardware-level failure (GPU error codes, NVLink failure, thermal event). If a node-level cordon or drain is needed to recover, ML Infrastructure coordinates with Platform Engineering to execute it.

## Related Runbooks
- [inference-gpu-oom.md](inference-gpu-oom.md) — primary cause of worker crashes
- [inference-model-load-failure.md](inference-model-load-failure.md) — if the crash occurs during startup
- [compute-crashloop.md](compute-crashloop.md) — general CrashLoopBackOff pattern
- [inference-request-queue-depth.md](inference-request-queue-depth.md) — queued requests pile up during worker downtime

## Tags
`family: inference`
`severity: critical`
`services: vllm, tgi, inference-serving, gpu`
