# Network Timeout — Intermittent or Sustained Connection Timeouts

## Overview
Network timeouts occur when a connection is established (or attempted) but the response does not arrive within the configured deadline. Unlike connection refused (immediate rejection), timeouts mean the request is sitting in a queue somewhere — in the kernel, a load balancer, a sidecar proxy, or the downstream service. Timeouts are one of the most ambiguous incident signals because they can originate at any layer: DNS, TCP, TLS, HTTP, or application. Systematic narrowing of the layer is the core skill here.

## Alert Signatures
- `HighConnectionTimeoutRate` — custom metric: `rate(http_request_duration_seconds_bucket{le="+Inf"}[5m]) - rate(http_request_duration_seconds_bucket{le="<slo_threshold>"}[5m])`
- Client-side errors: `context deadline exceeded`, `i/o timeout`, `net/http: request canceled`
- Upstream 504 Gateway Timeout responses from ingress
- `etcd_request_duration_seconds` elevated (if timeouts are hitting the API server)
- Alertmanager: `[HIGH] Timeout rate elevated for <service-name>`

## Common Causes
- Downstream service overloaded (see [compute-high-cpu.md](compute-high-cpu.md) or [api-latency-spike.md](api-latency-spike.md))
- DNS resolution delay causing initial connection setup to exceed deadline (see [network-dns-failure.md](network-dns-failure.md))
- TCP connection pool exhaustion — connections queueing before being established
- Load balancer or ingress health check removing healthy backends incorrectly
- Network policy or security group change blocking traffic (connection hangs instead of being rejected)
- MTU mismatch on a network path (PMTUD blackhole — affects large requests, not small ones)
- Sidecar proxy (Envoy/Istio) circuit breaker open or misconfigured timeout lower than upstream SLA
- Cloud provider network event on the path (intermittent packet loss, route flap)

## Diagnostic Steps
1. Establish scope: is the timeout affecting all clients or specific ones? All destinations or one?
   ```bash
   # Compare error rates across services
   kubectl logs <pod-name> -n <namespace> --tail=200 | grep -E "timeout|deadline|i/o timeout" | head -30
   ```
2. Check if the downstream service is healthy and responding within its own SLA:
   ```bash
   kubectl top pods -n <downstream-namespace>
   kubectl logs <downstream-pod> -n <downstream-namespace> --tail=100
   ```
3. Test raw TCP connectivity from inside the pod:
   ```bash
   kubectl exec -it <pod-name> -n <namespace> -- nc -zv <service-name> <port>
   kubectl exec -it <pod-name> -n <namespace> -- curl -v --max-time 5 http://<service-name>:<port>/healthz
   ```
4. Check DNS resolution time:
   ```bash
   kubectl exec -it <pod-name> -n <namespace> -- time nslookup <service-name>
   ```
5. Check ingress/load balancer logs for 504s:
   ```bash
   kubectl logs -n ingress-nginx -l app.kubernetes.io/name=ingress-nginx --tail=100 | grep 504
   ```
6. Check service mesh (if present) for circuit breaker state:
   ```bash
   # Istio example
   kubectl exec -it <pod-name> -n <namespace> -c istio-proxy -- pilot-agent request GET stats | grep cx_open
   ```
7. Check for recent network policy changes:
   ```bash
   kubectl get networkpolicy -n <namespace>
   kubectl get events -n <namespace> --sort-by='.lastTimestamp' | tail -20
   ```

## Resolution Steps
1. **If downstream service is the bottleneck:** Address the downstream issue (CPU, DB connection pool, etc.) using the relevant runbook.
2. **If DNS is slow:** See [network-dns-failure.md](network-dns-failure.md). Short-term: add DNS caching in the application.
3. **If connection pool exhaustion:** Increase the pool size in the application config or reduce connection hold time. For HTTP/2, confirm multiplexing is enabled.
4. **If MTU mismatch (large requests timeout, small ones don't):**
   ```bash
   ping -M do -s 1450 <destination-ip>   # test if large packets are dropped
   # Fix: set interface MTU or enable PMTUD; for container networks, check CNI MTU setting
   ```
5. **If sidecar circuit breaker is open:** Check Envoy admin interface and reset the circuit breaker, then address the upstream issue that opened it.
6. **If network policy or security group:** Identify the rule blocking the traffic and update it. Test with `kubectl exec` + `nc` before and after.

## Escalation Criteria
Escalate to the on-call engineer if:
- Timeouts are affecting multiple services with no common downstream dependency
- Raw TCP tests also time out (rules out application-level cause — possible infrastructure issue)
- Cloud provider network event is suspected (check provider status page)
- Timeouts began immediately after a network infrastructure change (CNI update, security group change)

## Ownership

| Layer | Team | Contact |
|---|---|---|
| Application timeout config, connection pool, retry logic | **Application Team** | See service `CODEOWNERS` · Slack: `#team-<service>` |
| Kubernetes networking (CNI, kube-proxy, network policy) | **Platform Engineering** | Slack: `#platform-oncall` · PD: `platform-engineering` |
| VPC routing, security groups, firewall rules, cross-region paths | **Network / Infrastructure** | Slack: `#network-oncall` · PD: `network-infrastructure` |
| Cloud provider network event | **Platform Engineering** → cloud provider support | Platform Eng opens provider ticket |

**Boundary:** Timeouts are the most ambiguous alert — the ownership depends entirely on which layer the timeout occurs at. Application teams own their timeout configuration and client-side retry behavior. Platform Engineering owns cluster-level networking (CNI, network policy). Network / Infrastructure owns everything outside the cluster boundary. If raw TCP tests timeout (not just HTTP), the issue is below the application layer — escalate to Platform Engineering or Network / Infrastructure depending on whether it's intra-cluster or cross-cluster traffic.

## Related Runbooks
- [network-dns-failure.md](network-dns-failure.md)
- [network-ingress-502.md](network-ingress-502.md) — ingress-layer timeout manifests as 5xx
- [network-service-mesh-error.md](network-service-mesh-error.md)
- [api-latency-spike.md](api-latency-spike.md)

## Tags
`family: networking`
`severity: high`
`services: any`
