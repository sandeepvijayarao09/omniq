---
title: ADR-012 — Kafka for the ingest pipeline
category: engineering
---
# ADR-012: Replace the bespoke ingest queue with Kafka

Status: Accepted, migration 70% complete. Author: Amara Nwosu. Reviewers: Tobias Lindqvist, Mei Lin Zhou.

## Context

The in-house ingest queue cannot replay data after a downstream failure, and its backpressure is all-or-nothing. During the August incident, a stalled consumer forced us to drop data for two tenants.

## Decision

Use managed Kafka with one topic per signal type, partitioned by tenant ID. Consumers commit offsets after a successful write, so failures replay instead of dropping. Per-tenant quotas move from the gateway into Kafka client quotas.

## Consequences

- Seven-day replay window for every signal.
- About $18,000 per month in additional managed-Kafka cost, offset by retiring three queue clusters.
- Ingest team owns the Kafka configuration; SRE owns capacity alerts.
- Cutover is per tenant behind a feature flag (ADR-015); the last tenants move in November.
