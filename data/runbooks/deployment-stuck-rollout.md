# Stuck Rollout — Deployment or Rollout Stalled

## Overview
A Kubernetes deployment rollout is "stuck" when it has been in progress for longer than its `progressDeadlineSeconds` (default: 600 seconds) without completing. This means the deployment controller is unable to bring new pods to the Ready state and replace old pods. The `AVAILABLE` count in `kubectl get deployment` will be less than `DESIRED`. Stuck rollouts are a common failure mode after a bad deploy — the new version fails to start, but the rollout strategy prevents old pods from being fully terminated. This can leave the service partially on the new (broken) version.

## Alert Signatures
- `KubeDeploymentRolloutStuck` — `kube_deployment_status_condition{condition="Progressing",status="false"} == 1`
- `kube_deployment_status_replicas_available < kube_deployment_spec_replicas` sustained > 10m
- `kubectl rollout status` returns: `Waiting for deployment rollout to finish`
- Alertmanager: `[HIGH] Deployment <deployment-name> rollout is stuck`

## Common Causes
- New pod version is crash-looping (see [compute-crashloop.md](compute-crashloop.md))
- New pod is failing readiness probe — starting successfully but not becoming Ready
- Insufficient cluster resources to schedule the new pods alongside the old ones during the rollout
- Resource quota exhaustion preventing new pods from being created
- Image pull failure — new image tag doesn't exist or registry is unreachable (see [deployment-image-pull-error.md](deployment-image-pull-error.md))
- PVC binding issue for the new pods
- A PodDisruptionBudget (PDB) preventing old pods from being terminated
- HPA is fighting the rollout — scaling down new pods that are briefly at 0 QPS

## Diagnostic Steps
1. Check deployment status:
   ```bash
   kubectl rollout status deployment/<deployment-name> -n <namespace>
   kubectl describe deployment <deployment-name> -n <namespace>
   ```
   Look at `Conditions` section for the root cause message.
2. Check the status of new and old ReplicaSets:
   ```bash
   kubectl get replicaset -n <namespace> | grep <deployment-name>
   ```
   New RS should be scaling up, old RS scaling down.
3. Check the new pods' status:
   ```bash
   kubectl get pods -n <namespace> -l <app-label>
   # Look for: CrashLoopBackOff, ImagePullBackOff, Pending, 0/1 READY
   ```
4. Check events for the stuck pods:
   ```bash
   kubectl get events -n <namespace> --sort-by='.lastTimestamp' | tail -30
   ```
5. Check if a PodDisruptionBudget is blocking termination of old pods:
   ```bash
   kubectl get pdb -n <namespace>
   kubectl describe pdb <pdb-name> -n <namespace>
   ```
6. Check resource availability for the new pods:
   ```bash
   kubectl describe pod <new-pod-name> -n <namespace> | grep -A20 "Events:"
   ```

## Resolution Steps
1. **If the new version is crashing:** The fastest path is rollback:
   ```bash
   kubectl rollout undo deployment/<deployment-name> -n <namespace>
   kubectl rollout status deployment/<deployment-name> -n <namespace>
   ```
2. **If readiness probe is failing:** Check the probe endpoint and the application startup logs. Either fix the application or adjust the probe if it's misconfigured:
   ```bash
   kubectl describe pod <new-pod-name> -n <namespace> | grep -A10 "Readiness"
   ```
3. **If ImagePullBackOff:** See [deployment-image-pull-error.md](deployment-image-pull-error.md).
4. **If PDB is blocking:** Check if it's safe to temporarily relax the PDB:
   ```bash
   kubectl patch pdb <pdb-name> -n <namespace> --type=merge \
     -p '{"spec":{"maxUnavailable":2}}'
   ```
   Restore after rollout completes.
5. **If insufficient resources:** Free resources (scale down non-critical deployments) or increase cluster capacity, then the rollout will resume automatically.
6. **Force rollout completion (last resort — may cause brief downtime):**
   ```bash
   kubectl rollout pause deployment/<deployment-name> -n <namespace>
   kubectl scale deployment <deployment-name> -n <namespace> --replicas=0
   kubectl scale deployment <deployment-name> -n <namespace> --replicas=<desired>
   kubectl rollout resume deployment/<deployment-name> -n <namespace>
   ```

## Escalation Criteria
Escalate to the on-call engineer if:
- Rollback does not complete within 5 minutes
- Service is effectively down (0 Ready pods) and rollback is also stuck
- The stuck rollout is in the critical path of a larger incident where other operations are being blocked
- PDB cannot be relaxed without risking data consistency (stateful services)

## Ownership

| Layer                                              | Team                     | Contact                                                |
|----------------------------------------------------|--------------------------|--------------------------------------------------------|
| Application code, image, deployment config         | **Application Team**     | See service `CODEOWNERS` · Slack: `#team-<service>`    |
| PodDisruptionBudgets, cluster capacity, scheduling | **Platform Engineering** | Slack: `#platform-oncall` · PD: `platform-engineering` |

**Boundary:** Application teams own their deployment and rollback decisions. Platform Engineering is involved when the rollout is blocked by a cluster-level constraint (PDB preventing termination, resource quota, scheduling failure). The application team initiates rollback; if rollback itself is blocked, loop in Platform Engineering. Do not modify another team's PDB during an incident without their explicit consent — it may affect their availability guarantees.

## Related Runbooks
- [compute-crashloop.md](compute-crashloop.md) — most common reason a rollout stalls
- [deployment-image-pull-error.md](deployment-image-pull-error.md)
- [compute-pending-pods.md](compute-pending-pods.md) — new pods may be Pending due to scheduling
- [compute-resource-quota-exceeded.md](compute-resource-quota-exceeded.md)

## Tags
`family: deployment`
`severity: high`
`services: any`
