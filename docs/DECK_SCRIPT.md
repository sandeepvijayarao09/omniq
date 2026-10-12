# Omniq Atlas: speaker script for the pitch deck

13 slides, about 4 minutes, about 560 spoken words. The deck's flow is Problem → Proposal → Solution → Results.
Say "MongoDB" and "Raindrop" by name whenever they do the work.

Before presenting: open the live app in a second tab, signed in as Diego, and ask one question so the local model is warm.

---

## 01 · Title (0:00–0:15)
"I'm Sandeep, and this is Omniq Atlas. One person plus a team of self-improving agents is 10×. Every employee gets
specialist agents that improve every week from how people actually work. Raindrop finds where they fail, and MongoDB
Atlas holds every version and the proof."

## 02 · Problem (0:15–0:35)
"Company agents ship once and never get better. The prompt written on launch day is still the prompt a year later.
Nobody fills in feedback forms; the real signal is what people do next, like rephrasing or correcting. And letting an
agent rewrite itself is risky: it can get worse, leak data, or widen its own access."

## 03 · Proposal (0:35–0:55)
"So our proposal has three parts. Learn from behaviour: every rephrase, correction, follow-up and copy is a signal.
Find the failure with Raindrop: every answer is a Raindrop event, and its Query API tells each agent where it fails.
Prove every change in MongoDB: each change is a new playbook version in Atlas, kept only if it beats its parent on
unseen tests. Three triggers start a cycle, and five gates decide what's kept."

## 04 · Solution: the product (0:55–1:15)
"Here's the product. A 10-person company: a CTO, two directors, three PMs and four developers. Each role has five agents,
like Build, Verifier, Debug and Release for developers, and higher roles inherit the ones below, so the CTO has all 20.
You hand off a job with an @ mention, and the agent answers with your role, persona and projects, and only the documents
you're allowed to see. It's model agnostic, and private data stays on local models."

## 05 · Solution: how it works (1:15–1:45)
"This is the loop. One: the agent answers, grounded by Atlas Vector Search, with access rules compiled into the
`$vectorSearch` pre-filter. Two: Raindrop observes; each answer is an event and each rephrase or correction is a signal.
Three: the Raindrop Query API diagnoses failures by agent, failure type and playbook version. Four: the improvement cycle
reads the week's ledger from Atlas and proposes one change. Five: the change has to prove itself. Six: it's kept as a new
version in Atlas, or recorded as undone. The next answer uses the new version, and a watchdog rolls back if live
failures get worse."

## 06 · Solution: when it improves (1:45–2:05)
"Three things start a cycle. One: a Raindrop error spike. If an agent's failure rate crosses 30% over 24 hours, a
cycle starts immediately, with a cooldown so it can't loop. Two: every 7 days, for every agent and every person, even
when nothing looks wrong. Three: on demand, when someone clicks 'Improve from last 7 days', and IAM decides who's allowed."

## 07 · Solution: end to end (2:05–2:20)
"Here's the whole thing as a flowchart: people, signals and Raindrop, the improvement engine, the gates, and MongoDB
Atlas at the bottom, where every version lands. The dashed line is the loop back to the next job."

## 08 · Solution: MongoDB Atlas (2:20–2:45)
"MongoDB Atlas is the agents' memory and their proof. It runs on the Atlas Sandbox. Vector Search, with IAM in the
pre-filter, so the database enforces access. `$rankFusion` for hybrid search, which the loop can switch on. Voyage
embeddings through the Atlas Model API. Every playbook, persona and context layer is a versioned document with its parent
and evidence, so rollback is one update. Change streams stream each cycle live, `$graphLookup` gives us the company
ontology, TTL locks keep cycles from colliding, and every decision goes into a hash-chained audit log."

## 09 · Solution: Raindrop (2:45–3:05)
"Raindrop sits inside the loop, not beside it. We write every answer as an event, using the Atlas episode id as the
event id so both systems join exactly. We read it back through the Query API in every cycle: failing answers by type,
and failure rate per version. And we act on it: a spike starts a cycle, and after release the watchdog compares versions
and rolls back."

## 10 · Solution: guardrails (3:05–3:20)
"Agents improve every week, and they can't make themselves worse. The same pattern must come from two or more people;
otherwise it's just a personal preference. It must pass every gate and the leak test, and be better on unseen tests and a
blind replay. Template or tool changes need a manager, and live failures trigger rollback. No agent can edit IAM or skip a
gate."

## 11 · Results (3:20–3:40)
"Every number here is a query over Atlas. 26 automatic decisions about playbooks. 6 kept, 12 undone, 4 refused by safety
rules, 1 waiting for a manager. Kept changes gained 38 points on average, there were zero leaks, and 78% of answers were right
the first time. The end-to-end suite passes 29 out of 29 on the public URL."

## 12 · Business value (3:40–3:52)
"For a business, that means each person does the work of a team, knowledge compounds every week instead of getting lost
in chat, mistakes stop repeating, and it's safe to deploy: access enforced by MongoDB, full audit, no model lock-in."

## 13 · Try it (3:52–4:00)
"It's live. Pick Diego, ask the Release agent about shipping on Friday, then open 'Where it fails' and 'Improve from last
7 days'. The link and the repo are on the slide. Thank you."

---

## If there's time for a live demo (60 seconds, after slide 06 or at the end)
1. As **Diego**: `@release_agent can I ship on friday afternoon` → rephrase → "Now also write the rollback checklist".
   Say: "Nothing popped up, but it logged a rephrase and understood the follow-up as an extension."
2. My agents → Release Agent → **Where it fails**: "This comes from the Raindrop Query API."
3. Learning → **Improve from last 7 days ▶**: "Each step streams live from a MongoDB change stream."
4. Only run step 3 if the numbers don't need to match the slides; a cycle adds a decision.

## Likely judge questions
| Question | Answer |
|---|---|
| How do you know a change is actually better? | It runs on held-out test cases and a blind, order-randomized side-by-side replay judged by a model, with personal memory turned off. It's kept only if it wins and passes the leak test on every case. |
| What stops one user from steering an agent? | A shared change needs the same pattern from 2+ distinct people. One person's preference only applies to them. |
| Why MongoDB and not a vector database? | One place holds vectors, full-text search, the ontology graph, versioned playbooks, change streams, TTL locks and the audit log. Access control lives in the `$vectorSearch` pre-filter, so the database enforces it. |
| What does Raindrop do that your own logs don't? | It's the failure analysis layer: we read failure types and per-version failure rates back through the Query API, and it drives the spike trigger and the rollback watchdog. |
| What if the improvement makes things worse in production? | The watchdog compares live Raindrop failure rates of the new version against its parent and rolls back automatically. After 3 failures, improvement pauses for that agent. |
| Which models? | A model-agnostic gateway (`config/models.yaml`): local Gemma 4 and Llama 3.2 via Ollama today, Claude, OpenAI, Gemini or Groq by config. Board, HR and finance data is only sent to local models. |
| What was built today? | All code was written during the hackathon. The fictional company documents are reused from an earlier prototype. |

## Coverage checklist
- Problem, proposal, solution, results: slides 02, 03, 04–10, 11–13
- Self-improvement loop: 05, 07, 10
- Three triggers (30% Raindrop spike, 7 days, on demand): 06
- MongoDB: Vector Search + IAM pre-filter, `$rankFusion`, Voyage via Atlas, versioned documents, change streams,
  `$graphLookup`, TTL locks, audit chain, Atlas Sandbox: 05, 08, 11
- Raindrop: write events and signals, Query API read-back, spike trigger, rollback watchdog: 05, 06, 09, 10
- Implicit signals and follow-up intent (no feedback forms): 02, 03, 05
- IAM and privacy (pre-filter, name masking, local-only private data): 04, 08, 10
- Model agnostic: one line on 04
- Governance (quorum, gates, leak test, manager approval, rollback): 10
- Numbers from Atlas: 11
- Business value and live link: 12, 13
