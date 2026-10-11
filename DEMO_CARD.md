# Demo card (3 minutes): keep this open next to the app

**Setup (1 min before):** `.venv/bin/oa demo-reset`, then `OA_AUTO_EVOLVE_MINUTES=0 OA_CYCLES=off .venv/bin/oa serve`.
Open http://127.0.0.1:8000 and hard-refresh (Cmd+Shift+R). Mac awake, Ollama open.

| # | Time | Do | Say |
|---|---|---|---|
| 1 | 0:00 | Org chart on screen | "Every person gets a team of agents that makes them 10×, and the team gets better every week by watching how they work. All on MongoDB Atlas." |
| 2 | 0:15 | Click **Diego Ferreira** → type `@ver` → pick **@verifier_agent** → *Verify: ship the gateway connection-limit change on Friday at 3pm straight to 100% of traffic.* | (while it streams) "His Verifier checks plans against our runbooks: **FAIL**, deploys are frozen Friday after 14:00." |
| 3 | 0:40 | **Switch person ▾ → Marcus (CTO)** → home suggestion *What happened in the August 14 outage…* | "Same company, different person: the CTO gets the board-level version. ⓘ context shows the layers: role file → his persona." |
| 4 | 1:05 | As Diego or Maya: ask something, then *now also write the rollback checklist* | "Nothing pops up. In the background it understood that follow-up as an *extension*: people want more on top of this work. That's how agents learn." |
| 5 | 1:25 | **🤖 My agents → Release Agent → 🌧 Where it fails** | "Raindrop tells each agent where it fails: rephrasings, corrections, by version." |
| 6 | 1:40 | As **Marcus**: **📈 Learning** → playbook **CTO: Security Agent** → **Improve from last 7 days ▶** | Narrate the boxes lighting up: "signals → Raindrop → evidence → proposed change → gates → proof on unseen tests → keep or undo → saved in Atlas." |
| 7 | 2:15 | Scroll the versions → **Developer: Verifier Agent** → v3 **needs approval** → **Approve** | "It proved a better template from 2 people's patterns, but template changes need a manager. IAM decides who that is." |
| 8 | 2:35 | **📊 Impact** | "25 decisions, 5 kept, 12 undone, +34 points, 0 leaks, 78% right the first time. Nothing changes on opinion." |
| 9 | 2:50 | **⋯ Under the hood → 🔐 Access** → simulator `docs:read` / `doc:category/board` as Maya | "And it can't unlock itself: IAM compiles into the vector search filter. MongoDB enforces it." |

**If something stalls:** the model is local, so wait about 5 s. If the public link drops, use localhost; same app, same data.
**Likely questions:** "Is it really 10×?" → "10× is the goal; what we measure is first-time-right and every decision, kept or undone, in Atlas."
"Could it make itself unsafe?" → "Locked invariants, an absolute leak gate, quarantined injection, manager approval for big changes, and a watchdog that rolls back."
