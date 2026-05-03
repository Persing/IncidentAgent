# High CPU Usage — Pod or Node

## Overview
This alert fires when CPU utilization on a pod or node exceeds the defined threshold (typically 85–95% sustained over 5+ minutes). High CPU can cause request latency to spike, trigger OOM conditions, and destabilize neighboring workloads on the same node. Left unaddressed, it may cause cascading failures as the scheduler marks the node as pressured or the pod's liveness probe fails.

## Alert Signatures
- `KubePodCPUSaturation` — pod CPU usage > 90% of limit for 5m
- `NodeCPUSaturation` — `1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])) > 0.90`
- `container_cpu_usage_seconds_total` trending toward limit in dashboards
- PagerDuty / Alertmanager: `[HIGH] CPU throttling detected on <pod-name>`

## Common Causes
- Runaway process or tight loop inside the container (bug, infinite retry, unbounded queue consumer)
- Traffic spike without corresponding horizontal scaling (HPA not reacting fast enough or misconfigured)
- CPU limit set too low relative to actual workload needs (resource under-provisioning)
- Noisy neighbor: another pod on the same node consuming burst CPU
- Garbage collection pressure in JVM or Python runtimes causing high sustained CPU
- Inference workload (tokenization, pre/post-processing) saturating CPU while GPU is idle

## Diagnostic Steps
1. Identify the affected pod and node:
   ```bash
   kubectl top pods -n <namespace> --sort-by=cpu
   kubectl top nodes
   ```
2. Check CPU limits and current throttling:
   ```bash
   kubectl describe pod <pod-name> -n <namespace> | grep -A5 "Limits\|Requests"
   # Throttling metric:
   # container_cpu_cfs_throttled_periods_total / container_cpu_cfs_periods_total > 0.25 is significant
   ```
3. Check if HPA is configured and whether it has headroom to scale:
   ```bash
   kubectl get hpa -n <namespace>
   kubectl describe hpa <hpa-name> -n <namespace>
   ```
4. Look for CPU-intensive processes inside the container:
   ```bash
   kubectl exec -it <pod-name> -n <namespace> -- top -bn1 | head -20
   ```
5. Review recent deployments or config changes that may have altered behavior:
   ```bash
   kubectl rollout history deployment/<deployment-name> -n <namespace>
   ```
6. Check node-level CPU pressure and neighboring pod usage:
   ```bash
   kubectl describe node <node-name> | grep -A10 "Allocated resources"
   ```

## Resolution Steps
1. **If traffic spike:** Manually scale the deployment while HPA catches up:
   ```bash
   kubectl scale deployment <deployment-name> -n <namespace> --replicas=<desired>
   ```
2. **If CPU limit is too low:** Increase the limit in the deployment spec. Avoid setting limit == request for bursty workloads:
   ```bash
   kubectl set resources deployment <deployment-name> -n <namespace> --limits=cpu=<new-limit>
   ```
3. **If runaway process:** Identify the PID inside the container and check logs for error loops:
   ```bash
   kubectl logs <pod-name> -n <namespace> --tail=200
   kubectl exec -it <pod-name> -n <namespace> -- kill -9 <pid>  # last resort
   ```
   If the pod keeps respawning the issue, roll back the deployment.
4. **If GC pressure (JVM/Python):** Check heap metrics and consider adjusting GC tuning flags or memory limits to give the runtime more headroom.
5. **If noisy neighbor:** Cordon the node to prevent new scheduling and drain non-critical pods:
   ```bash
   kubectl cordon <node-name>
   kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data
   ```

## Escalation Criteria
Escalate to the on-call engineer if:
- CPU is pegged at 100% and the pod is unresponsive to exec or log queries
- Scaling up has no effect on CPU and the cause is unknown
- Multiple nodes are affected simultaneously (possible cluster-wide event)
- The affected service is in the request path for a customer-facing SLO and p99 latency is rising

## Ownership

| Layer                                   | Team                                 | Contact                                                |
|-----------------------------------------|--------------------------------------|--------------------------------------------------------|
| Application code, resource limits       | **Application Team** (service owner) | See service `CODEOWNERS` · Slack: `#team-<service>`    |
| Kubernetes scheduling, HPA, node health | **Platform Engineering**             | Slack: `#platform-oncall` · PD: `platform-engineering` |
| Physical node hardware                  | **Hardware & Datacenter Ops**        | Slack: `#dc-ops-oncall` · PD: `datacenter-ops`         |

**Boundary:** Application teams own their pod's CPU limit and application behavior. Platform Engineering owns the node and the HPA controller. If CPU is pegged at the node level across multiple pods, escalate to Platform Engineering. If the cause is a hardware anomaly (e.g., a physical core failing or thermal throttle), escalate to Hardware & Datacenter Ops.

## Related Runbooks
- [compute-oom-killed.md](compute-oom-killed.md) — CPU pressure often precedes OOM
- [compute-pending-pods.md](compute-pending-pods.md) — scaling may surface scheduling failures
- [api-latency-spike.md](api-latency-spike.md) — CPU saturation is a common latency root cause
- [inference-throughput-drop.md](inference-throughput-drop.md) — CPU saturation during inference pre/post-processing

## Tags
`family: compute`
`severity: high`
`services: any`
