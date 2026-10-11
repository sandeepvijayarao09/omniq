---
title: PRD — Project Nimbus (AI incident summaries)
category: engineering
---
# PRD: Project Nimbus

Owner: Freya Nilsson. Engineering: Data & ML. Status: confidential; not announced.

## Problem

On-call engineers spend the first 10–20 minutes of an incident figuring out what changed. Pulse timelines show the signals, but people still have to read them.

## Proposal

Nimbus generates a plain-language summary at the top of each timeline: what broke, the likely cause, the blast radius and suggested next steps, with links to the evidence. It never takes actions.

## Guardrails

- Summaries only cite signals in the customer's own tenant.
- Customer telemetry is never used to train shared models.
- A thumbs up/down on every summary feeds quality reviews.

## Plan

Private beta with Globex Financial in Q1 (board-approved), then five more design partners. Pricing: an add-on for Business, included in Enterprise; the Marketing narrative is in progress.
