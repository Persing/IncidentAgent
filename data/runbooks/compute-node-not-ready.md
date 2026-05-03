# Node NotReady — Node Drops Out of Ready State

## Overview
A Kubernetes node transitions to `NotReady` when the kubelet on that node stops reporting a healthy heartbeat to the API server. After a configurable timeout (default 40s), the node controller marks it `NotReady` and begins evicting pods. If the node stays `NotReady` for the pod eviction timeout (default 5 minutes), pods are terminated and rescheduled elsewhere. During this window, affected workloads may be unavailable. This is one of the highest-urgency alerts in a cluster — it can silently take down multiple services at once.

## Alert Signatures
- `KubeNodeNotReady` — `kube_node_status_condition{condition="Ready",status="true"} == 0`
- `KubeNodeUnreachable` — node unreachable from API server
- Node shows `NotReady` in `kubectl get nodes`
- Alertmanager: `[CRITICAL] Node <node-name> is NotReady`

## Common Causes
- Kubelet process crashed or hung on the node
- Node-level OS or kernel panic (check serial console / OOB management)
- Node disk pressure causing kubelet to stop reporting (see [storage-disk-pressure.md](storage-disk-pressure.md))
- Network partition between the node and the API server
- Node memory pressure causing kubelet eviction loop
- Cloud provider instance health event (hardware failure, live migration, spot preemption)
- High iowait causing kubelet heartbeat to miss its deadline

## Diagnostic Steps
1. Check node status and conditions:
   ```bash
   kubectl get nodes
   kubectl describe node <node-name> | grep -A20 "Conditions:"
   ```
   Look at `MemoryPressure`, `DiskPressure`, `PIDPressure`, and `Ready` conditions.
2. List pods that were running on this node:
   ```bash
   kubectl get pods --all-namespaces --field-selector spec.nodeName=<node-name>
   ```
3. Check if the node is reachable at the network layer (from a bastion or another node in the same subnet).
4. If cloud-hosted: check the cloud provider console for instance health events, hardware failures, or scheduled maintenance on `<node-name>`.
5. If reachable via SSH: check kubelet status and logs:
   ```bash
   systemctl status kubelet
   journalctl -u kubelet --since "10 minutes ago" -n 200
   ```
6. Check for disk pressure on the node:
   ```bash
   df -h   # run on the node via SSH
   journalctl -u kubelet | grep -i "evict\|pressure\|disk"
   ```
7. Check for kernel panics or OOM events:
   ```bash
   dmesg | tail -100
   cat /var/log/syslog | grep -E "oom-killer|kernel panic" | tail -50
   ```

## Resolution Steps
1. **If kubelet is stopped and node is reachable:** Restart kubelet:
   ```bash
   systemctl restart kubelet
   ```
   Monitor for the node to return to Ready (watch `kubectl get nodes -w`).
2. **If disk pressure:** Clear space immediately (see [storage-disk-pressure.md](storage-disk-pressure.md)) then restart kubelet.
3. **If cloud instance health event:** Follow cloud provider runbook for the specific event type. For spot preemption: confirm replacement nodes are launching.
4. **If node is unreachable and unrecoverable:** Cordon and drain from a healthy node, then terminate the instance:
   ```bash
   kubectl cordon <node-name>
   kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data --force
   ```
   Then terminate the instance via cloud provider CLI and allow the autoscaler to replace it.
5. Monitor pod rescheduling on healthy nodes:
   ```bash
   kubectl get pods --all-namespaces -o wide -w | grep Pending
   ```

## Escalation Criteria
Escalate to the on-call engineer if:
- Multiple nodes go NotReady simultaneously (possible network partition, cloud provider outage, or cluster-level issue)
- Node cannot be recovered or drained and critical stateful workloads are stuck
- Pods fail to reschedule due to scheduling constraints (resource exhaustion, taints)
- The cause is unknown after checking kubelet logs and cloud provider console

## Ownership

| Layer                                       | Team                                              | Contact                                                 |
|---------------------------------------------|---------------------------------------------------|---------------------------------------------------------|
| Node health, kubelet, cluster autoscaler    | **Platform Engineering**                          | Slack: `#platform-oncall` · PD: `platform-engineering`  |
| Physical node hardware, OOB management      | **Hardware & Datacenter Ops**                     | Slack: `#dc-ops-oncall` · PD: `datacenter-ops`          |
| Cloud instance health events (cloud-hosted) | **Platform Engineering** → cloud provider support | Platform Eng opens the provider ticket                  |
| Network partition to node                   | **Network / Infrastructure**                      | Slack: `#network-oncall` · PD: `network-infrastructure` |

**Boundary:** Platform Engineering is first responder for any NotReady node. They determine whether the cause is software (kubelet, disk, OS) or hardware. If the node is physically inaccessible or shows hardware fault indicators (OOB logs, console errors), they hand off to Hardware & Datacenter Ops. If the node is unreachable due to a network-level partition (not just kubelet), they loop in Network / Infrastructure. Cloud instance events (spot preemption, live migration) are handled by Platform Engineering via the cloud provider console.

## Related Runbooks
- [storage-disk-pressure.md](storage-disk-pressure.md) — disk pressure is a common NotReady trigger
- [compute-pending-pods.md](compute-pending-pods.md) — pods evicted from this node may get stuck Pending
- [compute-oom-killed.md](compute-oom-killed.md) — memory pressure on the node can correlate

## Tags
`family: compute`
`severity: critical`
`services: kubernetes-control-plane, any`
