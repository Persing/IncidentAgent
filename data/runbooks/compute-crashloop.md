# CrashLoopBackOff — Container Repeatedly Crashing

## Overview
CrashLoopBackOff means a container is starting, crashing, and being restarted by Kubernetes in an exponential backoff loop. The backoff starts at 10s and doubles to a max of 5 minutes between restarts. The container is functionally unavailable during backoff windows. CrashLoop is always a symptom — the root cause is something causing the process to exit non-zero. The most common triggers are misconfiguration, OOMKill, failed health checks, and application startup errors.

**User-facing presentation:** A pod in CrashLoopBackOff often appears to be running from the Kubernetes perspective (`kubectl get pods` shows `Running` or `CrashLoopBackOff`) but the service is completely unresponsive — health checks time out, the pod does not accept connections on its port, and users receive 503 errors from the load balancer or ingress. This is the most common reason a service appears to be running but is not accepting connections. A liveness probe that fails after startup triggers this exact pattern: the probe times out, Kubernetes kills the container, restarts it, and the cycle repeats while the pod status oscillates between Running and CrashLoopBackOff.

## Alert Signatures
- `KubePodCrashLooping` — `rate(kube_pod_container_status_restarts_total[15m]) > 0`
- Pod status shows `CrashLoopBackOff` in `kubectl get pods`
- `kube_pod_container_status_restarts_total` incrementing
- Alertmanager: `[CRITICAL] Pod <pod-name> is crash looping in <namespace>`

## Common Causes
- OOMKill (container exceeds memory limit on startup or shortly after)
- Application error on startup — bad config, missing env var, failed DB connection on init
- Liveness probe misconfigured — killing a healthy container before it finishes starting
- Missing or incorrect secret/configmap — process crashes when it can't read required config
- Image entrypoint or command error — wrong binary path, missing dependency
- Readiness gate or init container failure blocking the main container
- Persistent volume or file permission issue preventing startup

## Diagnostic Steps
1. Check pod state and restart count:
   ```bash
   kubectl get pod <pod-name> -n <namespace>
   kubectl describe pod <pod-name> -n <namespace>
   ```
   Look at `Last State`, `Exit Code`, and `Reason` fields.
2. Read logs from the previous (crashed) container instance:
   ```bash
   kubectl logs <pod-name> -n <namespace> --previous
   ```
   This is the most important step — the crash reason is almost always in these logs.
3. Check exit code for clues:
   - `Exit Code 0` — process exited cleanly but unexpectedly (check liveness probe config)
   - `Exit Code 1` — application error (check logs)
   - `Exit Code 137` — OOMKilled (SIGKILL from kernel)
   - `Exit Code 139` — segfault
4. Inspect liveness and readiness probe configuration:
   ```bash
   kubectl describe pod <pod-name> -n <namespace> | grep -A10 "Liveness\|Readiness"
   ```
5. Check if required secrets and configmaps are present and correctly mounted:
   ```bash
   kubectl get secret,configmap -n <namespace>
   kubectl describe pod <pod-name> -n <namespace> | grep -A5 "Environment\|Mounts"
   ```
6. If init containers are present, check their status:
   ```bash
   kubectl describe pod <pod-name> -n <namespace> | grep -A5 "Init Containers"
   kubectl logs <pod-name> -n <namespace> -c <init-container-name>
   ```

## Resolution Steps
1. **If OOMKilled (Exit Code 137):** Follow [compute-oom-killed.md](compute-oom-killed.md). Increase memory limit.
2. **If application startup error:** Fix the root cause in config/code. To unblock quickly, roll back to the last known-good image:
   ```bash
   kubectl rollout undo deployment/<deployment-name> -n <namespace>
   ```
3. **If liveness probe is killing the container too early:** Add or increase `initialDelaySeconds`:
   ```bash
   # Edit deployment spec to increase initialDelaySeconds on livenessProbe
   kubectl edit deployment <deployment-name> -n <namespace>
   ```
4. **If missing secret/configmap:** Create the missing resource, then restart the pod:
   ```bash
   kubectl create secret generic <secret-name> -n <namespace> --from-literal=key=value
   kubectl rollout restart deployment/<deployment-name> -n <namespace>
   ```
5. **If wrong image or entrypoint:** Patch the deployment to the correct image:
   ```bash
   kubectl set image deployment/<deployment-name> <container>=<correct-image> -n <namespace>
   ```

## Escalation Criteria
Escalate to the on-call engineer if:
- Previous logs show no output (container is crashing before logging starts — possible missing binary or seg fault)
- Rollback does not stop the crash loop
- The crash loop is affecting multiple deployments simultaneously (possible cluster-wide config issue)
- Service is in the critical request path and the backoff window is causing significant unavailability

## Ownership

| Layer                                            | Team                                 | Contact                                                |
|--------------------------------------------------|--------------------------------------|--------------------------------------------------------|
| Application code, config, secrets                | **Application Team** (service owner) | See service `CODEOWNERS` · Slack: `#team-<service>`    |
| Cluster config, probe defaults, secret injection | **Platform Engineering**             | Slack: `#platform-oncall` · PD: `platform-engineering` |

**Boundary:** Application teams own whatever is causing the process to exit — their code, their config, their secrets. Platform Engineering owns cluster-level concerns: if the crash is caused by a missing secret that Platform Engineering provisions, or a liveness probe default that was changed cluster-wide, that crosses into their scope. If rollback is blocked by a cluster-level issue, loop in Platform Engineering.

## Related Runbooks
- [compute-oom-killed.md](compute-oom-killed.md) — most common cause of Exit Code 137
- [compute-pending-pods.md](compute-pending-pods.md) — if rollback triggers a new pending state
- [deployment-stuck-rollout.md](deployment-stuck-rollout.md) — rollback may itself get stuck
- [storage-pvc-mount-failure.md](storage-pvc-mount-failure.md) — PVC failures can cause crash at startup

## Tags
`family: compute`
`severity: critical`
`services: any`
