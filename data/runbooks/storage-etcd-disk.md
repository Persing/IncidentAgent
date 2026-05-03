# etcd Disk Pressure — Disk Latency or Space Exhaustion on Control Plane

## Overview
etcd is the key-value store backing all Kubernetes cluster state. It is extremely sensitive to disk I/O latency — etcd uses a consensus protocol (Raft) that requires disk writes to complete within strict timeouts. If disk latency spikes or disk space runs out, etcd leader elections fail, API server requests time out, and the entire cluster control plane can become unavailable. etcd disk issues are cluster-wide emergencies. Even if your workloads appear healthy, a degraded etcd means no new pods can be scheduled, no changes can be applied, and recovery operations are blocked.

## Alert Signatures
- `EtcdHighFsyncDurations` — `histogram_quantile(0.99, etcd_disk_wal_fsync_duration_seconds_bucket) > 0.01`
- `EtcdHighCommitDurations` — `histogram_quantile(0.99, etcd_disk_backend_commit_duration_seconds_bucket) > 0.25`
- `EtcdInsufficientMembers` — fewer than quorum members healthy
- `EtcdNoLeader` — no etcd leader elected
- API server returning 504 or timing out on kubectl operations
- Alertmanager: `[CRITICAL] etcd high fsync latency detected`

## Common Causes
- etcd data directory on a shared disk with other noisy I/O workloads (logs, container overlay, etc.)
- etcd data directory disk filling up (default: no compaction or defragmentation configured)
- Cloud block volume with insufficient IOPS for etcd write throughput at scale
- Heavy API server write load (mass deployments, controllers in tight reconciliation loops)
- etcd database size growing beyond default quota (default 2GiB, `etcd_server_quota_backend_bytes`)
- Control plane node CPU starvation causing fsync delays

## Diagnostic Steps
1. Check etcd member health:
   ```bash
   ETCDCTL_API=3 etcdctl --endpoints=https://127.0.0.1:2379 \
     --cacert=/etc/kubernetes/pki/etcd/ca.crt \
     --cert=/etc/kubernetes/pki/etcd/healthcheck-client.crt \
     --key=/etc/kubernetes/pki/etcd/healthcheck-client.key \
     endpoint health
   ```
2. Check etcd database size and quota:
   ```bash
   ETCDCTL_API=3 etcdctl ... endpoint status --write-out=table
   ```
   Look at `DB SIZE` and compare to quota.
3. Check disk usage on the control plane node:
   ```bash
   df -h /var/lib/etcd
   iostat -x 1 5   # check iowait and disk utilization
   ```
4. Check fsync latency in Prometheus:
   - `histogram_quantile(0.99, etcd_disk_wal_fsync_duration_seconds_bucket)` — should be < 10ms
   - `histogram_quantile(0.99, etcd_disk_backend_commit_duration_seconds_bucket)` — should be < 25ms
5. Check if defragmentation is needed (fragmentation ratio > 0.3 is significant):
   ```bash
   ETCDCTL_API=3 etcdctl ... endpoint status --write-out=json | jq '.[] | {endpoint: .Endpoint, dbSize: .Status.dbSize, dbSizeInUse: .Status.dbSizeInUse}'
   ```

## Resolution Steps
1. **If database is near quota:** Compact and defragment etcd to reclaim space. Do this on one member at a time:
   ```bash
   # Get current revision
   rev=$(ETCDCTL_API=3 etcdctl ... endpoint status --write-out=json | jq '.[] | .Status.header.revision' | head -1)
   # Compact
   ETCDCTL_API=3 etcdctl ... compact $rev
   # Defragment (will cause brief unavailability for this member)
   ETCDCTL_API=3 etcdctl ... defrag --endpoints=<single-member-endpoint>
   ```
2. **If disk space is full:** Clear etcd WAL snapshots older than retention window (do not delete live WAL files):
   ```bash
   ls -lh /var/lib/etcd/member/wal/
   ```
   Move the etcd data directory to a dedicated disk if possible.
3. **If disk IOPS are insufficient:** Move etcd to an SSD-backed volume or increase the volume's provisioned IOPS via cloud provider.
4. **If noisy neighbor I/O:** Move etcd data directory to a dedicated volume separate from container logs and overlay filesystems.
5. **If etcd has lost quorum:** This is a disaster recovery scenario — do not attempt without reading the etcd disaster recovery documentation for your Kubernetes distribution.

## Escalation Criteria
Escalate to the senior on-call or cluster admin immediately if:
- etcd has lost quorum (fewer than (N/2 + 1) members healthy)
- `EtcdNoLeader` alert is firing
- kubectl commands are timing out cluster-wide
- Defragmentation alone does not bring fsync latency below alert threshold

## Ownership

| Layer | Team | Contact |
|---|---|---|
| etcd cluster health, compaction, defragmentation | **Platform Engineering** (senior/lead on-call) | Slack: `#platform-oncall` · PD: `platform-engineering` |
| Control plane node hardware, disk replacement | **Hardware & Datacenter Ops** | Slack: `#dc-ops-oncall` · PD: `datacenter-ops` |
| Cloud-managed etcd / control plane (e.g., EKS, GKE) | **Platform Engineering** → cloud provider support | Platform Eng opens the provider ticket |

**Boundary:** etcd is exclusively owned by Platform Engineering — no other team should touch etcd directly. This is a full Platform Engineering escalation from the moment the alert fires. If the alert is caused by disk hardware on the control plane node, Platform Engineering coordinates with Hardware & Datacenter Ops but remains the incident commander. etcd disaster recovery (quorum loss) requires the most senior available Platform Engineering staff — do not attempt without explicit authorization.

## Related Runbooks
- [storage-disk-pressure.md](storage-disk-pressure.md) — node-level disk pressure affecting etcd host
- [compute-node-not-ready.md](compute-node-not-ready.md) — etcd failure can cause API server issues that look like node NotReady

## Tags
`family: storage`
`severity: critical`
`services: kubernetes-control-plane, etcd`
