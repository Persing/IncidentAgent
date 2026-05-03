# ELT Pipeline Failure — Batch Pipeline Stale or Failed

## Overview
An ELT (Extract, Load, Transform) pipeline failure means a scheduled data job has not completed successfully, leaving downstream tables or datasets stale. Depending on the pipeline's role, this can mean dashboards showing outdated metrics, ML models trained on stale features, billing or compliance data not being updated, or SLO calculations using old data. The blast radius depends on who consumes the pipeline output. Silent failures (job completes but produces wrong data) are more dangerous than loud failures (job errors and alerts).

## Alert Signatures
- `PipelineJobFailed` — job exit code non-zero, or Airflow/Prefect task in FAILED state
- `PipelineDataFreshness` — `time() - max(pipeline_last_success_timestamp) > <threshold>`
- `pipeline_rows_processed` drops to zero when it should be non-zero
- Airflow/Prefect/Dagster: DAG/flow shows red in the UI
- Alertmanager: `[HIGH] ELT pipeline <pipeline-name> has not succeeded in > <N> hours`

## Common Causes
- Upstream data source changed schema or returned unexpected data format
- Database connection failure during extract or load phase
- Transformation SQL query failing due to data quality issue (null values, type mismatch, duplicate keys)
- Resource exhaustion: job ran out of memory or disk during a large batch
- Scheduler (Airflow, Prefect) itself is unhealthy — jobs are not being triggered
- Dependency DAG failed upstream, causing this DAG to be skipped or blocked
- Cloud storage permission change preventing the job from reading source data
- Timeout on a slow query — query plan regressed after a data volume increase

## Diagnostic Steps
1. Check the job's most recent run status and logs:
   ```bash
   # Kubernetes job
   kubectl get jobs -n <namespace>
   kubectl describe job <job-name> -n <namespace>
   kubectl logs -n <namespace> -l job-name=<job-name> --tail=200
   ```
   For Airflow: check the task log in the Airflow UI for the failed task instance.
2. Find the exact failure point — which stage failed (extract, load, transform)?
3. Check data freshness for the output table:
   ```sql
   SELECT MAX(updated_at) FROM <output_table>;
   -- Compare to expected freshness SLA
   ```
4. Check if upstream schema changed:
   ```bash
   # Compare current source schema to what the pipeline expects
   # Check source system changelog or recent migrations
   ```
5. Check for data quality issues in the source:
   ```sql
   SELECT COUNT(*) FROM <source_table> WHERE <key_column> IS NULL;
   SELECT COUNT(*) FROM <source_table> GROUP BY <key_column> HAVING COUNT(*) > 1;
   ```
6. Check if the scheduler is healthy:
   ```bash
   kubectl get pods -n airflow   # or prefect, dagster namespace
   kubectl logs -n airflow -l component=scheduler --tail=100
   ```
7. Check cloud storage access:
   ```bash
   # GCS example
   gsutil ls gs://<bucket>/<path>
   # S3 example
   aws s3 ls s3://<bucket>/<path>
   ```

## Resolution Steps
1. **If schema change:** Update the pipeline's schema definition to match the new source. Validate against historical data before re-running.
2. **If data quality issue:** Add a data validation step (assertion) at the extract stage. For the immediate incident, either filter bad records or fail fast with a clear error message. Do not silently skip bad data.
3. **If OOM on large batch:** Increase the job's memory limit, add pagination/chunking to the extract phase, or partition the job by date range.
4. **If scheduler is unhealthy:** Restart the scheduler pod and verify DAG triggers resume:
   ```bash
   kubectl rollout restart deployment/airflow-scheduler -n airflow
   ```
5. **If upstream dependency failed:** Resolve the upstream DAG failure first, then trigger this pipeline manually:
   ```bash
   # Airflow CLI
   airflow dags trigger <dag-id> --run-id manual_recovery_$(date +%Y%m%d)
   ```
6. **If permission issue:** Restore the required IAM permission or service account binding. Rerun the job after confirming access.

## Escalation Criteria
Escalate to the on-call engineer if:
- The pipeline has been failing for longer than its SLA window and downstream consumers are impacted
- Data was written incorrectly (wrong data in the output table) — requires investigation before re-running
- The scheduler is down, and you do not have access to restart it
- A schema change broke multiple pipelines simultaneously

## Ownership

| Layer                                               | Team                                       | Contact                                             |
|-----------------------------------------------------|--------------------------------------------|-----------------------------------------------------|
| Pipeline code, DAG definitions, scheduling config   | **Data Engineering**                       | Slack: `#data-oncall` · PD: `data-engineering`      |
| Scheduler infrastructure (Airflow, Prefect cluster) | **Data Engineering**                       | Slack: `#data-oncall` · PD: `data-engineering`      |
| Source system schema or data changes                | **Application Team** (source system owner) | See service `CODEOWNERS` · Slack: `#team-<service>` |
| Destination database / data warehouse               | **Database Reliability Engineering**       | Slack: `#db-oncall` · PD: `database-reliability`    |

**Boundary:** Data Engineering owns the pipeline code and scheduler. If the failure is caused by an upstream source system changing schema or emitting bad data, that is the source team's responsibility — Data Engineering coordinates the fix but cannot unilaterally modify the source. If the destination database is the failure point (connection refused, schema mismatch at the load layer), loop in Database Reliability Engineering. Data Engineering should not run manual queries in production databases without DRE oversight.

## Related Runbooks
- [data-kafka-consumer-lag.md](data-kafka-consumer-lag.md) — streaming pipelines feeding this ELT may be behind
- [data-db-connection-pool.md](data-db-connection-pool.md) — DB connection issues during load phase
- [data-db-replication-lag.md](data-db-replication-lag.md) — pipeline may be reading from a lagged replica

## Tags
`family: data`
`severity: high`
`services: airflow, prefect, dagster, data-pipeline`
