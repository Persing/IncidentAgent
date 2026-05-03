# OOMKilled — Container Killed by Out-of-Memory

## Overview
An OOMKilled container was terminated by the Linux kernel's OOM killer because it exceeded its memory limit or the node ran out of allocatable memory. The pod typically restarts automatically, but repeated OOMKills indicate a structural problem — either the limit is too low, there is a memory leak, or the workload is processing larger-than-expected payloads. Repeated kills lead to CrashLoopBackOff and service degradation.

## Alert Signatures
- `KubeContainerOOMKilled` — `kube_pod_container_status_last_terminated_reason{reason="OOMKilled"} == 1`
- `container_oom_events_total` counter incrementing
- Pod `LAST STATE` shows `OOMKilled` in `kubectl describe pod`
- Alertmanager: `[CRITICAL] OOMKilled detected on <pod-name> in <namespace>`

## Common Causes
- Memory limit set too low for the actual working set (under-provisioned resource spec)
- Memory leak in application code — heap grows unbounded over time
- Large payload or batch being processed in memory (e.g., loading a full dataset, large model weights, long context window)
- JVM or Python process memory fragmentation causing RSS to exceed heap size
- Sudden traffic spike with in-memory request buffering
- Node-level memory pressure causing the kernel to kill the least-privileged process

## Diagnostic Steps
1. Confirm OOMKill and how many times it has happened:
   ```bash
   kubectl describe pod <pod-name> -n <namespace> | grep -A5 "Last State"
   kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.status.containerStatuses[*].restartCount}'
   ```
2. Check current memory limits and actual usage before the kill:
   ```bash
   kubectl top pod <pod-name> -n <namespace> --containers
   kubectl describe pod <pod-name> -n <namespace> | grep -A5 "Limits\|Requests"
   ```
3. Review memory usage trend over time in Prometheus/Grafana:
   - `container_memory_working_set_bytes{pod="<pod-name>"}` — true working set (what counts against limit)
   - Look for a gradual climb (leak) vs. sudden spike (large payload or traffic burst)
4. Check application logs immediately before the kill:
   ```bash
   kubectl logs <pod-name> -n <namespace> --previous --tail=200
   ```
5. Check node-level memory pressure:
   ```bash
   kubectl describe node <node-name> | grep -E "MemoryPressure|Allocatable|Allocated"
   ```
6. If inference workload: check request payload sizes and context lengths being processed at kill time.

## Resolution Steps
1. **If limit is too low (working set consistently near limit):** Increase the memory limit:
   ```bash
   kubectl set resources deployment <deployment-name> -n <namespace> --limits=memory=<new-limit>
   ```
   Set limit 20-30% above observed working set peak, not at the median.
2. **If memory leak (gradual climb visible in metrics):**
   - Enable heap profiling if available (`py-spy`, `jmap`, `pprof`)
   - Roll out a pod restart schedule as a short-term mitigation while the fix is developed
   - File a bug and set a memory limit with a restart policy to bound the blast radius
3. **If large payload spike:** Add payload size validation at the API boundary or implement streaming processing to avoid loading full payload into memory.
4. **If node-level pressure:** Cordon the node and redistribute pods:
   ```bash
   kubectl cordon <node-name>
   kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data
   ```
5. Verify the pod stabilizes after the fix:
   ```bash
   kubectl get pod <pod-name> -n <namespace> -w
   ```

## Escalation Criteria
Escalate to the on-call engineer if:
- Pod is in CrashLoopBackOff and restart count is growing rapidly (service is effectively down)
- Multiple pods across multiple nodes are OOMKilling simultaneously
- Memory limit has been increased but OOMKills persist (possible kernel or runtime bug)
- The OOMKill is affecting a model serving pod and inference is unavailable

## Ownership

| Layer | Team | Contact |
|---|---|---|
| Application memory usage, limits, leak fixes | **Application Team** (service owner) | See service `CODEOWNERS` · Slack: `#team-<service>` |
| Node-level memory pressure, kubelet eviction | **Platform Engineering** | Slack: `#platform-oncall` · PD: `platform-engineering` |
| Physical DIMM or memory hardware failure | **Hardware & Datacenter Ops** | Slack: `#dc-ops-oncall` · PD: `datacenter-ops` |

**Boundary:** Application teams own memory limit configuration and their process's memory behavior (leaks, large payloads). Platform Engineering owns node-level memory pressure and kubelet eviction policy. If OOMKills are occurring on multiple pods across a node simultaneously without an application explanation, escalate to Platform Engineering — the node itself may have a memory hardware issue.

## Related Runbooks
- [compute-crashloop.md](compute-crashloop.md) — repeated OOMKills lead to CrashLoopBackOff
- [compute-high-cpu.md](compute-high-cpu.md) — often co-occurs under load
- [inference-gpu-oom.md](inference-gpu-oom.md) — GPU-side OOM for inference workloads
- [compute-pending-pods.md](compute-pending-pods.md) — if node is drained, replacement pods may be pending

## Tags
`family: compute`
`severity: critical`
`services: any`
