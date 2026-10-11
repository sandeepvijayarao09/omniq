# 2-minute video script: Omniq Atlas (self-improvement · MongoDB · Raindrop)

Before recording: `.venv/bin/oa demo-reset`, restart with `OA_AUTO_EVOLVE_MINUTES=0 OA_CYCLES=off .venv/bin/oa serve`,
hard-refresh, browser full-screen, mic on. About 300 spoken words. Say "MongoDB" and "Raindrop" out loud at every step.
The story is one loop: **work → signals → Raindrop finds the failure → Atlas tests a change → kept or undone → next answer.**

| Time | On screen | Say |
|---|---|---|
| **0:00–0:10** | `/workflow`: agent self-improvement flowchart | "These are agents that improve themselves every week from how people actually work. MongoDB Atlas is their memory and their proof. Raindrop tells them where they're failing. Here's the loop, live." |
| **0:10–0:30** | Sign in as **Diego (Developer)**. Ask `@release_agent can I ship on friday afternoon`, rephrase `can I deploy on a Friday afternoon?`, then `Now also write the rollback checklist for that deploy` | "Diego asks his Release agent. The answer is grounded by Atlas Vector Search with Voyage embeddings, and IAM is compiled into the `$vectorSearch` filter. He rephrases: that's a miss. Then he builds on it: classified in the background as an *extension*. No feedback forms. Every answer is a Raindrop event, and every rephrase, correction and copy is a Raindrop signal." |
| **0:30–0:50** | 🤖 My agents → **Release Agent** → **🌧 Where it fails** | "Every agent reads that back through the Raindrop Query API: which answers fail, by failure type, and on which playbook version. This is where the Release agent is struggling right now. A failure spike in Raindrop triggers an improvement cycle immediately, without waiting for the 7-day cycle." |
| **0:50–1:15** | 📈 Learning → **Improve from last 7 days ▶** (let the steps stream in) | "Every person and every agent runs a 7-day cycle. Watch it run: MongoDB change streams push each step to the screen. It builds the week's decision ledger from Atlas, pulls Raindrop failures, attributes each one to retrieval or reasoning, and proposes one change to the playbook: rules, retrieval, workflow or template. Then it has to win on unseen test cases and a blind side-by-side replay." |
| **1:15–1:35** | Result of the cycle (kept / undone sentence), then the flowchart's gate column | "A change is only kept if the data proves it. It must pass every gate and an absolute leak test, and the pattern must come from two or more people. Bigger changes wait for a manager. Otherwise it's undone, and that's recorded as a decision too. After release, a watchdog compares Raindrop failure rates per version and auto-rolls back if the new one is worse." |
| **1:35–1:50** | ⋯ Under the hood → **🗄 Data layer**, then an agent's version timeline | "MongoDB Atlas holds all of it: every playbook and persona version with its parent and evidence, context layers, memory, `$rankFusion` hybrid search, a `$graphLookup` ontology, TTL locks so two cycles never collide, and a hash-chained audit log." |
| **1:50–2:00** | 📊 **Impact** | "The result: 26 automatic decisions, 6 kept, 12 undone, 4 refused, plus 38 points on kept changes, zero leaks, 78% right the first time. Agents that get better every week, and prove it in MongoDB." |

## Backup lines if something is slow
- A model answer is slow: "This runs on a local Gemma model, so private data never leaves the machine."
- The improvement cycle is still running at 1:15: go to the Data layer, then come back for the result before Impact.
