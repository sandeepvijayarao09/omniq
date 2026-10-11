---
title: ADR-007 — Move session storage from Redis to Postgres
category: engineering
---
# ADR-007: Move session storage from Redis to Postgres

Status: Accepted. Author: Maya Chen. Deciders: Platform team, reviewed by Priya Raman.

## Context

User sessions for the Pulse web app live in a single Redis cluster. During the August incident, a Redis failover evicted all sessions and logged out every customer, which turned a degraded-ingest incident into a full outage from the customer's point of view.

## Decision

Store sessions in the primary Postgres cluster in a dedicated `sessions` table with a TTL index, and keep Redis only as a read-through cache. Postgres is already replicated across three zones with automated failover and point-in-time recovery.

## Consequences

- Session reads add about 2 ms at p50 on a cache miss; acceptable.
- Failover no longer logs customers out.
- Migrations touching `sessions` must stay backward compatible for one release so rollback stays schema-free.
- Redis can be resized down, saving an estimated $3,200 per month.
