# TLS Certificate Expiry — Warning or Hard Failure

## Overview
TLS certificate expiry has two phases: a warning phase (cert is valid but will expire soon) and a hard failure (cert has expired and TLS handshakes fail). The warning phase is a soft deadline that should trigger rotation before impact. The hard failure causes immediate, complete service unavailability for any client that validates the certificate — which is all clients using HTTPS. Certificate failures produce confusing error messages (`x509: certificate has expired`) that can look like a network or application issue to engineers unfamiliar with the chain.

## Alert Signatures
- `CertificateExpiryWarning` — `(x509_cert_expiry - time()) / 86400 < 30` (30 days out)
- `CertificateExpiryCritical` — `(x509_cert_expiry - time()) / 86400 < 7` (7 days out)
- `ssl_certificate_expiry_seconds < 0` — cert has already expired
- Client errors: `x509: certificate has expired or is not yet valid`
- Ingress returning 525 (SSL Handshake Failed) or browsers showing cert error
- Alertmanager: `[CRITICAL] TLS certificate for <domain> expires in <N> days`

## Common Causes
- Automated cert rotation (cert-manager) failing silently — challenge not completing, ACME rate limit hit, or DNS-01 misconfigured
- Manual cert was issued and the renewal was not scheduled or was forgotten
- Cert was rotated but the new cert was not propagated to all secrets or load balancers
- Wildcard cert covering multiple services expires and takes down all of them at once
- Internal CA cert (used for mTLS between services) expired
- cert-manager's CertificateRequest is stuck in a pending state

## Diagnostic Steps
1. Verify certificate expiry via openssl:
   ```bash
   # External endpoint
   echo | openssl s_client -connect <domain>:443 2>/dev/null | openssl x509 -noout -dates
   # Internal service (from inside the cluster)
   kubectl exec -it <pod-name> -n <namespace> -- openssl s_client -connect <service>:<port> 2>/dev/null | openssl x509 -noout -dates
   ```
2. Check cert-manager certificate resources:
   ```bash
   kubectl get certificate -A
   kubectl describe certificate <cert-name> -n <namespace>
   ```
   Look at `Status.Conditions` — should show `Ready=True`.
3. Check cert-manager CertificateRequest and Order status:
   ```bash
   kubectl get certificaterequest -n <namespace>
   kubectl describe certificaterequest <request-name> -n <namespace>
   kubectl get order -n <namespace>
   kubectl describe order <order-name> -n <namespace>
   ```
4. Check cert-manager controller logs for errors:
   ```bash
   kubectl logs -n cert-manager -l app=cert-manager --tail=100 | grep -i "error\|fail\|challenge"
   ```
5. Check the Kubernetes secret containing the cert:
   ```bash
   kubectl get secret <tls-secret-name> -n <namespace> -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -noout -dates
   ```

## Resolution Steps
1. **If cert-manager challenge is failing (ACME):**
   - Check DNS-01 or HTTP-01 challenge records:
     ```bash
     kubectl get challenge -n <namespace>
     kubectl describe challenge <challenge-name> -n <namespace>
     ```
   - For DNS-01: verify the `_acme-challenge` TXT record is propagated
   - For HTTP-01: verify the ACME solver pod is running and reachable at `/.well-known/acme-challenge/`
   - Fix the challenge and trigger re-issuance: `kubectl delete certificaterequest <name> -n <namespace>`
2. **If cert-manager is healthy but not rotating:** Manually trigger rotation:
   ```bash
   kubectl annotate certificate <cert-name> -n <namespace> cert-manager.io/issue-temporary-certificate="true"
   # Or delete the existing cert to force re-issuance
   kubectl delete secret <tls-secret-name> -n <namespace>
   ```
3. **If cert is already expired (hard failure):** Manually create or import a new certificate as a Kubernetes secret immediately:
   ```bash
   kubectl create secret tls <tls-secret-name> -n <namespace> \
     --cert=new.crt --key=new.key \
     --dry-run=client -o yaml | kubectl apply -f -
   ```
   Then restart pods that mount the secret.
4. **If load balancer cert is managed outside Kubernetes:** Rotate via cloud provider console/CLI and update the listener.

## Escalation Criteria
Escalate to the on-call engineer if:
- Certificate has already expired and manual rotation is not completing in under 15 minutes
- The cert covers a wildcard or multiple services — blast radius is large
- cert-manager itself is unhealthy (check cert-manager pods)
- Internal CA cert has expired (affects mTLS between all services — cluster-wide impact)

## Ownership

| Layer | Team | Contact |
|---|---|---|
| cert-manager, ACME configuration, cluster-wide cert policy | **Platform Engineering** | Slack: `#platform-oncall` · PD: `platform-engineering` |
| Internal CA, mTLS certificates, PKI infrastructure | **Security Engineering** | Slack: `#security-oncall` · PD: `security-engineering` |
| External load balancer certificates (cloud-managed) | **Platform Engineering** (configures) + cloud provider | Platform Eng owns the LB cert config |
| Application-specific cert usage and secrets | **Application Team** | See service `CODEOWNERS` · Slack: `#team-<service>` |

**Boundary:** Platform Engineering owns cert-manager and the automation that rotates most certificates. Security Engineering owns the internal CA and any certificates that are part of the PKI/mTLS trust chain — do not rotate internal CA certs without Security Engineering involvement. For ACME (Let's Encrypt) failures, Platform Engineering is the first call. If the expired cert is part of the internal mTLS fabric (service-to-service trust), treat it as a Security Engineering incident from the start.

## Related Runbooks
- [network-ingress-502.md](network-ingress-502.md) — expired cert causes ingress to return 5xx
- [deployment-stuck-rollout.md](deployment-stuck-rollout.md) — cert rotation may require pod restarts

## Tags
`family: networking`
`severity: critical`
`services: ingress, tls, cert-manager`
