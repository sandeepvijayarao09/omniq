# 2-minute video script: Omniq Atlas

Before recording: `.venv/bin/oa demo-reset`, restart with `OA_AUTO_EVOLVE_MINUTES=0 OA_CYCLES=off .venv/bin/oa serve`,
hard-refresh, browser full-screen, mic on. About 320 spoken words.

## 1. What is this (0:00–0:15)
**On screen:** org-chart sign-in, then Diego's chat with his 5 agents.
**Say:** "This is Omniq Atlas. Every employee gets a team of specialist agents, and those agents improve themselves every
week by watching how people work. MongoDB Atlas is their memory and their proof. Raindrop is how they find out where
they're failing."

## 2. Model agnostic (0:15–0:20)
**On screen:** ⋯ Under the hood → **Models**.
**Say:** "It's model agnostic: local Gemma or Llama, or any cloud model by config, and private data stays on local models."

## 3. How this works (0:20–1:20)
**On screen:** as **Diego**: `@release_agent can I ship on friday afternoon` → rephrase `can I deploy on a Friday
afternoon?` → `Now also write the rollback checklist for that deploy`.
**Say:** "Diego asks his Release agent. MongoDB grounds the answer with Atlas Vector Search, Voyage embeddings and
`$rankFusion` hybrid search, and his access rights are compiled into the `$vectorSearch` pre-filter, so MongoDB itself
enforces who sees what. He rephrases: that's a miss. He builds on the answer: understood as an extension. Nobody fills in a
feedback form. Every answer is logged to Atlas and sent to Raindrop as an event, and every rephrase, correction and copy
becomes a Raindrop signal."

**On screen:** My agents → Release Agent → **🌧 Where it fails**.
**Say:** "Then the agent reads it back through the Raindrop Query API: which answers failed, why, and on which playbook
version. A Raindrop failure spike triggers an improvement cycle immediately."

**On screen:** 📈 Learning → **Improve from last 7 days ▶** (let the steps stream in).
**Say:** "Every person and every agent also runs a 7-day cycle. MongoDB change streams push each step to the screen live.
It builds the week's decision ledger from Atlas, pulls the Raindrop failures, works out whether retrieval or reasoning
failed, and proposes one change to the agent's playbook. Then it has to beat the current version on unseen tests and a
blind side-by-side replay. TTL locks in MongoDB make sure two cycles never collide."

## 4. What is the overall outcome (1:20–1:35)
**On screen:** the cycle's kept/undone result, then the agent's version timeline.
**Say:** "A change is kept only if the data proves it, it passes a leak test, and two or more people showed the same pattern.
Otherwise it's undone. Every playbook and persona is versioned in Atlas with its parent and its evidence. After release,
a watchdog compares Raindrop failure rates per version and rolls back automatically if the new one is worse."

## 5. What is the business value (1:35–1:47)
**On screen:** 📊 **Impact**, then ⋯ Under the hood → **🗄 Data layer**.
**Say:** "For a business, each person does the work of a team. Knowledge is captured in agent playbooks instead of lost in
chat, mistakes stop repeating, and every decision is in a hash-chained audit log in MongoDB."

## 6. Results (1:47–2:00)
**On screen:** 📊 **Impact** numbers.
**Say:** "The results, queried straight from MongoDB: 26 automatic decisions, 6 kept, 12 undone, 4 refused, plus 38 points on kept
changes, zero leaks, and 78% of answers right the first time. Agents that get better every week: memory and proof in MongoDB
Atlas, failure insight from Raindrop."

## Backup lines if something is slow
- A model answer is slow: "This runs on a local model, so private data never leaves the machine."
- The improvement cycle is still running at 1:20: show the version timeline, then come back for the result before Impact.
