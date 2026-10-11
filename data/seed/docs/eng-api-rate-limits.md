---
title: Public API Rate Limiting Spec
category: engineering
---
# Public API rate limits

Limits are per API key with a token-bucket algorithm, enforced at the edge.

| Plan | Requests per minute | Burst |
|---|---|---|
| Team | 600 | 100 |
| Business | 3,000 | 500 |
| Enterprise | 10,000 (configurable) | 2,000 |

Responses include `X-RateLimit-Remaining` and `Retry-After`. A 429 never drops ingest data: telemetry goes through the ingest gateway, which has separate per-tenant quotas (ADR-012).

Customers can request higher limits through Support; increases above 2x the plan default need approval from the Platform team.
