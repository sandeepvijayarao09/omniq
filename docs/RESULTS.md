# Results (generated from MongoDB Atlas)

_Generated 2026-10-11 22:52 UTC by `scripts/results.py`. Every number below is a query over the live data._

## Data-driven decisions

| | |
|---|---|
| Automatic decisions about playbooks | **25** |
| Kept (proven better on unseen tests / blind replay) | **5** |
| Undone (not better: that is a decision too) | **12** |
| Refused by safety rules | **4** |
| Waiting for a manager's approval | **1** |
| Average score gain of kept changes | **+34 pts** |
| Privacy leaks in any kept version | **0** |
| Answers right the first time, last 7 days (no rephrase, correction, regenerate or 👎) | **76%** of 84 |

## First-time-right by agent (last 7 days)

| Agent | Answers | Right first time |
|---|---|---|
| General assistant | 31 | 90% |
| Release Agent | 13 | 77% |
| Debug Agent | 7 | 43% |
| Verifier Agent | 6 | 67% |
| Build Agent | 6 | 100% |
| General assistant | 5 | 20% |
| General assistant | 5 | 60% |
| Board Brief Agent | 2 | 100% |
| General assistant | 2 | 100% |
| General assistant | 2 | 100% |

## Scores over time (practice-question pass rate)

| Playbook | Start | Now |
|---|---|---|
| executive | 0% | 100% |
| manager | 50% | 75% |
| pro | 75% | 88% |
| Code Review Agent | 67% | 67% |
| Debug Agent | 100% | 100% |
| People Agent | 50% | 50% |
| Release Agent | 100% | 100% |
| Risk Agent | 50% | 50% |
| Verifier Agent | 100% | – (not re-scored yet) |

## What it learned from (signals, never asked for)

| Signal | Count |
|---|---|
| Answers given | 84 |
| 👍/👎 ratings | 22 |
| followup_correction | 3 |
| copied | 3 |
| rephrased | 3 |
| follow-up understood as extension | 3 |
| follow-up understood as refinement | 1 |
| follow-up understood as correction | 1 |
| follow-up understood as new_task | 1 |
| 7-day person cycles run | 11 (reviewing 128 decisions) |
| Agent 7-day cycles run | 5 |
| Events stored in Raindrop | 77 |
| Graded test cases | 84 |

## Safety

- Leak test cases across roles and agents: all pass on every kept version (absolute safety gate).
- Suggestions quarantined as injection attempts: 0.
- Hash-chained audit log: 415 events, verified end to end.
- End-to-end suite `scripts/e2e_live.py`: **29/29** through the public Modelence URL.
