# Team Directory — Incident Escalation Contacts

This document defines team ownership, scope boundaries, and contact information for incident escalation. Referenced by all runbooks in `data/runbooks/`.

> **Note:** Channel names and PagerDuty service IDs are placeholders. Update to match your organization's actual contacts.

---

## Platform Engineering

**Scope:** Kubernetes control plane, cluster health, node lifecycle, scheduling, autoscaler, ingress controllers, CoreDNS, service mesh (Istio/Linkerd), storage classes, RBAC, namespaces, resource quotas.

**Does NOT own:** Applications running inside pods, physical node hardware, cloud VPC/security groups, managed databases.

| Channel | Purpose |
|---|---|
| Slack `#platform-oncall` | Immediate escalation — on-call is paged here |
| Slack `#platform-engineering` | Non-urgent questions, follow-up |
| PagerDuty `platform-engineering` | Direct page |

**On-call rotation:** 24/7. Acknowledge SLA: 5 minutes (critical), 15 minutes (high).

---

## Network / Infrastructure

**Scope:** VPCs, subnets, security groups, firewall rules, load balancers (external), BGP/routing, cross-region network paths, VPN tunnels, DNS infrastructure (authoritative DNS, not CoreDNS).

**Does NOT own:** Kubernetes networking (CNI, kube-proxy, CoreDNS) — that is Platform Engineering.

| Channel | Purpose |
|---|---|
| Slack `#network-oncall` | Immediate escalation |
| Slack `#network-infra` | Non-urgent |
| PagerDuty `network-infrastructure` | Direct page |

**On-call rotation:** 24/7. Acknowledge SLA: 10 minutes (critical), 30 minutes (high).

---

## Hardware & Datacenter Ops

**Scope:** Physical server health, GPU hardware, cooling systems, rack operations, OOB management (IPMI/iDRAC), hardware failure replacement, datacenter network switches.

**Does NOT own:** Software running on nodes, Kubernetes, cloud-hosted instances (for cloud instances, use cloud provider support).

| Channel | Purpose |
|---|---|
| Slack `#dc-ops-oncall` | Immediate escalation for hardware emergencies |
| Slack `#dc-ops` | Non-urgent hardware requests |
| PagerDuty `datacenter-ops` | Direct page |
| OOB Ticket Portal | For non-emergency hardware replacement requests |

**On-call rotation:** 24/7 for critical hardware failures. Business hours for non-critical.

---

## Database Reliability Engineering (DRE)

**Scope:** Managed database clusters (PostgreSQL, MySQL), replication topology, backup/restore operations, connection pooling infrastructure (PgBouncer), database performance, schema migrations in production.

**Does NOT own:** Application-level queries or ORM usage — those belong to the application team. Connection pool *configuration* inside the application is the application team's responsibility.

| Channel | Purpose |
|---|---|
| Slack `#db-oncall` | Immediate escalation |
| Slack `#db-reliability` | Non-urgent, performance questions |
| PagerDuty `database-reliability` | Direct page |

**On-call rotation:** 24/7. Acknowledge SLA: 5 minutes (critical), 15 minutes (high).

---

## Data Engineering

**Scope:** Kafka clusters, consumer group management, ELT/batch pipeline infrastructure (Airflow, Prefect, Dagster), streaming pipeline frameworks, data warehouse tables, data quality monitoring.

**Does NOT own:** Application-level Kafka consumers — those belong to the consuming application team.

| Channel | Purpose |
|---|---|
| Slack `#data-oncall` | Immediate escalation for pipeline/Kafka emergencies |
| Slack `#data-engineering` | Non-urgent, pipeline questions |
| PagerDuty `data-engineering` | Direct page |

**On-call rotation:** Business hours primary. After-hours on-call for critical pipelines only.

---

## ML Infrastructure

**Scope:** GPU serving infrastructure, vLLM/TGI/TensorRT-LLM deployment and configuration, model storage systems, inference autoscaling, model registry, GPU node pool management, inference platform APIs.

**Does NOT own:** Physical GPU hardware (Hardware & Datacenter Ops), model training pipelines (ML Platform/Research), application-layer LLM API clients.

| Channel | Purpose |
|---|---|
| Slack `#ml-infra-oncall` | Immediate escalation for inference outages |
| Slack `#ml-infrastructure` | Non-urgent, config questions |
| PagerDuty `ml-infrastructure` | Direct page |

**On-call rotation:** 24/7. Acknowledge SLA: 5 minutes (critical), 15 minutes (high).

---

## Security Engineering

**Scope:** TLS certificate lifecycle management, cert-manager configuration, internal CA operations, IAM/RBAC policies, secret management (Vault, Kubernetes Secrets), audit logging, security incident response.

**Does NOT own:** Application-level secret usage — application teams are responsible for how they consume secrets.

| Channel | Purpose |
|---|---|
| Slack `#security-oncall` | Immediate escalation for security incidents or cert emergencies |
| Slack `#security-engineering` | Non-urgent, policy questions |
| PagerDuty `security-engineering` | Direct page |

**On-call rotation:** 24/7 for security incidents. Business hours for certificate rotation requests.

---

## Application Teams

Each service has a designated owning team. Application teams own everything running inside their pods: application code, resource requests/limits, dependency configuration, connection pool settings, log output, and their service's own on-call rotation.

**To find the owning team for a service:**
- Check the service's `CODEOWNERS` file in the source repository
- Check the `team` label on the Kubernetes deployment: `kubectl get deployment <name> -n <ns> -o jsonpath='{.metadata.labels.team}'`
- Check the service catalog (internal wiki link)

---

## Escalation Path Principles

1. **Start with the team closest to the symptom.** If an application is returning 500s, start with the application team. Only loop in Platform Engineering if the issue turns out to be cluster-level.
2. **Boundary incidents:** When an issue crosses team boundaries (e.g., a pod fills disk affecting node health), the first-responder owns coordination — pull in the other team rather than handing off.
3. **Cloud provider escalation:** For cloud-hosted infrastructure failures (instance health events, managed service outages), the Platform Engineering or ML Infrastructure on-call opens the cloud provider support ticket. Do not open duplicate tickets.
4. **Bridge call threshold:** If two or more teams are simultaneously involved and the incident is SEV-1 or SEV-2, open an incident bridge call and page all relevant on-call staff.
