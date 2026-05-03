# Kafka Consumer Lag — Consumer Group Falling Behind

## Overview
Kafka consumer lag measures how many messages a consumer group has not yet processed relative to the latest offset in a partition. A lag alert means producers are writing faster than consumers are reading, and unprocessed messages are accumulating. Sustained lag degrades data freshness (pipelines see stale data), can cause downstream timeouts that assume near-real-time delivery, and risks data loss if retention policies expire messages before they're consumed. Lag is one of the most common data pipeline alerts and has a wide range of causes from consumer slowness to partition imbalance.

## Alert Signatures
- `KafkaConsumerGroupLag` — `kafka_consumer_group_lag > <threshold>` (threshold varies by topic SLA)
- `kafka_consumer_group_lag_sum` growing monotonically
- Dashboard: lag chart showing divergence between latest offset and current offset
- Alertmanager: `[HIGH] Consumer group <group-id> lag on topic <topic> > <N>`

## Common Causes
- Consumer pod is slow: downstream database or API it writes to is the bottleneck
- Consumer pod crashed or stopped — partition is unassigned or assigned to a dead consumer
- Consumer group rebalance in progress — all consumers stop processing during rebalance
- Insufficient consumer instances relative to partition count (parallelism is capped by partitions)
- Message payload size increased dramatically — processing time per message increased
- Consumer is stuck on a poison pill message — retrying indefinitely on a bad record
- Producer throughput spike without corresponding consumer capacity increase
- Topic retention period is short and lag is at risk of passing the retention boundary

## Diagnostic Steps
1. Check current lag per partition and consumer group:
   ```bash
   kafka-consumer-groups.sh --bootstrap-server <broker>:<port> \
     --group <group-id> --describe
   ```
   Look at `LAG` column per partition. Is lag evenly distributed or concentrated on specific partitions?
2. Check consumer pod health:
   ```bash
   kubectl get pods -n <namespace> -l <consumer-app-label>
   kubectl logs <consumer-pod> -n <namespace> --tail=200
   ```
   Look for error loops, slow processing messages, or rebalance events.
3. Check consumer processing rate vs. producer rate:
   ```bash
   # In Prometheus / Grafana:
   # rate(kafka_consumer_records_consumed_total[5m])  -- consumer rate
   # rate(kafka_topic_partitions_messages_in_per_sec[5m])  -- producer rate
   ```
4. Check for a poison pill message (consumer logging the same offset repeatedly):
   ```bash
   kubectl logs <consumer-pod> -n <namespace> | grep -E "offset|retry|error" | tail -50
   ```
5. Check the number of consumer instances vs. partition count:
   ```bash
   kafka-consumer-groups.sh --bootstrap-server <broker>:<port> --group <group-id> --describe | grep CONSUMER-ID
   # Count CONSUMER-IDs vs. partition count
   ```
6. Check if retention period is at risk:
   ```bash
   kafka-configs.sh --bootstrap-server <broker>:<port> --entity-type topics --entity-name <topic> --describe | grep retention
   ```

## Resolution Steps
1. **If consumer pod is crashed:** Restart it and monitor lag recovery:
   ```bash
   kubectl rollout restart deployment/<consumer-deployment> -n <namespace>
   ```
2. **If consumer group rebalance is in progress:** Wait for rebalance to complete (monitor lag; it should stabilize then decrease). If rebalance loops, check for consumer instances repeatedly crashing and triggering rebalance.
3. **If insufficient consumer parallelism:** Scale the consumer deployment up to the number of partitions (max useful parallelism):
   ```bash
   kubectl scale deployment <consumer-deployment> -n <namespace> --replicas=<partition-count>
   ```
4. **If poison pill message:** Skip the bad offset manually (requires consumer group reset — do carefully):
   ```bash
   kafka-consumer-groups.sh --bootstrap-server <broker>:<port> \
     --group <group-id> --topic <topic>:<partition> \
     --reset-offsets --to-offset <next-good-offset> --execute
   ```
   Archive the bad message before skipping for forensics.
5. **If downstream is the bottleneck:** Optimize the downstream write path, add backpressure, or increase downstream capacity.
6. **If retention at risk:** Immediately increase partition count or consumer replicas to reduce lag before messages expire.

## Escalation Criteria
Escalate to the on-call engineer if:
- Lag is growing at a rate that will exhaust message retention within 2 hours
- Messages are already being lost (lag exceeds retention boundary for any partition)
- Consumer group reset is required — this is an operational action with data-loss risk if done incorrectly
- The Kafka broker itself is unhealthy (check broker metrics and ZooKeeper/KRaft status)

## Ownership

| Layer | Team | Contact |
|---|---|---|
| Kafka cluster health, broker configuration, topic creation | **Data Engineering** | Slack: `#data-oncall` · PD: `data-engineering` |
| Consumer application code and deployment | **Application Team** (consumer owner) | See service `CODEOWNERS` · Slack: `#team-<service>` |
| Consumer offset reset operations | **Data Engineering** (must approve and execute) | Slack: `#data-oncall` — do not reset offsets without their sign-off |

**Boundary:** Data Engineering owns the Kafka cluster and has authority over offset management. Application teams own their consumer code and scaling. **Critical:** Consumer group offset resets are a Data Engineering operation — application teams must not perform resets unilaterally, as incorrect resets can cause duplicate processing or message loss affecting other consumers of the same topic. If the consumer is the bottleneck, the application team fixes it. If the broker is the bottleneck, Data Engineering fixes it.

## Related Runbooks
- [data-elt-pipeline-failure.md](data-elt-pipeline-failure.md) — Kafka lag often indicates downstream pipeline issues
- [data-db-connection-pool.md](data-db-connection-pool.md) — consumer bottleneck is often the DB write path

## Tags
`family: data`
`severity: high`
`services: kafka, data-pipeline, streaming`
