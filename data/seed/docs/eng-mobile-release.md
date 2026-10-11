---
title: Mobile Release Process
category: engineering
---
# Mobile release process

We ship iOS and Android on a two-week train. The branch cut is Monday of week 2 at 12:00 PT.

1. **Cut:** the release captain (rotates in the Mobile team) cuts `release/x.y` and freezes features.
2. **QA:** two days of regression testing on the device lab, plus a TestFlight and Play internal track build for the company.
3. **Staged rollout:** 10% for 24 hours, then 50%, then 100%. Halt if crash-free sessions drop below 99.5%.
4. **Hotfixes:** only for P1 bugs, approved by Jae-won Kim.

Push-alert changes need sign-off from SRE because they affect paging reliability for customers.
