---
title: Postmortem — August 2026 Ingest Outage
category: engineering
---
# Postmortem: August 14 ingest outage

Severity: SEV1. Duration: 2 hours 41 minutes. Incident commander: Priya Raman.

## Summary

A configuration change raised the ingest gateway's connection limit beyond what the Redis session cluster could handle. Redis failed over, all web sessions were evicted, and the ingest backlog grew until alerting itself was delayed.

## Customer impact

Globex Financial and Stark Logistics, our two largest accounts, lost real-time alerting for the full window. We issued $42,000 in service credits across affected customers.

## Root causes

1. Connection-limit changes were not load tested.
2. Sessions depended on a single Redis cluster (addressed by ADR-007).
3. Alerting ran on the same ingest path it monitors.

## Action items

- Load-test gate for gateway config changes (owner: Maya Chen, done).
- Move sessions to Postgres per ADR-007 (owner: Platform, in progress).
- Out-of-band alerting path (owner: Leo Martins, due end of Q4).
