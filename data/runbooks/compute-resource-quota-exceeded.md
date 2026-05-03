# Resource Quota Exceeded — Namespace Quota Blocks Scheduling

## Overview
Kubernetes ResourceQuota objects cap the total CPU, memory, and object count (pods, services, PVCs) a namespace can consume. When a deployment attempts to scale or a new pod is created and the namespace is at quota, the pod is rejected by the API server — it never reaches Pending state. This is silent from the pod's perspective: the deployment controller will show a warning event, but no pod appears. This commonly surfaces during incident response when you try to scale up and nothing happens.

## Alert Signatures
- No direct Prometheus alert by default — detected by symptoms:
  - Deployment replicas not matching desired count
  - Events: `Error creating: pods "..." is forbidden: exceeded quota`
- `kube_resourcequota` metric showing `used >= hard`
- Alertmanager (custom): `[HIGH] Namespace <ns> at 95%+ of CPU/memory quota`

## Common Causes
- Namespace quota was set conservatively at provisioning time and never updated as the service scaled
- A traffic spike triggered HPA scale-out that hit the quota ceiling
- A previous incident left zombie pods or terminating pods that still count against quota
- Another team's workload was deployed into the same namespace and consumed available quota
- A resource-intensive one-off job (batch, migration) consumed quota and was not cleaned up

## Diagnostic Steps
1. Check current quota usage in the affected namespace:
   ```bash
   kubectl describe resourcequota -n <namespace>
   ```
   Compare `Used` vs. `Hard` for each resource type.
2. Find which pods are consuming the most quota:
   ```bash
   kubectl get pods -n <namespace> -o json | jq '.items[] | {name: .metadata.name, cpu: .spec.containers[].resources.requests.cpu, mem: .spec.containers[].resources.requests.memory}'
   ```
3. Check for stuck Terminating pods still counting against quota:
   ```bash
   kubectl get pods -n <namespace> | grep Terminating
   ```
4. Look at deployment events for the forbidden error:
   ```bash
   kubectl describe deployment <deployment-name> -n <namespace> | grep -A5 "Events:"
   kubectl get events -n <namespace> --sort-by='.lastTimestamp' | tail -20
   ```
5. Check if HPA is trying to scale and being blocked:
   ```bash
   kubectl describe hpa <hpa-name> -n <namespace>
   ```

## Resolution Steps
1. **If Terminating pods are stuck:** Force-delete them to free quota:
   ```bash
   kubectl delete pod <pod-name> -n <namespace> --grace-period=0 --force
   ```
2. **If quota is legitimately too low for current scale:** Request a quota increase from the cluster admin or apply if you have permissions:
   ```bash
   kubectl edit resourcequota <quota-name> -n <namespace>
   # Increase the hard limits to accommodate the needed scale
   ```
3. **If a batch job consumed quota:** Delete completed/failed jobs:
   ```bash
   kubectl delete jobs -n <namespace> --field-selector status.successful=1
   ```
4. **If a different team's workload is the cause:** Identify and coordinate with the owner. Consider moving the workload to its own namespace.
5. After freeing or raising quota, verify the deployment resumes scaling:
   ```bash
   kubectl rollout status deployment/<deployment-name> -n <namespace>
   ```

## Escalation Criteria
Escalate to the on-call engineer or platform team if:
- You do not have permission to modify the ResourceQuota and service is degraded
- Quota increase alone doesn't resolve the issue (check for LimitRange violations)
- Multiple namespaces are hitting quota simultaneously (cluster capacity may be the real constraint)

## Ownership

| Layer | Team | Contact |
|---|---|---|
| ResourceQuota configuration, namespace provisioning | **Platform Engineering** | Slack: `#platform-oncall` · PD: `platform-engineering` |
| Workload resource requests, zombie pod cleanup | **Application Team** (namespace owner) | See service `CODEOWNERS` · Slack: `#team-<service>` |

**Boundary:** Platform Engineering provisions and owns ResourceQuota objects. Application teams own the workloads consuming quota within their namespace. Quota increases require Platform Engineering approval — the application team makes the request with justification. If a different team's workload consumed the quota, Platform Engineering coordinates between teams. Do not unilaterally delete another team's pods to free quota.

## Related Runbooks
- [compute-pending-pods.md](compute-pending-pods.md) — pods blocked by quota may appear Pending or not appear at all
- [compute-high-cpu.md](compute-high-cpu.md) — quota pressure often surfaces during scale-out triggered by CPU alerts

## Tags
`family: compute`
`severity: high`
`services: any`
