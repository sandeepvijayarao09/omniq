# 2-minute video script: Omniq Atlas

Before recording: `.venv/bin/oa demo-reset`, restart with `OA_AUTO_EVOLVE_MINUTES=0 OA_CYCLES=off .venv/bin/oa serve`,
hard-refresh, browser full-screen, mic on. About 310 spoken words. Say "MongoDB" and "Raindrop" out loud in every section.

## 1. What is this (0:00–0:15)
**On screen:** org-chart sign-in, then Diego's chat with his 5 agents.
**Say:** "This is Omniq Atlas: every employee gets a team of specialist agents, and those agents improve themselves every
week by watching how people actually work. One person plus a team of self-improving agents is 10×. MongoDB Atlas is their
memory and their proof, and Raindrop tells them where they're failing."

## 2. Model agnostic (0:15–0:30)
**On screen:** ⋯ Under the hood → **Models**, then My agents → Release Agent → model choice.
**Say:** "It's model agnostic. One gateway routes every agent to whichever model is configured: Gemma and Llama running
locally, or Claude, OpenAI, Gemini or Groq by config. Each agent and each person can pick a model. Private data like board,
HR and finance only ever goes to local models. What improves is the agent's playbook in MongoDB, not the model, so the
gains carry over when you switch models."

## 3. How this works (0:30–1:15)
**On screen:** as **Diego**: `@release_agent can I ship on friday afternoon` → rephrase `can I deploy on a Friday
afternoon?` → `Now also write the rollback checklist for that deploy`. Then Release Agent → **🌧 Where it fails**. Then
📈 Learning → **Improve from last 7 days ▶** (let the steps stream in).
**Say:** "Diego asks his Release agent. Atlas Vector Search with Voyage embeddings grounds the answer, and IAM is compiled into
the `$vectorSearch` filter. He rephrases: that's a miss. He builds on it: understood as an extension. Nobody fills in
feedback forms. Every answer is a Raindrop event and every behaviour is a Raindrop signal.
The agent reads that back through the Raindrop Query API: where it fails, by failure type and playbook version. A failure
spike triggers a cycle immediately; otherwise every agent runs one every 7 days. MongoDB change streams stream each
step: the week's ledger from Atlas, Raindrop failures, one proposed change, then a test on unseen cases and a blind
side-by-side replay."

## 4. What is the overall outcome (1:15–1:30)
**On screen:** the cycle's kept/undone result, then `/workflow` flowchart (gates column) and the agent's version timeline.
**Say:** "The outcome: agents that get measurably better every week, and can't make themselves worse. A change is kept only if
it wins on the data, passes an absolute leak test, and comes from two or more people. Otherwise it's undone. If Raindrop
shows the new version failing more live, it rolls back automatically. Every version is in Atlas with its parent and evidence."

## 5. What is the business value (1:30–1:45)
**On screen:** 📊 **Impact** (top section), then ⋯ Under the hood → **🔐 Access** simulator showing a Deny.
**Say:** "For a business, that means each person does the work of a team. Company knowledge is captured in playbooks instead
of lost in chats, and the same mistakes stop repeating. It's safe by design, with zero data leaks, hash-chained audit and
manager approval for big changes. And there's no model lock-in."

## 6. Results (1:45–2:00)
**On screen:** 📊 **Impact** numbers, then `docs/RESULTS.md` on GitHub.
**Say:** "Results, straight from MongoDB: 26 automatic decisions, 6 kept, 12 undone, 4 refused, plus 38 points on kept changes,
zero leaks, and 78% of answers right the first time. Agents that get better every week and prove it, on MongoDB Atlas
and Raindrop."

## Backup lines if something is slow
- A model answer is slow: "This runs on a local Gemma model, so private data never leaves the machine."
- The improvement cycle is still running at 1:15: show the flowchart and come back for the result before Impact.
