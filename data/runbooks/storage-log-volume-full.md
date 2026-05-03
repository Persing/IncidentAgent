# Log Volume Full — Container Filesystem Filled by Logs

## Overview
This alert fires when a container's writable filesystem or a mounted log volume is near or at capacity due to application log output. Unlike node-level disk pressure, this affects a single container — the process may stop writing logs (or crash) while the node itself remains healthy. For services that write structured logs to a mounted volume (rather than stdout), this can silently cause log loss or application crashes with cryptic "no space left on device" errors.

## Alert Signatures
- `ContainerFilesystemAlmostFull` — `(container_fs_usage_bytes / container_fs_limit_bytes) > 0.85`
- Application logs showing `write /app/logs/service.log: no space left on device`
- `node_filesystem_avail_bytes{mountpoint="/logs"}` trending to zero
- Alertmanager: `[HIGH] Log volume on <pod-name> is over 85% full`

## Common Causes
- Log rotation not configured or not working inside the container
- Debug logging enabled and left on after an investigation
- A logging sink (Fluentd, Filebeat) has fallen behind or stopped, allowing logs to accumulate
- Log volume sized too small relative to normal write rate
- A burst of errors causing a log storm (e.g., tight retry loop producing thousands of error lines/second)
- Coredumps or heap dumps being written to the log volume

## Diagnostic Steps
1. Check filesystem usage inside the container:
   ```bash
   kubectl exec -it <pod-name> -n <namespace> -- df -h
   kubectl exec -it <pod-name> -n <namespace> -- du -sh /app/logs/* 2>/dev/null | sort -rh | head -10
   ```
2. Check log write rate and identify which log file is growing:
   ```bash
   kubectl exec -it <pod-name> -n <namespace> -- ls -lhtr /app/logs/
   ```
3. Check whether the log shipper (Fluentd/Filebeat) sidecar or daemonset is healthy:
   ```bash
   kubectl get pods -n logging
   kubectl logs -n logging <fluentd-pod> --tail=50
   ```
4. Check if debug logging was recently enabled:
   ```bash
   kubectl get configmap <app-config> -n <namespace> -o yaml | grep -i "log.level\|LOG_LEVEL"
   ```
5. Look for log storms in the current logs:
   ```bash
   kubectl logs <pod-name> -n <namespace> --tail=100 | sort | uniq -c | sort -rn | head -20
   ```

## Resolution Steps
1. **Immediate — clear old log files inside the container:**
   ```bash
   kubectl exec -it <pod-name> -n <namespace> -- bash -c "find /app/logs -name '*.log.*' -mtime +1 -delete"
   kubectl exec -it <pod-name> -n <namespace> -- bash -c "truncate -s 0 /app/logs/service.log"
   ```
   Only truncate if you have confirmed the log shipper has already processed the file.
2. **If debug logging was left on:** Revert the log level to info/warn via configmap and restart the pod:
   ```bash
   kubectl set env deployment/<deployment-name> -n <namespace> LOG_LEVEL=info
   ```
3. **If log shipper is behind or down:** Restart the log shipper and monitor it catches up before clearing logs.
4. **If log storm from error loop:** Address the underlying error (check [compute-crashloop.md](compute-crashloop.md) or [api-error-rate-spike.md](api-error-rate-spike.md)). Add a rate limit on error logging as a short-term mitigation.
5. **Long-term:** Configure log rotation in the application (e.g., `logrotate`, Python `RotatingFileHandler`) and size the log volume appropriately for peak write rate × retention period.

## Escalation Criteria
Escalate to the on-call engineer if:
- Log volume is full and the application has crashed — data may be at risk
- The log shipper being down has caused a gap in audit logs that must be preserved
- The issue recurs within hours of clearing, indicating the root cause (log storm or rotation failure) is not resolved

## Ownership

| Layer | Team | Contact |
|---|---|---|
| Application log behavior, log rotation config | **Application Team** | See service `CODEOWNERS` · Slack: `#team-<service>` |
| Log shipping infrastructure (Fluentd/Filebeat daemonsets) | **Platform Engineering** | Slack: `#platform-oncall` · PD: `platform-engineering` |

**Boundary:** Application teams own log volume sizing requests and their application's logging configuration (level, rotation). Platform Engineering owns the log shipping infrastructure that drains the logs off the container. If the log shipper (Fluentd/Filebeat) is the bottleneck, that is a Platform Engineering issue. If the application is generating too many logs, that is the application team's issue to fix.

## Related Runbooks
- [storage-disk-pressure.md](storage-disk-pressure.md) — node-level disk pressure from logs on stdout
- [api-error-rate-spike.md](api-error-rate-spike.md) — error storms are a common log volume trigger

## Tags
`family: storage`
`severity: medium`
`services: any`
