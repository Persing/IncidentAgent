# Pods Stuck in Pending — Scheduling Failure

## Overview
A pod in Pending state has been accepted by the API server but the scheduler cannot place it on any node. This can be immediate (hard constraint — no node ever fits) or delayed (soft constraint — waiting for resources to free). Pending pods mean your deployment is not at desired replica count. During incident response this is critical: if you're trying to scale up and pods are Pending, you're not actually scaling. The cause is almost always one of: insufficient cluster resources, node selector/affinity/taint constraints, PVC binding failure, or quota exhaustion.

## Alert Signatures
- `KubePodNotScheduled` — `kube_pod_status_phase{phase="Pending"} > 0` for > 5m
- Deployment desired != ready in dashboards
- Alertmanager: `[HIGH] Pod <pod-name> has been Pending for more than 5 minutes`

## Common Causes
- Insufficient CPU or memory available across all schedulable nodes
- Node selector or nodeAffinity constraints that no current node satisfies
- Tolerations missing for a required taint on GPU or specialized nodes
- PVC in Pending state — no available PersistentVolume to bind
- Resource quota exhausted (namespace-level, see [compute-resource-quota-exceeded.md](compute-resource-quota-exceeded.md))
- All matching nodes are cordoned or have the `NoSchedule` taint
- Pod anti-affinity rules preventing co-location on the only available nodes
- GPU resource request (`nvidia.com/gpu: 1`) but no GPU nodes available or all GPU slots occupied

## Diagnostic Steps
1. Check pod status and get the scheduler message:
   ```bash
   kubectl describe pod <pod-name> -n <namespace> | grep -A20 "Events:"
   ```
   The scheduler event message tells you exactly why it can't schedule.
2. Check overall cluster resource availability:
   ```bash
   kubectl describe nodes | grep -A5 "Allocated resources"
   kubectl get nodes -o custom-columns=NAME:.metadata.name,STATUS:.status.conditions[-1].type,CPU:.status.allocatable.cpu,MEM:.status.allocatable.memory
   ```
3. Check for cordoned or tainted nodes:
   ```bash
   kubectl get nodes -o wide
   kubectl describe nodes | grep -E "Taints:|Unschedulable"
   ```
4. If the pod requests a PVC, check PVC status:
   ```bash
   kubectl get pvc -n <namespace>
   kubectl describe pvc <pvc-name> -n <namespace>
   ```
5. Check pod spec for node selectors, affinity, and tolerations:
   ```bash
   kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.nodeSelector}'
   kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.affinity}'
   ```
6. Check resource quota usage:
   ```bash
   kubectl describe resourcequota -n <namespace>
   ```

## Resolution Steps
1. **If insufficient cluster resources:** Trigger cluster autoscaler (if configured) by waiting, or manually add nodes via cloud provider console. Verify autoscaler is not blocked:
   ```bash
   kubectl -n kube-system logs -l app=cluster-autoscaler --tail=50
   ```
2. **If node selector/affinity mismatch:** Verify the required label exists on at least one node:
   ```bash
   kubectl get nodes -l <key>=<value>
   ```
   If no nodes have the label, either add the label or update the pod spec.
3. **If GPU nodes are fully occupied:** Check current GPU allocation:
   ```bash
   kubectl describe nodes | grep -A5 "nvidia.com/gpu"
   ```
   Preempt lower-priority workloads or add GPU nodes.
4. **If PVC is Pending:** See [storage-pvc-mount-failure.md](storage-pvc-mount-failure.md).
5. **If quota is exhausted:** See [compute-resource-quota-exceeded.md](compute-resource-quota-exceeded.md).
6. **If nodes are cordoned:** Uncordon if appropriate:
   ```bash
   kubectl uncordon <node-name>
   ```

## Escalation Criteria
Escalate to the on-call engineer if:
- Pods have been Pending for 15+ minutes with no autoscaler activity
- The scheduler event message references a bug or unexpected condition
- Cluster autoscaler is failing to provision nodes (check autoscaler logs and cloud provider quotas)
- Multiple deployments are simultaneously blocked

## Ownership

| Layer                                                              | Team                                                              | Contact                                                |
|--------------------------------------------------------------------|-------------------------------------------------------------------|--------------------------------------------------------|
| Pod spec (node selector, affinity, tolerations, resource requests) | **Application Team**                                              | See service `CODEOWNERS` · Slack: `#team-<service>`    |
| Cluster capacity, autoscaler, node taints, node labels             | **Platform Engineering**                                          | Slack: `#platform-oncall` · PD: `platform-engineering` |
| GPU node provisioning and capacity                                 | **ML Infrastructure** (GPU pool) or **Hardware & Datacenter Ops** | Slack: `#ml-infra-oncall` or `#dc-ops-oncall`          |

**Boundary:** If the pod spec is the problem (wrong node selector, missing toleration), that is the application team's fix. If the cluster has no nodes that satisfy any reasonable pod spec (capacity exhausted, autoscaler broken), that is Platform Engineering's problem. GPU capacity planning is shared between ML Infrastructure (which manages the GPU pool configuration) and Hardware & Datacenter Ops (which provisions physical GPU nodes).

## Related Runbooks
- [compute-resource-quota-exceeded.md](compute-resource-quota-exceeded.md)
- [compute-node-not-ready.md](compute-node-not-ready.md) — NotReady nodes remove schedulable capacity
- [storage-pvc-mount-failure.md](storage-pvc-mount-failure.md)

## Tags
`family: compute`
`severity: high`
`services: any`
