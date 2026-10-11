# 3-minute demo script

**Before you go on:** Ollama running, `oa serve` (or the Modelence URL), then `oa reset-harness --role agent:deploy_guide`
so the live run starts at v1. Open http://127.0.0.1:8000.

**Say first (5 s):** "All code was written today. The demo company dataset is from my earlier prototype."

1. **The idea (15 s).** "A company has four roles: Developer, Product Manager, Director and CTO. Each role gets five
   agents. Each agent is one Markdown context file plus a playbook that improves itself, all on MongoDB Atlas. It can
   tune itself, but it can never unlock itself."

2. **Hierarchy and context files (40 s).** On the sign-in screen, people are grouped CTO → Directors → PMs and Developers.
   - Sign in as **Maya Chen (Developer)**: 5 agents. Pick **Deploy Guide** → **📄 Context file**. That is exactly what the
     model receives, including its LLM connection (model, temperature, max tokens).
   - Ask the starter *"At what error rate or latency should I roll back a canary?"* Show **What the AI model saw**
     (names masked) and **Where this came from** (filtered inside `$vectorSearch`).
   - Switch to **Priya Raman (Director)**: 15 agents (hers plus every Developer and PM agent). **Team Pulse** only
     reads notes for the team she manages.
   - **Marcus Whitfield (CTO)**: all 20. Board Tech Brief works for him; Maya gets "not in the audience".

3. **It improves itself (60 s).** Right panel, pick **Developer: Deploy Guide**, then **Improve itself ▶**.
   Read the plain sentences: Score → Idea → Kept / Undone. Changes are kept only if they're better on unseen questions
   with zero leaks.

4. **It learns from you (30 s).** As Maya, 👎 an answer and type *"Always end with the exact rollback command."*
   The next answer shows **✦ Used your preferences**. **My memory** shows it, and you can delete it. When a second developer
   asks for the same thing, **Learn from feedback** tries it for everyone using a blind side-by-side comparison.

5. **It can't unlock itself (15 s).** **Try to break it**: a change that switches off access rules and adds an
   "export all data" tool is refused, and the refusal is written to the audit log.

6. **Show the data (15 s).** The **🕸 Ontology** tab shows the company graph with hidden links counted, not shown.
   The **🗄 Data layer** tab shows all Atlas collections, vector indexes and *your* ACL filter. Close:
   "Atlas holds the agents, their playbook versions, memory, the ontology and the audit trail."

**Likely questions**
- *What is in a context file?* Persona, mission, the team's rhythm, current priorities, recent events, working style,
  house rules, output format and glossary, drawn per agent from pools with a seeded RNG (`scripts/build_agents.py`).
  A test guarantees that no context file contains board, HR or finance facts. Those only arrive through ACL-filtered retrieval.
- *Can the improver rewrite the context file?* No. It's the agent's fixed identity. The improver adds rules and tunes
  knobs on top of it, inside locked invariants.
- *What if a new version is worse in real use?* The watchdog compares live thumbs-down per version and restores the parent.
- *What stops one user from steering an agent?* Changes for everyone need 2+ distinct users. Suspicious suggestions
  are quarantined, and big changes wait for an admin or executive.
