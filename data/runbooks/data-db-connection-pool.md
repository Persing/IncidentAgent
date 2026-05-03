# Database Connection Pool Exhaustion

## Overview
Connection pool exhaustion occurs when all connections in the application's pool are in use and new requests cannot acquire a connection. Requests queue (or fail immediately with a timeout), causing latency spikes and 5xx errors. Database connections are expensive — each one consumes memory and a file descriptor on both the client and the database server. Pool exhaustion is almost always a symptom of something else: a slow query holding connections longer than expected, a traffic spike, a connection leak, or the database itself being slow.

## Alert Signatures
- `DBConnectionPoolExhausted` — custom metric: `db_pool_checked_out / db_pool_size > 0.95`
- Application errors: `timeout waiting for connection from pool`, `too many connections`, `FATAL: remaining connection slots are reserved`
- Postgres: `pg_stat_activity` showing `wait_event_type = 'Client'` with many idle connections
- `pg_stat_activity count > max_connections * 0.9`
- Alertmanager: `[HIGH] DB connection pool utilization > 95% for <service>`

## Common Causes
- Slow query holding a connection for an extended duration (lock contention, missing index, query regression)
- Connection leak — application acquiring connections and not releasing them (missing `finally` block, ORM session not closed)
- Sudden traffic spike with insufficient pool size configured
- Long-running transaction (possibly from a batch job or a transaction left open)
- Database server overloaded — queries are slow, connections pile up waiting
- A deployment that increased the number of application replicas without updating the pool size, overwhelming the DB's max_connections
- PgBouncer or other connection pooler is unhealthy or misconfigured

## Diagnostic Steps
1. Check current pool utilization:
   - Application metric: `db_pool_checked_out / db_pool_size`
   - Check the application's DB pool config (pool size, timeout, max overflow)
2. Check active connections on the database:
   ```sql
   -- PostgreSQL
   SELECT count(*), state, wait_event_type, wait_event
   FROM pg_stat_activity
   GROUP BY state, wait_event_type, wait_event
   ORDER BY count DESC;
   ```
3. Identify long-running queries:
   ```sql
   SELECT pid, now() - pg_stat_activity.query_start AS duration, query, state
   FROM pg_stat_activity
   WHERE (now() - pg_stat_activity.query_start) > interval '30 seconds'
   ORDER BY duration DESC;
   ```
4. Check for connection leaks — connections that are idle but not being returned to pool:
   ```sql
   SELECT pid, usename, application_name, client_addr, state, query_start, query
   FROM pg_stat_activity
   WHERE state = 'idle'
   ORDER BY query_start;
   ```
5. Check if PgBouncer (if in use) is healthy and check its pool stats:
   ```bash
   # Connect to PgBouncer admin console
   psql -p 6432 -U pgbouncer pgbouncer -c "SHOW POOLS;"
   psql -p 6432 -U pgbouncer pgbouncer -c "SHOW CLIENTS;"
   ```
6. Check application logs for connection acquisition timeout errors and timestamps to correlate with a deployment or traffic event.

## Resolution Steps
1. **Immediate — kill long-running queries holding connections:**
   ```sql
   SELECT pg_terminate_backend(<pid>);
   -- Terminate all queries running > 2 minutes (use carefully)
   SELECT pg_terminate_backend(pid)
   FROM pg_stat_activity
   WHERE (now() - query_start) > interval '2 minutes'
     AND state != 'idle'
     AND pid != pg_backend_pid();
   ```
2. **If pool size is too small for current replica count:** Increase pool size in application config or reduce the number of application replicas as a temporary measure. Ensure `pool_size * replicas < max_connections * 0.8` (leave headroom for admin connections).
3. **If PgBouncer is the bottleneck:** Increase PgBouncer's `max_client_conn` and `pool_size` in `pgbouncer.ini` and reload:
   ```bash
   pkill -HUP pgbouncer
   ```
4. **If connection leak:** Identify the leaking code path from the idle connection query (look at `application_name` or `client_addr`). Deploy a fix. Short-term mitigation: set a `idle_in_transaction_session_timeout` on the database:
   ```sql
   ALTER DATABASE <dbname> SET idle_in_transaction_session_timeout = '5min';
   ```
5. **If traffic spike:** Scale the application horizontally — but coordinate with DB capacity first.

## Escalation Criteria
Escalate to the on-call engineer if:
- Terminating long-running queries does not resolve pool exhaustion
- The database itself is the bottleneck (CPU or memory pressure on the DB server)
- Connections are growing unboundedly and the database is at risk of rejecting all connections
- The root cause is a code bug (connection leak) that requires a deployment to fix

## Ownership

| Layer                                                    | Team                                 | Contact                                             |
|----------------------------------------------------------|--------------------------------------|-----------------------------------------------------|
| Application connection pool config, connection lifecycle | **Application Team**                 | See service `CODEOWNERS` · Slack: `#team-<service>` |
| PgBouncer / connection pooler infrastructure             | **Database Reliability Engineering** | Slack: `#db-oncall` · PD: `database-reliability`    |
| Database server health, max_connections config           | **Database Reliability Engineering** | Slack: `#db-oncall` · PD: `database-reliability`    |

**Boundary:** Application teams own their pool configuration (`pool_size`, `max_overflow`, connection timeouts) and connection lifecycle in code. Database Reliability Engineering owns the database server and connection pooler infrastructure. Terminating long-running queries in production requires DRE sign-off unless you have explicit on-call authorization to do so — a query may be critical to another team's workflow. Changes to `max_connections` on the database server must go through DRE.

## Related Runbooks
- [api-latency-spike.md](api-latency-spike.md) — DB pool exhaustion is a top latency cause
- [data-kafka-consumer-lag.md](data-kafka-consumer-lag.md) — consumer-to-DB writes often exhaust pools under load
- [data-db-replication-lag.md](data-db-replication-lag.md) — replica lag can push load back to primary

## Tags
`family: data`
`severity: high`
`services: postgresql, mysql, database, connection-pool`
