# API Error Rate Spike — 5xx Error Rate Above Threshold

## Overview
A 5xx error rate spike means a significant fraction of API requests are failing with server-side errors. Unlike latency, which degrades gradually, error spikes often appear suddenly and can mean requests are failing completely rather than slowly. A 1% error rate on a high-traffic service can represent thousands of failures per minute for real users. 5xx errors are caused by the server — client errors (4xx) are not covered here. The priority in this runbook is to stop the bleeding (reduce error rate) quickly, then identify the root cause.

## Alert Signatures
- `APIHighErrorRate` — `rate(http_requests_total{status=~"5.."}[5m]) / rate(http_requests_total[5m]) > 0.01`
- `nginx_ingress_controller_requests{status=~"5.."}` elevated
- SLO error budget burn rate alert firing
- Client-side: users seeing errors, retry storms increasing load
- Alertmanager: `[HIGH] API 5xx error rate > 1% for <service>`

## Common Causes
- Application crash or panic (unhandled exception returning 500)
- Dependency unavailability — database down, downstream service returning errors
- Deployment of a broken version (code error on a specific code path)
- Resource exhaustion causing request rejection — thread pool, connection pool, or file descriptors
- OOM causing pods to crash mid-request
- A bad request from a specific client triggering an unhandled code path (but affecting all subsequent requests if it corrupts shared state)
- Rate limiting misconfigured and rejecting legitimate traffic as 429→500
- Infrastructure change — config map update, secret rotation, or network policy change breaking a dependency

## Diagnostic Steps
1. Establish scope: what percentage of requests are failing and is it all endpoints or specific ones?
   ```bash
   # Prometheus — break down by status code and handler
   rate(http_requests_total{status=~"5.."}[5m]) by (status, handler)
   ```
2. Check when the error rate started — correlate with recent deploys or config changes:
   ```bash
   kubectl get events -n <namespace> --sort-by='.lastTimestamp' | tail -30
   kubectl rollout history deployment/<deployment-name> -n <namespace>
   ```
3. Read application logs for error details:
   ```bash
   kubectl logs -n <namespace> -l <app-label> --tail=200 | grep -E "ERROR|FATAL|panic|exception|traceback"
   ```
4. Check if backend pods are healthy:
   ```bash
   kubectl get pods -n <namespace>
   kubectl describe pods -n <namespace> -l <app-label> | grep -E "Ready|Restart|OOMKilled"
   ```
5. Check if a dependency is down:
   ```bash
   # Test DB connectivity
   kubectl exec -it <pod-name> -n <namespace> -- nc -zv <db-host> <db-port>
   # Check downstream service health
   kubectl exec -it <pod-name> -n <namespace> -- curl -s http://<downstream-service>/healthz
   ```
6. Check ingress access logs for the exact error and upstream response:
   ```bash
   kubectl logs -n ingress-nginx -l app.kubernetes.io/name=ingress-nginx --tail=200 | grep " 5[0-9][0-9] "
   ```
7. Check for resource exhaustion (thread pool, FD):
   ```bash
   kubectl exec -it <pod-name> -n <namespace> -- cat /proc/sys/fs/file-nr
   kubectl exec -it <pod-name> -n <namespace> -- ls /proc/<pid>/fd | wc -l
   ```

## Resolution Steps
1. **If a bad deploy caused it:** Roll back immediately — this is the fastest path to recovery:
   ```bash
   kubectl rollout undo deployment/<deployment-name> -n <namespace>
   kubectl rollout status deployment/<deployment-name> -n <namespace>
   ```
2. **If a dependency is down:** Check the dependency's status and use the appropriate runbook for that service. Apply a circuit breaker or fallback in the application to stop cascading errors while the dependency recovers.
3. **If pods are crashing:** Restart pods and follow [compute-crashloop.md](compute-crashloop.md) to identify the cause.
4. **If DB connection pool exhausted:** See [data-db-connection-pool.md](data-db-connection-pool.md).
5. **If resource exhaustion:** Scale horizontally to spread load and restart affected pods.
6. **If a specific client is triggering the errors:** Identify the client IP/user-agent in access logs and apply rate limiting or a WAF rule to block the malformed request pattern temporarily:
   ```bash
   kubectl logs -n ingress-nginx -l app.kubernetes.io/name=ingress-nginx --tail=500 | grep " 500 " | awk '{print $1}' | sort | uniq -c | sort -rn | head
   ```

## Escalation Criteria
Escalate to the on-call engineer if:
- Error rate is 100% (complete service outage) and rollback is not available or is itself failing
- Root cause cannot be determined within 10 minutes and error rate is at SLO-breach level
- A data consistency issue is suspected — errors may indicate data corruption (do not just restart)
- A security event is suspected — unusual error patterns from multiple IPs may indicate an attack

## Ownership

| Layer                                                  | Team                                 | Contact                                                |
|--------------------------------------------------------|--------------------------------------|--------------------------------------------------------|
| Application code, dependency config, rollback decision | **Application Team**                 | See service `CODEOWNERS` · Slack: `#team-<service>`    |
| Downstream service (if dependency is failing)          | **Owning team of that dependency**   | Check `CODEOWNERS` for the dependency                  |
| Cluster health, ingress, infrastructure                | **Platform Engineering**             | Slack: `#platform-oncall` · PD: `platform-engineering` |
| Database-caused errors                                 | **Database Reliability Engineering** | Slack: `#db-oncall` · PD: `database-reliability`       |

**Boundary:** Application teams own the decision to roll back and must lead the initial triage. If the error root cause is in a dependency they don't own, they loop in that dependency's team — but they remain the incident commander for their own SLO. If the error is caused by a data consistency issue, do not attempt to fix the data without DRE involvement. Security-related error patterns (auth failures from unexpected IPs) should be flagged to Security Engineering immediately, even if they look like application errors.

## Related Runbooks
- [compute-crashloop.md](compute-crashloop.md)
- [data-db-connection-pool.md](data-db-connection-pool.md)
- [network-ingress-502.md](network-ingress-502.md)
- [api-latency-spike.md](api-latency-spike.md) — latency and errors often co-occur
- [deployment-stuck-rollout.md](deployment-stuck-rollout.md) — if rollback itself gets stuck

## Tags
`family: api`
`severity: critical`
`services: any-http-service`
