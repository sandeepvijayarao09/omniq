# Submission: Omniq Atlas

**Repo:** https://github.com/sandeepvijayarao09/omniq-atlas
**Live demo:** https://omniq-mv4a4zfo47x-prod.cloud.modelence.app
**Problem statements:** Recursive Harnessing (primary), Long Horizon Engineering (7-day cycles, versioned memory)

## One-liner
Every person gets a team of agents that makes them 10×, and the team gets better every week by watching how they work.

## Description (paste into the form)

**One person + a team of self-improving agents = 10×.** Omniq Atlas gives every employee their own team of specialist
agents, and makes that team better every week.

A developer works with **Build** (task → implementation plan with owners, ADRs, flags, tests), **Verifier** (plan or PR →
PASS/FAIL against our runbooks and ADRs), **Debug** (symptoms → next actions and who to page), **Release** (canary,
rollback limits, freezes) and **Code Review**. A PM has Spec, Prioritization, Research, Launch and Metrics. Directors get
their five (Status, Risk, People, Capacity, Incident Review) plus every developer and PM agent. The CTO gets all 20,
including Strategy, Board Brief, Architecture Review, Vendor and Security. You hand off a job with @, and the agent does it
in your context: your role, persona, projects and exactly the documents you're allowed to see. The demo is a 10-person org
(CTO, 2 directors, 3 PMs, 4 developers).

**Every agent improves itself over time, from behaviour, not from asking.** Nobody fills in feedback forms. The system
notices when someone rephrases a question, corrects an answer, builds on it ("now also write the rollback plan"),
regenerates it or copies it, and classifies each follow-up's intent in the background. Raindrop tells each agent where it
is failing, by failure type and playbook version. Every person and every agent runs a 7-day improvement cycle, and a
Raindrop failure spike triggers one immediately. Each week the home screen shows what your agents learned.

**A change is kept only if the data proves it.** Every proposed change to an agent's playbook (rules, retrieval,
workflow, output template) must win on unseen tests or a blind side-by-side replay, pass an absolute leak safety gate,
and come from a pattern seen in 2+ people. Bigger changes wait for a manager. Everything else is undone, and that is
recorded as a decision too. Current numbers: 26 automatic decisions, 6 kept, 12 undone, 4 refused, 1 awaiting approval, +38 points average gain on kept changes, 0 leaks, 78% of answers right the first time (docs/RESULTS.md is generated from Atlas).

**It can't unlock itself.** IAM policies (explicit Deny > Allow > implicit deny, role inheritance, ${user.manages} /
${user.projects} variables) compile into the `$vectorSearch` pre-filter, so MongoDB enforces document access. Names are
masked before any model call. Private-only data (board, HR, finance) only goes to local models. Injection attempts are
quarantined, and every action is in a hash-chained audit log.

**Built with:**
- **MongoDB Atlas (core):** Vector Search with ACL filter fields; Atlas Search + `$rankFusion` hybrid retrieval;
  `$graphLookup` ontology; change streams streaming live runs; TTL locks; versioned playbooks and context layers; memory;
  audit.
- **Voyage AI:** voyage-4 embeddings, rerank-3.
- **Raindrop:** events and signals written, read back through the Query API into every agent's improvement cycle, plus
  spike triggers and the regression watchdog.
- **Modelence:** public deployment.
- **Model-agnostic gateway:** Gemma 4 and Llama 3.2 run locally via Ollama; Claude, OpenAI, Gemini and Groq plug in by config.

**Built today vs prior work:** all code was written during the hackathon. The fictional demo company documents
(`data/seed/`) are reused from the author's earlier prototype.

## 60-second video (QuickTime → New Screen Recording, mic on)
1. **0:00–0:08** "Agents that get better at your job by watching how you work, and can never unlock themselves."
   Show the org-chart sign-in.
2. **0:08–0:20** As **Maya (Developer)**, type `@verifier_agent Verify: ship the gateway change Friday 3pm to 100%`
   → **Verdict: FAIL** with the runbook rule. Switch person → **Marcus (CTO)**, same outage question → a different,
   board-level answer.
3. **0:20–0:32** As Maya, ask, then rephrase, then "now also write the rollback checklist". Say: "nothing pops up, but
   it just logged a rephrase and understood the follow-up as an extension."
4. **0:32–0:45** 🤖 My agents → Release Agent → **🌧 Where it fails** (from Raindrop). Then 📈 Learning →
   **Improve from last 7 days ▶**: Raindrop → tests → kept/undone, in plain sentences.
5. **0:45–0:55** 📊 Impact: 26 decisions, 6 kept, 12 undone, +38 pts, 0 leaks. Flowcharts at /workflow. Then ⋯ Under the hood → 🔐 Access
   simulator: Deny with the reason.
6. **0:55–1:00** "MongoDB Atlas holds the agents, their playbook versions, memory and audit. Raindrop tells each agent
   where it fails. Repo and live link are in the submission."
