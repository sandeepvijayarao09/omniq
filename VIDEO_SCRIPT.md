# 2-minute video script: Omniq Atlas

Before recording: `.venv/bin/oa demo-reset`, restart with `OA_AUTO_EVOLVE_MINUTES=0 OA_CYCLES=off .venv/bin/oa serve`,
hard-refresh, browser full-screen, mic on. About 290 spoken words at a calm pace. Every model answer streams in 1–3 s,
so start talking while it types.

| Time | On screen | Say |
|---|---|---|
| **0:00–0:12** | Org-chart sign-in (10 people) | "One person plus a team of self-improving agents is 10×. Omniq Atlas gives everyone in a company their own specialist agents, and those agents get better every week by watching how people work, without ever being able to unlock data they shouldn't see." |
| **0:12–0:30** | Sign in as **Diego (Developer)**. Type `@verifier_agent Verify: ship the gateway connection-limit change Friday 3pm straight to 100% of traffic` | "Diego is a developer with five agents: Build, Verifier, Debug, Release and Code Review. He hands the Verifier a job. It answers in his context: his role, his persona, his projects, and only the documents IAM lets him read. Verdict: FAIL, citing our runbook's canary and Friday-freeze rules." |
| **0:30–0:45** | Switch person → **Marcus (CTO)**. Ask `What happened in the August 14 outage and what should we do next?` | "Same company, different person. The CTO has all 20 agents and gets a board-level answer. A developer who tries the Board Brief agent gets a 403, because IAM policies compile straight into the MongoDB `$vectorSearch` filter." |
| **0:45–1:05** | Back as Diego: ask, rephrase it, then `Now also write the rollback checklist for that deploy` | "Here's the core idea: nobody fills in feedback forms. Diego rephrased, so the first answer missed. Then he built on it, and the system classified that follow-up as an *extension* in the background. Every rephrase, correction, copy and follow-up becomes a signal in Atlas and in Raindrop." |
| **1:05–1:25** | 🤖 My agents → **Release Agent** → **🌧 Where it fails**. Then 📈 Learning → **Improve from last 7 days ▶** (let the steps stream) | "Raindrop tells each agent where it's failing, by failure type and playbook version. Every person and every agent runs a 7-day improvement cycle, or I can trigger one now. It reads the week's ledger, pulls Raindrop failures, proposes one change, then tests it on unseen cases and a blind side-by-side replay." |
| **1:25–1:42** | `/workflow`: agent self-improvement flowchart | "A change is only kept if the data proves it. It has to pass every gate and an absolute leak test, and come from a pattern seen in two or more people. Template or tool changes wait for a manager. After release, a watchdog compares live failure rates and auto-rolls back." |
| **1:42–1:52** | 📊 **Impact** | "So far: 26 automatic decisions, 6 kept, 12 undone, 4 refused, plus 38 points on kept changes, zero leaks, and 78% of answers right the first time." |
| **1:52–2:00** | ⋯ Under the hood → 🗄 Data layer (or the README architecture) | "MongoDB Atlas holds the agents, every playbook and persona version, memory and the hash-chained audit. Voyage embeds, Raindrop finds the failures, and Modelence hosts it. Any model plugs in." |

## Backup lines if something is slow
- A model answer is slow: "This runs on a local Gemma model, so private data never leaves the machine."
- The improvement cycle is still running at 1:25: cut to `/workflow` and come back for the result at the end.
