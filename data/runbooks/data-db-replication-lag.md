# Database Replication Lag — Read Replica Lag Exceeds Threshold

## Overview
Replication lag is the delay between a write being committed on the primary database and that write being visible on a read replica. Services that route reads to replicas (for load distribution) will see stale data when lag is high. For most OLTP workloads, acceptable lag is under 1 second. When lag exceeds seconds to minutes, applications that rely on read-after-write consistency may behave incorrectly — showing users their own writes as missing, or computing incorrect aggregates. Extreme lag (tens of minutes) puts the replica at risk of diverging and requiring a full resync.

## Alert Signatures
- `PostgresReplicationLagHigh` — `pg_replication_lag > 30` (seconds)
- `pg_stat_replication.write_lag` or `replay_lag` elevated
- Alertmanager: `[HIGH] Replication lag on replica <host> > 30s`
- Application errors: stale read after write (symptom, not alert)

## Common Causes
- Heavy write load on the primary — replica cannot apply WAL fast enough
- Long-running query on the replica blocking WAL replay (query conflict with replication)
- Replica is resource-constrained — CPU or I/O can't keep up with replay
- Network bandwidth between primary and replica is saturated (cross-region replication)
- A large DDL operation (index creation, ALTER TABLE) on the primary causing a large WAL burst
- Replica host is under disk pressure — slow WAL write speed
- Hot standby feedback is enabled and a long-running read query is preventing vacuum on the primary, causing lag to grow

## Diagnostic Steps
1. Check current replication lag from the primary:
   ```sql
   -- On the primary
   SELECT client_addr, state, sent_lsn, write_lsn, flush_lsn, replay_lsn,
          write_lag, flush_lag, replay_lag
   FROM pg_stat_replication;
   ```
2. Check lag from the replica's perspective:
   ```sql
   -- On the replica
   SELECT now() - pg_last_xact_replay_timestamp() AS replication_lag;
   SELECT pg_is_in_recovery(), pg_last_wal_receive_lsn(), pg_last_wal_replay_lsn();
   ```
3. Check for blocking queries on the replica:
   ```sql
   -- On the replica
   SELECT pid, query, state, wait_event_type, wait_event, now() - query_start AS duration
   FROM pg_stat_activity
   WHERE state != 'idle'
   ORDER BY duration DESC;
   ```
4. Check replica CPU and I/O utilization:
   ```bash
   # On the replica host or via cloud provider monitoring
   iostat -x 1 5
   top -bn1 | head -20
   ```
5. Check primary write rate to understand if lag is write-volume-driven:
   ```sql
   SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) AS lag_bytes
   FROM pg_stat_replication;
   ```
6. Check for large WAL events (DDL operations):
   ```bash
   # On primary
   tail -100 /var/log/postgresql/postgresql.log | grep -E "ALTER TABLE|CREATE INDEX|autovacuum"
   ```

## Resolution Steps
1. **If a blocking query on the replica is holding up WAL replay:** Terminate it:
   ```sql
   -- On the replica
   SELECT pg_terminate_backend(<pid>);
   ```
   Consider setting `max_standby_streaming_delay` and `max_standby_archive_delay` to limit how long queries can block replay.
2. **If replica is resource-constrained:** Scale up the replica instance (CPU/memory/IOPS). If running in Kubernetes, increase resource limits for the replica pod.
3. **If primary write load is too high:** Review write hot spots and consider partitioning, batching writes, or adding more replicas to distribute read traffic (temporarily reducing per-replica write replay load).
4. **If lag is caused by a large DDL on primary:** Wait for the DDL to complete — lag will catch up. Monitor progress. Use `pg_stat_progress_create_index` or `pg_stat_progress_alter_table` to track the DDL.
5. **Short-term mitigation — route reads back to primary:** Update application config to route read traffic to the primary while the replica catches up. Monitor primary CPU and connection pool capacity.

## Escalation Criteria
Escalate to the on-call engineer if:
- Lag is growing faster than it is recovering (replica is falling further behind)
- Lag exceeds the replica's WAL retention window — replica will need to be resynced from scratch
- The replica is in a `disconnected` state (replication stream has been cut)
- Routing reads to the primary is not viable (primary is already at capacity)

## Ownership

| Layer | Team | Contact |
|---|---|---|
| Replication topology, replica provisioning, WAL config | **Database Reliability Engineering** | Slack: `#db-oncall` · PD: `database-reliability` |
| Application read routing (which replica to use) | **Application Team** | See service `CODEOWNERS` · Slack: `#team-<service>` |
| Replica host hardware or instance | **Hardware & Datacenter Ops** (bare-metal) or cloud provider | Slack: `#dc-ops-oncall` or provider support via DRE |

**Boundary:** Database Reliability Engineering owns the replication topology and is the decision authority for routing reads back to the primary. Application teams own which replica endpoint they connect to — switching from replica to primary must be coordinated with DRE to avoid overloading the primary. Do not scale up the replica instance or modify replication configuration without DRE involvement.

## Related Runbooks
- [data-db-connection-pool.md](data-db-connection-pool.md) — routing reads back to primary may exhaust primary connection pool
- [api-latency-spike.md](api-latency-spike.md) — high lag causing stale reads can manifest as latency on read-heavy endpoints

## Tags
`family: data`
`severity: high`
`services: postgresql, mysql, database, replication`
