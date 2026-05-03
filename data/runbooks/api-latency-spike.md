# API Latency Spike — p95/p99 Latency Elevated

## Overview
An API latency spike means the tail latency of a service's responses has risen significantly above its baseline or SLO threshold. p95 and p99 spikes affect the worst-performing 5% and 1% of requests respectively — these are often real users experiencing slow responses. Latency spikes have many root causes across the full stack: slow database queries, resource saturation, garbage collection pauses, downstream service slowness, and network issues all produce the same alert signal. The investigation requires systematic layer-by-layer elimination.

## Alert Signatures
- `APIHighLatency` — `histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m])) > <slo_threshold>`
- `http_request_duration_seconds_p95` or `_p99` elevated in dashboards
- SLO burn rate alert firing (latency SLO is being consumed faster than allowed)
- Client-side: user-visible slowness, increased timeout rate
- Alertmanager: `[HIGH] API p99 latency > <threshold>ms for <service>`

## Common Causes
- Database slow query or connection pool exhaustion (see [data-db-connection-pool.md](data-db-connection-pool.md))
- A downstream service the API depends on is slow (cascading latency)
- CPU saturation causing request processing to queue (see [compute-high-cpu.md](compute-high-cpu.md))
- Garbage collection pause (JVM full GC, Python gc) causing periodic latency spikes
- Memory pressure causing increased GC frequency or paging
- A recently deployed code change introduced a performance regression (N+1 query, missing cache, synchronous I/O)
- Traffic spike exceeding current capacity without triggering fast enough scaling
- Inference backend is slow (for LLM APIs) — model queue depth growing (see [inference-request-queue-depth.md](inference-request-queue-depth.md))
- Lock contention in the application (global lock, database row lock)
- Cold start latency — pods scaled up during a spike are serving their first requests without warm caches

## Diagnostic Steps
1. Establish the latency trend — when did it spike and is it sustained or intermittent?
   ```bash
   # Prometheus
   histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{service="<svc>"}[5m]))
   ```
2. Check if the latency spike is correlated with a traffic increase:
   - Compare `rate(http_requests_total[5m])` with latency timeline
3. Break down latency by endpoint — is it all endpoints or specific ones?
   ```bash
   histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m])) by (handler)
   ```
4. Check if the slowness is in the application or in a dependency:
   - Review distributed traces (if instrumented) for the slow requests
   - Check `upstream_response_time` in ingress logs to separate ingress vs. app time
5. Check database query times:
   ```sql
   -- PostgreSQL
   SELECT query, mean_exec_time, calls, total_exec_time
   FROM pg_stat_statements
   ORDER BY mean_exec_time DESC
   LIMIT 20;
   ```
6. Check CPU and memory on the service pods:
   ```bash
   kubectl top pods -n <namespace>
   ```
7. Check recent deployments or config changes:
   ```bash
   kubectl rollout history deployment/<deployment-name> -n <namespace>
   kubectl get events -n <namespace> --sort-by='.lastTimestamp' | tail -20
   ```
8. Check GC metrics (if JVM or Python with GC instrumentation):
   - JVM: `jvm_gc_pause_seconds_p99`, `jvm_gc_overhead_percent`
   - Python: manual gc logs or tracemalloc traces

## Resolution Steps
1. **If DB is the bottleneck:** Terminate long-running queries (see [data-db-connection-pool.md](data-db-connection-pool.md)). Add a missing index if a specific slow query is identified. Escalate to the DB owner if the query plan regressed.
2. **If a downstream service is slow:** Apply a circuit breaker or timeout to prevent slow downstream from cascading. Check the downstream service using its own latency runbook.
3. **If CPU is saturated:** Scale out the service horizontally. Review [compute-high-cpu.md](compute-high-cpu.md).
4. **If GC pause is the cause:** JVM: tune GC settings or increase heap. Python: review object allocation patterns. Restart affected pods to reset GC state as a short-term measure.
5. **If a deployment caused the regression:** Roll back:
   ```bash
   kubectl rollout undo deployment/<deployment-name> -n <namespace>
   ```
6. **If traffic spike:** Scale up replicas while investigating if HPA is not keeping up:
   ```bash
   kubectl scale deployment <deployment-name> -n <namespace> --replicas=<N>
   ```
7. **If inference latency:** See [inference-request-queue-depth.md](inference-request-queue-depth.md).

## Escalation Criteria
Escalate to the on-call engineer if:
- Latency is breaching the SLO window at the current burn rate (calculate time to exhaustion)
- The root cause is not identifiable within 15 minutes and latency is not improving
- A database query regression requires a schema change or query rewrite that cannot be done safely during an incident
- All replicas are saturated and no additional capacity can be added quickly

## Ownership

| Layer                                           | Team                                 | Contact                                                |
|-------------------------------------------------|--------------------------------------|--------------------------------------------------------|
| Application code, caching, query efficiency     | **Application Team**                 | See service `CODEOWNERS` · Slack: `#team-<service>`    |
| Database performance, query plans, index tuning | **Database Reliability Engineering** | Slack: `#db-oncall` · PD: `database-reliability`       |
| Cluster resources, node capacity, HPA           | **Platform Engineering**             | Slack: `#platform-oncall` · PD: `platform-engineering` |
| Inference backend latency                       | **ML Infrastructure**                | Slack: `#ml-infra-oncall` · PD: `ml-infrastructure`    |

**Boundary:** Application teams own their code's latency and must perform the initial triage. Once the root cause is identified at a specific layer, hand off to the owning team: DRE for DB query issues, Platform Engineering for resource saturation, ML Infrastructure for inference backend. Do not engage all teams simultaneously at alert time — triage first, then escalate to the right team.

## Related Runbooks
- [data-db-connection-pool.md](data-db-connection-pool.md)
- [compute-high-cpu.md](compute-high-cpu.md)
- [inference-request-queue-depth.md](inference-request-queue-depth.md)
- [api-error-rate-spike.md](api-error-rate-spike.md) — latency spikes often precede or co-occur with error spikes
- [network-timeout.md](network-timeout.md)

## Tags
`family: api`
`severity: high`
`services: any-http-service`
