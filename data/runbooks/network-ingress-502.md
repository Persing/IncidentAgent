# Ingress 5xx — Ingress Returning 502/503/504 to External Traffic

## Overview
The ingress controller is returning 5xx errors to external clients. This means external traffic is reaching the cluster but failing before or during delivery to backend pods. 502 Bad Gateway means the ingress connected to a backend but received an invalid response (or the connection was reset). 503 Service Unavailable means no healthy backends are available. 504 Gateway Timeout means the backend took too long to respond. Each code points to a different failure mode and requires a different investigation path.

## Alert Signatures
- `IngressHighErrorRate` — `rate(nginx_ingress_controller_requests{status=~"5.."}[5m]) / rate(nginx_ingress_controller_requests[5m]) > 0.01`
- `nginx_ingress_controller_requests{status="502"}` or `{status="503"}` elevated
- External uptime checks failing with HTTP 5xx
- Alertmanager: `[HIGH] Ingress error rate > 1% for <ingress-name>`

## Common Causes
**502 Bad Gateway:**
- Backend pod is crashing or restarting (see [compute-crashloop.md](compute-crashloop.md))
- Backend pod is not ready but still receiving traffic (readiness probe delay)
- Backend process exiting connections before responding (app crash, OOM)
- TLS misconfiguration between ingress and backend (backend expects mTLS, ingress sending plain)

**503 Service Unavailable:**
- All backend pods are failing health checks or are not Ready
- Kubernetes service has no endpoints (all pods down or selector mismatch)
- Rate limiting or upstream block configured on the ingress

**504 Gateway Timeout:**
- Backend is too slow — see [api-latency-spike.md](api-latency-spike.md)
- Ingress `proxy-read-timeout` shorter than backend processing time
- Backend connection pool exhaustion causing request queuing

## Diagnostic Steps
1. Confirm the error code and scope:
   ```bash
   kubectl logs -n ingress-nginx -l app.kubernetes.io/name=ingress-nginx --tail=200 | grep -E " 50[234] "
   ```
   Note the backend service, upstream address, and request path.
2. Check if the backend service has healthy endpoints:
   ```bash
   kubectl get endpoints <service-name> -n <namespace>
   kubectl describe service <service-name> -n <namespace>
   ```
3. Check backend pod readiness:
   ```bash
   kubectl get pods -n <namespace> -l <app-label>
   kubectl describe pods -n <namespace> -l <app-label> | grep -A5 "Ready\|Conditions"
   ```
4. Test connectivity from the ingress pod to the backend directly:
   ```bash
   kubectl exec -it <ingress-pod> -n ingress-nginx -- curl -v http://<backend-pod-ip>:<port>/healthz
   ```
5. Check ingress resource configuration for the affected route:
   ```bash
   kubectl describe ingress <ingress-name> -n <namespace>
   ```
6. Check ingress controller logs for upstream connection errors:
   ```bash
   kubectl logs -n ingress-nginx -l app.kubernetes.io/name=ingress-nginx --tail=500 | grep -i "upstream\|connect\|reset"
   ```

## Resolution Steps
1. **If 503 — no healthy endpoints:**
   - Check if pods are running and passing readiness probes:
     ```bash
     kubectl get pods -n <namespace>
     kubectl describe pod <pod-name> -n <namespace> | grep -A10 "Readiness"
     ```
   - If all pods are down, trigger a rollout or scale up.
   - If selector mismatch: fix the service selector to match pod labels.
2. **If 502 — backend resetting connections:** Check backend logs for crashes, OOMs, or panic output:
   ```bash
   kubectl logs <pod-name> -n <namespace> --previous --tail=100
   ```
   Roll back the deployment if a recent change caused it.
3. **If 504 — backend timeout:** Increase ingress timeout annotation for the affected service:
   ```yaml
   nginx.ingress.kubernetes.io/proxy-read-timeout: "120"
   nginx.ingress.kubernetes.io/proxy-send-timeout: "120"
   ```
   Then address the underlying latency issue (see [api-latency-spike.md](api-latency-spike.md)).
4. **If TLS misconfiguration:** Check backend annotation `nginx.ingress.kubernetes.io/backend-protocol` and ensure it matches what the backend expects.

## Escalation Criteria
Escalate to the on-call engineer if:
- Error rate is 100% (complete service outage to external traffic)
- All backend pods are healthy but ingress is still returning 5xx (ingress controller bug or config corruption)
- The ingress controller itself is crash-looping or unavailable

## Ownership

| Layer                                                 | Team                         | Contact                                                 |
|-------------------------------------------------------|------------------------------|---------------------------------------------------------|
| Ingress controller (nginx, Traefik) config and health | **Platform Engineering**     | Slack: `#platform-oncall` · PD: `platform-engineering`  |
| Backend application pods and readiness                | **Application Team**         | See service `CODEOWNERS` · Slack: `#team-<service>`     |
| External load balancer, cloud-managed LB              | **Network / Infrastructure** | Slack: `#network-oncall` · PD: `network-infrastructure` |

**Boundary:** Platform Engineering owns the ingress controller itself. Application teams own their backend pods. The first step is always to determine whether the 5xx originates at the ingress (ingress controller issue) or is the ingress forwarding a 5xx from the backend (application issue). Check ingress logs to see whether the upstream responded or whether the ingress itself generated the error. External load balancers sitting in front of the ingress are Network / Infrastructure territory.

## Related Runbooks
- [compute-crashloop.md](compute-crashloop.md) — common source of 502
- [api-latency-spike.md](api-latency-spike.md) — common source of 504
- [network-certificate-expiry.md](network-certificate-expiry.md) — TLS expiry can cause 5xx at ingress
- [network-service-mesh-error.md](network-service-mesh-error.md)

## Tags
`family: networking`
`severity: high`
`services: ingress, ingress-nginx, external-traffic`
