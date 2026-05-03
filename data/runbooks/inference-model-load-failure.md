# Model Load Failure — Model Fails to Load at Startup

## Overview
Model load failure occurs when the inference server starts but cannot successfully initialize the model into GPU memory. This is distinct from a worker crash during serving — the pod starts, attempts to load the model, and then either fails (process exits) or gets killed by the startup probe timeout. Because model loading can take 5-15 minutes for large models, this failure mode can be slow to surface and hard to distinguish from "still loading" without proper logging. While any single pod is stuck in this state, it contributes no serving capacity.

## Alert Signatures
- Pod stuck in `Init:Error` or `CrashLoopBackOff` with exit code `1` immediately after start
- Startup probe failing: pod shows `READY 0/1` for an extended period then restarts
- Container logs end with model loading traceback rather than serving-ready message
- `vllm:num_requests_running` never becomes non-zero after a restart
- Alertmanager: `[HIGH] Inference pod <pod-name> failed to reach ready state`

## Common Causes
- GPU VRAM insufficient for model at the configured precision (FP16, BF16, INT4, INT8)
- Model weights missing, incomplete, or corrupted in the model storage volume
- Model name or path misconfigured — framework cannot find the model files
- Incompatible model format or version for the current framework version (model requires newer vLLM)
- Tensor parallelism configuration mismatch — model requires N GPUs but only M are available
- Tokenizer files missing or incompatible
- Network storage (NFS, object store) too slow to complete model load within the startup probe timeout
- Python dependency version conflict preventing the framework from importing the model class

## Diagnostic Steps
1. Check logs from the startup phase of the container:
   ```bash
   kubectl logs <pod-name> -n <namespace> --tail=500
   # If already crashed, check previous instance:
   kubectl logs <pod-name> -n <namespace> --previous --tail=500
   ```
   Look for the last successfully loaded component and the first error.
2. Check the startup probe configuration:
   ```bash
   kubectl describe pod <pod-name> -n <namespace> | grep -A10 "Startup"
   ```
   Is `failureThreshold * periodSeconds` long enough for model loading? (e.g., a 70B model may need 15+ minutes)
3. Verify model files exist and are complete on the storage volume:
   ```bash
   kubectl exec -it <pod-name> -n <namespace> -- ls -lh /models/<model-name>/
   # Check for expected files: config.json, tokenizer.json, model.safetensors or pytorch_model.bin shards
   ```
4. Check GPU availability and VRAM:
   ```bash
   kubectl exec -it <pod-name> -n <namespace> -- nvidia-smi
   ```
   Verify VRAM is free and device is in a clean state.
5. Confirm tensor parallelism config matches available GPU count:
   ```bash
   kubectl describe pod <pod-name> -n <namespace> | grep -E "GPU|tensor.parallel|tp.size"
   # vLLM arg: --tensor-parallel-size should == number of GPUs allocated to the pod
   kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.containers[0].resources.limits}'
   ```
6. Check framework version compatibility:
   ```bash
   kubectl exec -it <pod-name> -n <namespace> -- pip show vllm transformers | grep Version
   ```

## Resolution Steps
1. **If model files are missing or corrupted:** Re-run the model download job or pull script:
   ```bash
   kubectl delete pod <model-download-job-pod> -n <namespace>  # or trigger the job
   # After download completes, restart the inference pod
   kubectl rollout restart deployment/<inference-deployment> -n <namespace>
   ```
2. **If GPU VRAM is insufficient:** Switch to a quantized model version (INT8, INT4) or use a smaller model variant. Update the deployment to point to the quantized model path.
3. **If startup probe timeout is too short:** Increase `failureThreshold` to allow more time:
   ```bash
   # Edit deployment spec: startupProbe.failureThreshold = 60, periodSeconds = 30 = 30 minutes max wait
   kubectl edit deployment <inference-deployment> -n <namespace>
   ```
4. **If tensor parallelism misconfigured:** Ensure GPU request in pod spec matches `--tensor-parallel-size`:
   ```yaml
   resources:
     limits:
       nvidia.com/gpu: "2"  # Must equal --tensor-parallel-size 2
   ```
5. **If framework version incompatibility:** Pin the container image to a compatible version and redeploy. Test in a staging environment before rolling to production.
6. **If object store is too slow:** Pre-warm the model onto a local PVC using an init container before the serving container starts.

## Escalation Criteria
Escalate to the on-call engineer if:
- Model load fails after files have been verified and GPU is healthy — possible framework bug
- All GPU nodes are failing to load the model (deployment-wide outage)
- Model storage volume is unreadable or has been corrupted
- The fix requires a model redownload that will take > 30 minutes (customer impact window)

## Ownership

| Layer                                                     | Team                                                          | Contact                                             |
|-----------------------------------------------------------|---------------------------------------------------------------|-----------------------------------------------------|
| Inference server startup config, startup probe, TP config | **ML Infrastructure**                                         | Slack: `#ml-infra-oncall` · PD: `ml-infrastructure` |
| Model weights storage, model download pipeline            | **Data Engineering** (model storage) or **ML Infrastructure** | Slack: `#data-oncall` or `#ml-infra-oncall`         |
| GPU availability and VRAM for the model                   | **ML Infrastructure**                                         | Slack: `#ml-infra-oncall` · PD: `ml-infrastructure` |

**Boundary:** ML Infrastructure owns the model load process and is first responder. If model weights are missing or corrupted on the storage volume, the owning team depends on how model storage is managed — if Data Engineering runs the model registry and download pipeline, they own the fix. If ML Infrastructure owns the model pipeline end-to-end, they own it. Clarify this boundary in your org before an incident.

## Related Runbooks
- [inference-worker-crash.md](inference-worker-crash.md) — runtime crash vs. startup failure
- [inference-gpu-oom.md](inference-gpu-oom.md) — VRAM exhaustion during model load
- [storage-pvc-mount-failure.md](storage-pvc-mount-failure.md) — model storage volume not mounting
- [compute-pending-pods.md](compute-pending-pods.md) — GPU pod may be Pending if no GPU nodes available

## Tags
`family: inference`
`severity: critical`
`services: vllm, tgi, inference-serving, model-loading`
