---
title: Q3 OpEx Variance Report
category: finance
---
# Q3 operating expense variance

Q3 OpEx came in at $4.48M against a plan of $4.0M, 12% over budget.

## Drivers

1. Cloud infrastructure: $310,000 over plan. Over-provisioned ingest capacity during and after the August incident, plus a duplicated Redis cluster that ADR-007 will retire.
2. Service credits: $42,000 issued after the August outage, booked as contra-revenue but tracked here for visibility.
3. Contractors: $95,000 for the out-of-band alerting project and the SOC 2 readiness assessment.
4. Recruiting fees: $33,000 above plan for the SRE and senior backend searches.

## Actions

Cloud spend reduction target of $120,000 per quarter starting Q4 (rightsizing plus Redis downsizing). Recruiting to switch to in-house sourcing for the remaining roles.

## Vendor payment details

Primary cloud vendor invoices are paid from the operating account, IBAN GB82 WEST 1234 5698 7654 32. The corporate card ending program uses card 4111 1111 1111 1111 for marketplace purchases; rotate it after the SOC 2 audit.
