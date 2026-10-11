---
title: Security Incident Response Runbook
category: engineering
---
# Security incident response runbook

## Declare

Anyone can declare a security incident in the incident tool with type "security". This pages the Security on-call (Rebecca Stein, Viktor Sokolov or Aaliyah Johnson) and opens a private channel.

## First hour

1. Security on-call becomes incident commander.
2. Contain: revoke credentials, isolate hosts or disable the affected feature flag.
3. Preserve evidence: snapshot affected volumes before changes; don't wipe anything.
4. Start the timeline in the incident doc; write down every action with a timestamp.

## Customer data

If customer data may be involved, notify Legal (Adrienne Castillo) within one hour. Legal decides on customer and regulator notification; GDPR requires notifying regulators within 72 hours of becoming aware of a personal data breach.

## Afterwards

Blameless postmortem within five business days, with action items tracked in the security backlog.
