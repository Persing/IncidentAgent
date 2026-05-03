# PVC Mount Failure — PersistentVolumeClaim Fails to Mount

## Overview
A PersistentVolumeClaim (PVC) mount failure prevents a pod from starting because its required storage volume cannot be attached or mounted. The pod stays in `ContainerCreating` or `Pending` state indefinitely. This is a hard blocker for stateful workloads — databases, model weight caches, and any service that requires persistent storage. Mount failures are distinct from PVC Pending (no PV available) and can occur even when the PVC is `Bound`.

## Alert Signatures
- Pod stuck in `ContainerCreating` for > 5 minutes
- Event: `Unable to attach or mount volumes: unmounted volumes=[<volume-name>]`
- Event: `Failed to mount volume: ...`
- `kube_pod_status_phase{phase="Pending"}` sustained for pods with PVC requirements

## Common Causes
- Volume still attached to a previous (terminated) pod on a different node — cloud block volumes are exclusive-access (RWO)
- Cloud volume attachment API failure or timeout (transient cloud provider issue)
- StorageClass is unavailable, deleted, or the provisioner pod is unhealthy
- PVC is stuck in `Terminating` state blocking re-creation
- Node does not have required kernel modules for the filesystem type (e.g., NFS, ceph)
- File permission or ownership mismatch on the volume causing mount to succeed but container to fail on startup
- PV capacity mismatch — PVC requests more than the available PV

## Diagnostic Steps
1. Check pod events:
   ```bash
   kubectl describe pod <pod-name> -n <namespace> | grep -A20 "Events:"
   ```
   The volume attachment error message is in Events.
2. Check PVC and PV status:
   ```bash
   kubectl get pvc -n <namespace>
   kubectl describe pvc <pvc-name> -n <namespace>
   kubectl get pv <pv-name>
   kubectl describe pv <pv-name>
   ```
3. Check if the volume is still attached to a different node:
   ```bash
   kubectl get volumeattachment
   kubectl describe volumeattachment <attachment-name>
   ```
4. Check CSI driver or provisioner health:
   ```bash
   kubectl get pods -n kube-system | grep -E "csi|provisioner|ebs|oci-bvs"
   kubectl logs -n kube-system <csi-controller-pod> --tail=100
   ```
5. Check StorageClass exists and is active:
   ```bash
   kubectl get storageclass
   kubectl describe storageclass <class-name>
   ```

## Resolution Steps
1. **If volume is attached to a terminated pod on another node:** Delete the stale VolumeAttachment to force detach:
   ```bash
   kubectl get volumeattachment | grep <pv-name>
   kubectl delete volumeattachment <attachment-name>
   ```
   The volume will re-attach to the new node. Monitor pod events.
2. **If CSI driver pod is unhealthy:** Restart the CSI controller:
   ```bash
   kubectl rollout restart deployment/<csi-controller-name> -n kube-system
   ```
3. **If PVC is stuck in Terminating:** Remove the finalizer to allow deletion:
   ```bash
   kubectl patch pvc <pvc-name> -n <namespace> -p '{"metadata":{"finalizers":null}}'
   ```
   Then re-create the PVC.
4. **If StorageClass is missing:** Re-apply the StorageClass manifest from source control or IaC.
5. **If file permission issue:** Add a `securityContext.fsGroup` to the pod spec so the volume is chowned correctly on mount:
   ```yaml
   securityContext:
     fsGroup: 1000
   ```

## Escalation Criteria
Escalate to the on-call engineer if:
- Force-deleting VolumeAttachment does not resolve the issue after 10 minutes
- The PV contains critical data, and you are uncertain whether it is safe to manipulate
- CSI driver restart does not restore provisioning capability
- Multiple PVCs are simultaneously failing to mount (possible cloud provider storage outage)

## Ownership

| Layer                                      | Team                                              | Contact                                                |
|--------------------------------------------|---------------------------------------------------|--------------------------------------------------------|
| StorageClass, CSI driver, PV lifecycle     | **Platform Engineering**                          | Slack: `#platform-oncall` · PD: `platform-engineering` |
| Application PVC spec, fsGroup, mount paths | **Application Team**                              | See service `CODEOWNERS` · Slack: `#team-<service>`    |
| Cloud block volume infrastructure          | **Platform Engineering** → cloud provider support | Platform Eng opens provider ticket                     |
| Physical storage hardware (SAN, NAS)       | **Hardware & Datacenter Ops**                     | Slack: `#dc-ops-oncall` · PD: `datacenter-ops`         |

**Boundary:** Platform Engineering owns the CSI driver and StorageClass. Application teams own their PVC spec and how they configure the mounted volume in their pod. If the CSI driver is healthy but the cloud provider's volume attachment API is failing, Platform Engineering escalates to the cloud provider. Do not delete PVs containing application data without explicit sign-off from the owning application team.

## Related Runbooks
- [compute-pending-pods.md](compute-pending-pods.md) — pods with PVC failures appear Pending
- [compute-crashloop.md](compute-crashloop.md) — pod may crash on startup if volume mounts succeed but data is corrupted
- [storage-disk-pressure.md](storage-disk-pressure.md)

## Tags
`family: storage`
`severity: high`
`services: stateful-workloads, databases, model-cache`
