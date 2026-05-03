# Disk Pressure — Node Disk or Inode Exhaustion

## Overview
A node enters disk pressure when available disk space or inodes fall below kubelet's eviction thresholds (default: 10% disk available, or 5% inodes available). When this happens, kubelet begins evicting pods from the node — starting with BestEffort pods, then Burstable, then Guaranteed. The node also stops accepting new pod scheduling. In a logging-heavy or high-churn environment, disk fills faster than expected. The most common culprits are container log accumulation, large image layers, or coredumps.

## Alert Signatures
- `KubeNodeDiskPressure` — `kube_node_status_condition{condition="DiskPressure",status="true"} == 1`
- `NodeFilesystemAlmostOutOfSpace` — `(node_filesystem_avail_bytes / node_filesystem_size_bytes) < 0.1`
- `NodeFilesystemFilesFillingUp` — inode exhaustion projection
- Node shows `DiskPressure=True` in `kubectl describe node`
- Alertmanager: `[HIGH] Node <node-name> disk pressure detected`

## Common Causes
- Container log files growing unbounded (no log rotation or rotation misconfigured)
- Large container images not being garbage-collected by kubelet
- Coredumps or heap dumps written to the node filesystem by crashing processes
- `/tmp` or emptyDir volumes filling up with application-generated temp files
- etcd data directory growing (on control plane nodes — see [storage-etcd-disk.md](storage-etcd-disk.md))
- High pod churn leaving overlayFS layers that weren't cleaned up

## Diagnostic Steps
1. Check node disk usage at a high level:
   ```bash
   # SSH into the node or run via a privileged daemonset pod
   df -h
   df -i   # inode usage
   ```
2. Find the largest consumers on the filesystem:
   ```bash
   du -sh /var/log/containers/* 2>/dev/null | sort -rh | head -20
   du -sh /var/lib/docker/overlay2/* 2>/dev/null | sort -rh | head -10
   du -sh /var/lib/containerd/* 2>/dev/null | sort -rh | head -10
   ```
3. Check for coredumps:
   ```bash
   ls -lh /var/crash/ /tmp/*.core /core.* 2>/dev/null
   ```
4. Check kubelet image garbage collection configuration:
   ```bash
   cat /var/lib/kubelet/config.yaml | grep -i "imageGC\|eviction"
   ```
5. Look at which pods kubelet has already evicted:
   ```bash
   kubectl get events -n <namespace> | grep Evicted
   kubectl get pods --all-namespaces | grep Evicted
   ```
6. Check current disk pressure condition on the node:
   ```bash
   kubectl describe node <node-name> | grep -A5 "DiskPressure"
   ```

## Resolution Steps
1. **Immediate — free space fast:**
   ```bash
   # Clear old container logs
   find /var/log/containers -name "*.log" -mtime +1 -exec truncate -s 0 {} \;
   # Remove dangling images
   crictl rmi --prune   # or: docker image prune -f
   # Remove coredumps
   rm -f /var/crash/* /tmp/*.core
   # Remove evicted pod records
   kubectl get pods --all-namespaces | grep Evicted | awk '{print $1, $2}' | xargs -n2 kubectl delete pod -n
   ```
2. **If log volume is the cause:** Confirm log rotation is configured correctly. In Kubernetes, container logs are rotated by kubelet with `containerLogMaxSize` and `containerLogMaxFiles`. If not set:
   ```bash
   # Check kubelet config
   cat /var/lib/kubelet/config.yaml | grep containerLog
   # Recommended: containerLogMaxSize: "50Mi", containerLogMaxFiles: 5
   ```
3. **If images are accumulating:** Tune kubelet GC thresholds in the kubelet config (`imageGCHighThresholdPercent`, `imageGCLowThresholdPercent`).
4. **If inode exhaustion and disk space looks fine:** The culprit is usually a large number of small files in a log or temp directory. Use `find / -xdev -printf '%h\n' | sort | uniq -c | sort -rn | head` to identify the directory with the most files.
5. **If the node cannot be remediated quickly:** Cordon and drain it, let pods reschedule to healthy nodes:
   ```bash
   kubectl cordon <node-name>
   kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data
   ```

## Escalation Criteria
Escalate to the on-call engineer if:
- Disk cannot be freed fast enough to stop evictions and services are going down
- Multiple nodes are experiencing simultaneous disk pressure (possible cluster-wide log storm)
- The disk is full and contains data that must not be deleted (stateful workload data)
- Disk pressure is causing the node to go NotReady (see [compute-node-not-ready.md](compute-node-not-ready.md))

## Ownership

| Layer                                                   | Team                          | Contact                                                |
|---------------------------------------------------------|-------------------------------|--------------------------------------------------------|
| Node disk management, kubelet eviction config, image GC | **Platform Engineering**      | Slack: `#platform-oncall` · PD: `platform-engineering` |
| Application log volume, log rotation config             | **Application Team**          | See service `CODEOWNERS` · Slack: `#team-<service>`    |
| Physical disk hardware, disk replacement                | **Hardware & Datacenter Ops** | Slack: `#dc-ops-oncall` · PD: `datacenter-ops`         |

**Boundary:** Platform Engineering owns node-level disk configuration (kubelet log rotation, image GC thresholds, eviction settings) and is first responder for disk pressure alerts. The root cause often traces to an application team's logging behavior (no rotation, debug logging left on) — once identified, the fix belongs to that application team. Physical disk failures or replacements are Hardware & Datacenter Ops territory.

## Related Runbooks
- [compute-node-not-ready.md](compute-node-not-ready.md) — disk pressure can trigger NotReady
- [storage-etcd-disk.md](storage-etcd-disk.md) — etcd-specific disk issues on control plane
- [storage-log-volume-full.md](storage-log-volume-full.md) — log volume inside a container

## Tags
`family: storage`
`severity: high`
`services: kubernetes-nodes`
