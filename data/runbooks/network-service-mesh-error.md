# Service Mesh Error — Sidecar Proxy Failures or Circuit Breaker Open

## Overview
Service mesh errors occur when the sidecar proxy (Envoy in Istio, Linkerd proxy) that intercepts all pod traffic encounters failures, misconfiguration, or applies a circuit breaker. Because the sidecar is transparent to the application, these failures can look like network errors or application timeouts from the application's perspective. Service mesh issues commonly surface after a mesh upgrade, a policy change, or when a downstream service degrades enough to trip a circuit breaker. A circuit breaker that opens and stays open will cause an otherwise-healthy service to appear down.

## Alert Signatures
- `IstioPilotXdsPushErrors` — xDS config push failures
- `envoy_cluster_upstream_cx_connect_fail` elevated
- `envoy_http_downstream_rq_5xx` elevated on sidecar stats
- Kiali / Jaeger showing mesh-level errors or retries
- Application logs: `upstream connect error or disconnect/reset before headers. reset reason: connection failure`
- Alertmanager: `[HIGH] Service mesh error rate elevated for <service>`

## Common Causes
- Circuit breaker tripped on a downstream service that has been slow or returning errors
- Mutual TLS (mTLS) misconfiguration — one service upgraded to STRICT mTLS while another still uses PERMISSIVE
- Envoy sidecar running an incompatible version after a partial mesh upgrade
- PeerAuthentication or AuthorizationPolicy blocking traffic that was previously allowed
- istiod (Pilot) unavailable or unable to push xDS configuration to sidecars
- Sidecar resource limits too low — Envoy OOMKilled while handling high traffic
- `DestinationRule` with outlier detection ejecting all backends

## Diagnostic Steps
1. Check Envoy sidecar stats for the affected pod:
   ```bash
   kubectl exec -it <pod-name> -n <namespace> -c istio-proxy -- pilot-agent request GET stats | grep -E "upstream_rq_5xx|cx_connect_fail|circuit_breaker"
   ```
2. Check if circuit breaker is open:
   ```bash
   kubectl exec -it <pod-name> -n <namespace> -c istio-proxy -- pilot-agent request GET stats | grep "circuit_breakers.default.cx_open"
   ```
   If `cx_open = 1`, the circuit breaker is open for that cluster.
3. Check istiod health and xDS push status:
   ```bash
   kubectl get pods -n istio-system
   kubectl logs -n istio-system -l app=istiod --tail=100 | grep -i "error\|fail"
   ```
4. Check PeerAuthentication and AuthorizationPolicy for the namespace:
   ```bash
   kubectl get peerauthentication,authorizationpolicy -n <namespace>
   kubectl describe peerauthentication <name> -n <namespace>
   ```
5. Check mTLS mode for the affected service:
   ```bash
   kubectl get destinationrule -n <namespace>
   istioctl authn tls-check <pod-name>.<namespace> <service-name>.<namespace>.svc.cluster.local
   ```
6. Check DestinationRule outlier detection config:
   ```bash
   kubectl get destinationrule <name> -n <namespace> -o yaml | grep -A10 "outlierDetection"
   ```

## Resolution Steps
1. **If circuit breaker is open due to downstream errors:**
   - Fix the downstream service first (see the relevant downstream runbook)
   - Envoy circuit breakers reset automatically once the upstream is healthy
   - If the breaker is stuck open: temporarily modify the DestinationRule to disable outlier detection, then re-enable after the downstream is stable
2. **If mTLS misconfiguration:** Set the namespace to PERMISSIVE mode temporarily, then migrate services to STRICT one at a time:
   ```bash
   kubectl apply -f - <<EOF
   apiVersion: security.istio.io/v1beta1
   kind: PeerAuthentication
   metadata:
     name: default
     namespace: <namespace>
   spec:
     mtls:
       mode: PERMISSIVE
   EOF
   ```
3. **If AuthorizationPolicy is blocking traffic:** Check recent policy changes, and temporarily set action to ALLOW for the affected path while investigating.
4. **If istiod is down:** Restart istiod. Sidecars will continue using their last-known xDS config but cannot receive updates:
   ```bash
   kubectl rollout restart deployment/istiod -n istio-system
   ```
5. **If Envoy sidecar OOMKilled:** Increase sidecar resource limits via global `meshConfig` or per-pod annotation.

## Escalation Criteria
Escalate to the on-call engineer or platform/mesh team if:
- istiod is unhealthy and sidecars cannot receive xDS updates (mesh is frozen)
- A mTLS or AuthorizationPolicy change is causing cluster-wide traffic failures
- Circuit breaker behavior cannot be traced to a clear downstream failure

## Ownership

| Layer                                                 | Team                                 | Contact                                                |
|-------------------------------------------------------|--------------------------------------|--------------------------------------------------------|
| Istio control plane (istiod), mesh-wide policy        | **Platform Engineering**             | Slack: `#platform-oncall` · PD: `platform-engineering` |
| Service-specific AuthorizationPolicy, DestinationRule | **Application Team** (policy author) | See service `CODEOWNERS` · Slack: `#team-<service>`    |
| mTLS PKI / internal CA backing the mesh               | **Security Engineering**             | Slack: `#security-oncall` · PD: `security-engineering` |

**Boundary:** Platform Engineering owns istiod and mesh-wide configuration (PeerAuthentication defaults, global traffic policy). Application teams own policies they wrote for their own services (AuthorizationPolicy, DestinationRule). If a policy change by one application team is breaking traffic to another team's service, the policy author's team is responsible for the fix, but Platform Engineering may need to mediate and temporarily override the policy to restore service.

## Related Runbooks
- [network-timeout.md](network-timeout.md) — mesh errors often present as timeouts
- [network-dns-failure.md](network-dns-failure.md)
- [api-error-rate-spike.md](api-error-rate-spike.md)

## Tags
`family: networking`
`severity: high`
`services: istio, service-mesh, envoy`
