---
title: Pulse Architecture Overview
category: engineering
---
# Pulse architecture overview

## Request path

Customer agents and OpenTelemetry collectors send telemetry to the **ingest gateway** (Ingest team), which authenticates the tenant, applies quotas and writes to **Kafka** (ADR-012). Stream processors parse, enrich and route signals into three stores: **ClickHouse** for logs and traces, **a Prometheus-compatible TSDB** for metrics, and **object storage** for long-term retention.

## Product services

- **Timeline service** assembles incidents from signals, deploy events and config changes.
- **Detector service** runs threshold rules and anomaly detection v2 (Data & ML team).
- **API and web app** (Frontend team) serve the UI; the mobile apps use the same public API.
- **Auth and sessions** (Platform team): sessions in Postgres since ADR-007, Redis as a read-through cache.

## Infrastructure

Three availability zones in us-west-2, with an EU region planned. Everything is Terraform-managed by SRE; services run on Kubernetes with canary deploys through pulsectl. Feature flags are managed per ADR-015.

## Ownership

If you're unsure who owns something, check the Team Pages or ask in #eng-leads. Every service has an owning team listed in the service catalog.
