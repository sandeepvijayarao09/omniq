---
title: Design Doc — Anomaly Detection v2
category: engineering
---
# Anomaly detection v2

Authors: Lena Hoffmann, Kenji Watanabe. Status: beta with 40 customers.

## Problem

v1 used static seasonal baselines and produced too many false positives: 38% of v1 anomaly incidents were closed as noise in Q2.

## Approach

A per-series model combining robust seasonal decomposition with a lightweight gradient-boosted residual model. Series are grouped by service, so one deploy that shifts 200 metrics opens one incident, not 200.

## Results in beta

- Noise rate down from 38% to 11%.
- Median time-to-detect for real regressions: 2.4 minutes, versus 7 minutes for threshold rules.
- Compute cost: roughly $0.004 per series per day.

## Open questions

Cold start for new services (first 7 days), and explaining why an anomaly fired. Explanations feed into Project Nimbus.
