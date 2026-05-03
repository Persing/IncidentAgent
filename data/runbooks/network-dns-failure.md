# DNS Resolution Failure — Internal Cluster DNS

## Overview
Cluster DNS failures mean pods cannot resolve service names (e.g., `my-service.namespace.svc.cluster.local`) or external hostnames. This causes widespread application failures that may look like network timeouts, connection refused errors, or dependency unavailability — making DNS failure a common false-lead during incident response. In Kubernetes, CoreDNS handles all in-cluster name resolution. A CoreDNS outage or degradation will affect every service that makes DNS lookups, which is nearly everything.

## Alert Signatures
- `CoreDNSErrorsHigh` — `rate(coredns_dns_responses_total{rcode="SERVFAIL"}[5m]) > 0.01`
- `CoreDNSLatencyHigh` — `histogram_quantile(0.99, coredns_dns_request_duration_seconds_bucket) > 4`
- Application errors: `dial tcp: lookup <service-name>: no such host`
- Application errors: `connection refused` when using service names (DNS resolves to wrong IP after restart)
- Alertmanager: `[HIGH] CoreDNS SERVFAIL rate is elevated`

## Common Causes
- CoreDNS pods are down, crash-looping, or under-resourced (CPU/memory)
- CoreDNS ConfigMap has a syntax error after a recent edit
- `ndots` misconfiguration causing excessive upstream DNS lookups (latency, not failure)
- kube-dns service (`kube-dns` in `kube-system`) has no endpoints — CoreDNS pods not matching selector
- Network policy blocking UDP/TCP port 53 between pods and CoreDNS
- Node-level iptables rules corrupted (conntrack table full, or a recent iptables flush)
- External DNS upstream unreachable (only affects external hostname resolution)

## Diagnostic Steps
1. Verify CoreDNS pods are running:
   ```bash
   kubectl get pods -n kube-system -l k8s-app=kube-dns
   kubectl describe pods -n kube-system -l k8s-app=kube-dns
   ```
2. Check CoreDNS logs for errors:
   ```bash
   kubectl logs -n kube-system -l k8s-app=kube-dns --tail=100
   ```
   Look for `SERVFAIL`, `plugin/errors`, or `i/o timeout` lines.
3. Test DNS resolution from inside a pod:
   ```bash
   kubectl run dns-test --image=busybox:1.28 --restart=Never -it --rm -- nslookup kubernetes.default
   kubectl run dns-test --image=busybox:1.28 --restart=Never -it --rm -- nslookup google.com
   ```
4. Check the kube-dns service endpoints:
   ```bash
   kubectl get endpoints kube-dns -n kube-system
   ```
   Should show CoreDNS pod IPs. If empty, the selector is broken.
5. Check CoreDNS ConfigMap for syntax errors:
   ```bash
   kubectl get configmap coredns -n kube-system -o yaml
   ```
6. Check for network policies that may block DNS:
   ```bash
   kubectl get networkpolicy --all-namespaces
   ```

## Resolution Steps
1. **If CoreDNS pods are unhealthy:** Restart them:
   ```bash
   kubectl rollout restart deployment/coredns -n kube-system
   ```
2. **If CoreDNS ConfigMap has a syntax error:** Roll back to the last known-good config from source control and apply it.
3. **If kube-dns service has no endpoints:** Check the CoreDNS deployment selector matches the pod labels:
   ```bash
   kubectl get deployment coredns -n kube-system -o jsonpath='{.spec.selector}'
   kubectl get pods -n kube-system --show-labels | grep coredns
   ```
4. **If network policy is blocking DNS:** Ensure pods have egress rules allowing UDP/TCP port 53 to the CoreDNS pod CIDR.
5. **If iptables/conntrack:** On the affected node(s):
   ```bash
   conntrack -C   # check conntrack table size
   sysctl net.netfilter.nf_conntrack_max
   ```
   If conntrack is full, increase the limit or reduce connection rate.
6. **If external DNS only is failing:** Check upstream DNS configuration in the CoreDNS Corefile (`forward` plugin) and verify the upstream resolver is reachable from the nodes.

## Escalation Criteria
Escalate to the on-call engineer if:
- CoreDNS restart does not restore resolution
- DNS failure is cluster-wide (all namespaces affected)
- The root cause is iptables or kernel networking — escalate to infrastructure team
- An upstream DNS provider outage is confirmed

## Ownership

| Layer                                                        | Team                                                                           | Contact                                                 |
|--------------------------------------------------------------|--------------------------------------------------------------------------------|---------------------------------------------------------|
| CoreDNS, kube-dns service, in-cluster DNS config             | **Platform Engineering**                                                       | Slack: `#platform-oncall` · PD: `platform-engineering`  |
| Upstream/authoritative DNS, external resolver infrastructure | **Network / Infrastructure**                                                   | Slack: `#network-oncall` · PD: `network-infrastructure` |
| Network policy blocking DNS traffic                          | **Platform Engineering** (policy) + **Network / Infrastructure** (if firewall) | Both channels                                           |

**Boundary:** Platform Engineering owns everything CoreDNS and in-cluster. Network / Infrastructure owns the authoritative DNS servers and any upstream resolvers configured in the Corefile's `forward` plugin. If external hostname resolution is broken but internal service resolution works, escalate to Network / Infrastructure. If both internal and external resolution are broken, start with Platform Engineering (CoreDNS) — external resolution flows through CoreDNS first.

## Related Runbooks
- [network-timeout.md](network-timeout.md) — DNS failure often presents as connection timeout
- [network-service-mesh-error.md](network-service-mesh-error.md) — service mesh may interfere with DNS

## Tags
`family: networking`
`severity: critical`
`services: coredns, kubernetes-networking`
