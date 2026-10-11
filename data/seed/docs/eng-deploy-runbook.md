---
title: Production Deploy Runbook — Pulse
category: engineering
---
# Production deploy runbook

## Preconditions

- The change is merged to main and the CI pipeline is green, including the integration suite.
- No active SEV1/SEV2 incident. Check the #incidents channel.
- Deploys are frozen Friday after 14:00 and during the winter shutdown.

## Steps

1. Tag the release: `make release VERSION=x.y.z`.
2. Deploy to staging (staging ingest gateway is 10.20.4.17) and watch the canary dashboard for 15 minutes.
3. Promote with `pulsectl promote --env prod --strategy canary --step 10`. Step through 10%, 25%, 50%, 100%, waiting 10 minutes at each step.
4. If error rate rises above 0.5% or p99 ingest latency above 800 ms at any step, roll back.

## Rollback

`pulsectl rollback --env prod` restores the previous release in under two minutes. Database migrations must be backward compatible for one release (see ADR-007) so rollback never needs a schema change.

## On-call

Primary on-call this rotation is Maya Chen, +1 (415) 555-0142. Secondary is Priya Raman, +1 (415) 555-0178. Page through the incident tool rather than calling directly unless the tool is down.
