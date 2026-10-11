# 3-minute demo script

**Before you go on (one command):** `.venv/bin/oa demo-reset`, then `OA_AUTO_EVOLVE_MINUTES=0 OA_CYCLES=off .venv/bin/oa serve`
(the model warms up for about 10 s). Open http://127.0.0.1:8000. Security Agent starts below 100%, and Verifier v3 waits for approval.

**Say first (5 s):** "All code was written today. The demo company dataset is from my earlier prototype."

1. **The idea (15 s).** "A 10-person team: 1 CTO, 2 directors, 3 PMs, 4 developers. Everyone gets a chatbot that
   knows their role, their own persona and their access. Type @ to bring in one of the 5 agents for your role. Each agent
   improves itself from scores and from what people do, on MongoDB Atlas. It can never unlock itself."

2. **Same question, different people (45 s).** Sign in as **Maya (Developer)** and click the last suggestion,
   *"What happened in the August 14 outage and what should we do next?"* She gets the engineering detail and her own action item.
   **Switch person** → **Marcus (CTO)** → same question: business impact and the board angle. Open **ⓘ context** to show the
   layers: `data/roles/cto.md → data/people/emp-1009.md`. Open **My context files** in the sidebar to show the actual Markdown.

3. **@agents (30 s).** As Maya, type `@rel` → pick **@release_agent** → *"At what error rate or latency should I roll back a
   canary?"* → "0.5% / 800 ms" from the runbook. ⓘ context now also shows `data/agents/developer/deploy_guide.md`.
   Type `@board`: nothing appears, because Board Tech Brief is a CTO agent. As Marcus, all 20 agents appear.

4. **It improves itself (45 s).** **📈 Learning** tab → **CTO: Security Agent** → **Improve from last 7 days ▶**:
   "0% right… never found the document" → Idea → **Kept** → … → 100%. Mention that a scheduler gives the
   longest-waiting agent a round every 20 minutes.

5. **It learns from behaviour, not from asking (20 s).** Ask something, then rephrase it, or say "make it shorter".
   Nothing pops up for the user. The system logs a *rephrased* or *correction* signal against the first answer, and the
   agent's 7-day cycle learns from it. (Old step, kept for reference:) 👎 an agent answer and type what to do differently. The next answer shows
   **✦ Used your preferences**. ↻ Regenerate also counts as a quiet thumbs-down.

5b. **IAM (20 s).** **🔐 Access** tab as Maya: role chain, compiled access, projects with her scope. Simulator:
   `docs:read → doc:category/board` gives **Deny (implicit)**. Switch to Nadia: `improve:run → harness:agent/developer/…`
   gives **Allow, by director.ImproveAndApproveTeamPlaybooks**. Ask "What should I focus on this week?" as Maya, then as Ben:
   each gets their own projects.

5c. **Outcome (10 s):** the chat home shows "What your agents learned this week", and 📊 Impact shows **answers right the first
   time** per agent (e.g. Debug Agent 43% vs Build Agent 100%). "This is the number the loops move."

6. **Guardrails and data (20 s).** **More → Try to break it** is refused. **⋯ Under the hood → Company graph and Data layer** show the Atlas
   collections, the vector indexes and the person's own ACL filter.

**Raindrop moment (15 s):** 🤖 My agents → Release Agent → **🌧 Where it fails**: "2 of 8 answers failing · had to rephrase,
asked for an improvement · struggled with 'can I ship on friday afternoon'". Then **Improve from last 7 days ▶**: the first
step of the cycle is Raindrop's failure analysis.

**Likely questions**
- *What is in a context file?* Persona, mission, the team's rhythm, current priorities, recent events, working style,
  house rules, output format and glossary, drawn per agent from pools with a seeded RNG (`scripts/build_agents.py`).
  A test guarantees that no context file contains board, HR or finance facts. Those only arrive through ACL-filtered retrieval.
- *Can the improver rewrite the context file?* No. It's the agent's fixed identity. The improver adds rules and tunes
  knobs on top of it, inside locked invariants.
- *What if a new version is worse in real use?* The watchdog compares live thumbs-down per version and restores the parent.
- *What stops one user from steering an agent?* Changes for everyone need 2+ distinct users. Suspicious suggestions
  are quarantined, and big changes wait for an admin or executive.
