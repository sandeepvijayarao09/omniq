---
title: ADR-015 — Feature flags and progressive delivery
category: engineering
---
# ADR-015: Feature flags and progressive delivery

Status: Accepted. Author: Kwame Asante.

## Decision

All user-facing changes ship behind a flag in our flag service. Flags have an owner, a creation date and an expiry. A flag older than 90 days pages its owner in the weekly flag review.

## Rollout stages

1. Internal (Halcyon's own Pulse tenant).
2. Beta tenants (opt-in list maintained by Product).
3. Percentage rollout: 10%, 50%, 100% of tenants, at least 24 hours per step.

## Rules

- Kill switches for every flag that touches ingest or alerting.
- Never use flags for authorization decisions; use roles.
- Remove the flag and the dead code within two weeks of reaching 100%.
