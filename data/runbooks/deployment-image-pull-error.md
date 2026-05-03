# ImagePullBackOff — Container Image Pull Failing

## Overview
ImagePullBackOff means Kubernetes cannot pull the container image for a pod. The pod will never start. This is one of the most common deployment failures and has a small set of well-understood root causes. Despite being straightforward, it blocks all deployments and rollouts until resolved. The backoff means Kubernetes waits increasingly longer between retry attempts (up to 5 minutes), so it does not resolve itself quickly even after the underlying issue is fixed — pods may need to be deleted and recreated.

## Alert Signatures
- Pod status shows `ImagePullBackOff` or `ErrImagePull` in `kubectl get pods`
- Event: `Failed to pull image "<image>": rpc error: code = Unknown desc = failed to pull and unpack image`
- Deployment rollout stuck at 0/N ready
- Alertmanager: `[HIGH] Pod <pod-name> is in ImagePullBackOff for > 5 minutes`

## Common Causes
- Image tag does not exist in the registry (typo, tag was deleted, or CI/CD failed to push)
- Wrong registry URL in the image spec
- Missing or expired `imagePullSecret` for a private registry
- Registry is temporarily unavailable or rate-limited (Docker Hub has pull rate limits for unauthenticated pulls)
- Image digest changed after a tag was re-pushed (mutable tag used and now references a missing layer)
- Node has no network access to the registry (firewall rule, security group, or egress policy)
- The registry requires authentication and the node's pull credentials have expired

## Diagnostic Steps
1. Get the exact error from pod events:
   ```bash
   kubectl describe pod <pod-name> -n <namespace> | grep -A10 "Events:"
   ```
   The event will say: image not found, auth failure, or network error.
2. Confirm the image tag exists in the registry:
   ```bash
   # For Docker Hub
   docker manifest inspect <image>:<tag>
   # For private registry (if you have access)
   curl -s -u <user>:<token> https://<registry>/v2/<image>/tags/list
   ```
3. Check if imagePullSecret is configured on the pod:
   ```bash
   kubectl get pod <pod-name> -n <namespace> -o jsonpath='{.spec.imagePullSecrets}'
   ```
4. Check if the imagePullSecret exists and is valid:
   ```bash
   kubectl get secret <pull-secret-name> -n <namespace>
   kubectl get secret <pull-secret-name> -n <namespace> -o jsonpath='{.data.\.dockerconfigjson}' | base64 -d | jq .
   ```
   Verify the credential in `.dockerconfigjson` matches the registry and is not expired.
5. Test image pull from the node (requires SSH or a privileged pod):
   ```bash
   crictl pull <image>:<tag>
   # or
   docker pull <image>:<tag>
   ```
6. Check if the node has egress access to the registry:
   ```bash
   kubectl exec -it <pod-name-on-same-node> -- curl -I https://<registry>/v2/
   ```

## Resolution Steps
1. **If image tag doesn't exist:** Push the correct image or fix the tag in the deployment spec:
   ```bash
   kubectl set image deployment/<deployment-name> <container>=<registry>/<image>:<correct-tag> -n <namespace>
   ```
2. **If imagePullSecret is missing:** Create it and add it to the pod spec:
   ```bash
   kubectl create secret docker-registry <secret-name> \
     --docker-server=<registry> \
     --docker-username=<user> \
     --docker-password=<token> \
     -n <namespace>
   # Then add to deployment:
   kubectl patch deployment <deployment-name> -n <namespace> \
     -p '{"spec":{"template":{"spec":{"imagePullSecrets":[{"name":"<secret-name>"}]}}}}'
   ```
3. **If credentials expired:** Rotate the registry token and update the secret:
   ```bash
   kubectl delete secret <secret-name> -n <namespace>
   # Recreate with new token (same command as above)
   ```
4. **If registry is rate-limited (Docker Hub):** Configure an authenticated pull or migrate to a private registry mirror.
5. **After fixing:** Delete the ImagePullBackOff pods to force immediate retry (backoff will otherwise delay recovery):
   ```bash
   kubectl delete pod <pod-name> -n <namespace>
   ```

## Escalation Criteria
Escalate to the on-call engineer if:
- The registry itself is down and there is no mirror or fallback
- The image tag existed but was deleted — need to determine why (CI/CD pipeline issue)
- The pull secret was revoked as part of a security incident and credentials need emergency rotation
- Multiple deployments across multiple namespaces are failing (registry-wide issue)

## Ownership

| Layer                                               | Team                                                                    | Contact                                                |
|-----------------------------------------------------|-------------------------------------------------------------------------|--------------------------------------------------------|
| Application image, CI/CD pipeline, image tag        | **Application Team**                                                    | See service `CODEOWNERS` · Slack: `#team-<service>`    |
| imagePullSecret provisioning, registry proxy/mirror | **Platform Engineering**                                                | Slack: `#platform-oncall` · PD: `platform-engineering` |
| Registry credentials, secret rotation               | **Security Engineering** (if centrally managed) or **Application Team** | Slack: `#security-oncall` or `#team-<service>`         |

**Boundary:** Application teams own their image tags and CI/CD pipeline that pushes images. Platform Engineering owns the cluster-level pull secret infrastructure and any registry mirror or proxy. If pull credentials were revoked as part of a security event, Security Engineering leads the rotation. For Docker Hub rate limiting, Platform Engineering configures the authenticated pull or mirror solution.

## Related Runbooks
- [deployment-stuck-rollout.md](deployment-stuck-rollout.md) — ImagePullBackOff is a common rollout-stall cause
- [compute-pending-pods.md](compute-pending-pods.md) — if pod scheduling succeeds but image pull fails

## Tags
`family: deployment`
`severity: high`
`services: any, container-registry`
