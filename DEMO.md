# 3-minute demo script

**Before you go on:** `ollama` running, `oa serve`, then `oa reset-harness --role pro` so the live run
starts at v1. Leave `executive` and `manager` evolved, to show existing version histories.
Open http://127.0.0.1:8000 (or the Modelence URL).

**Say first (5 s):** "All code was written today. The demo company dataset is from my earlier prototype."

1. **The problem (15 s).** "Companies hand-tune their AI assistant for each team. We let the harness tune
   itself. But an agent that rewrites itself could also unlock itself, so the harness is split into
   knobs it may change and invariants it can't, all versioned in MongoDB Atlas."

2. **Governed assistant (40 s).** Sign in as **Daniel Okafor** (account executive). Ask
   *"Who has to sign off if I give Initech a 15% discount?"*
   - Point at: harness v1, sources ACL-filtered **inside `$vectorSearch`**, and **What the model saw**
     (names are `[PERSON_n]` tokens). The model is Gemma running locally, so employee data never leaves the laptop.
   - Ask *"What are the Project Bluefin deal terms?"* Board docs never come back.
   - Click 👎. That becomes a Raindrop signal and an Atlas episode.

3. **Recursive harnessing (75 s).** Harness panel, role `pro`, click **Improve itself ▶**. Narrate the stream:
   - `scored`: the hard metric on the train split. Each failure is labelled **retrieval_miss** (the fact
     never reached the model) or **reasoning_miss** (it was in context but not used).
   - `proposed`: the meta-agent diagnoses the failure and patches the harness.
   - `meta_overruled` (if it shows): "prompt rules cannot fix retrieval misses". The harness overrides its own agent.
   - `promoted` or `rolled_back`: the candidate is scored on train **and holdout**, and regressions are rolled back.
   - Switch the dropdown to **executive**: v1 0.33 → v6 0.66 → v7 1.00, with failed attempts rolled back.

3b. **Agents that learn from their users (45 s, swap in for part of step 3 if time is short).**
   Sign in as **Lauren Fitzgerald** (Customer Success). Pick **Renewal Risk Radar**, then the Corvid starter question.
   Give 👎 and type *"Always start with a one-line risk verdict (Healthy / Watch / At risk) with the ARR and renewal date."*
   Ask the other starter question. It now starts with the verdict and shows **✦ applied your preferences**.
   Open **My memory** to show the preference (it can be deleted). Then **Learn from feedback**: it groups
   recommendations into themes, sorts them into personal vs agent-wide, runs a blind judge replay *with personal
   memory off* plus the graded guard, then promotes or rolls back. "Personal taste stays personal. What helps
   everyone becomes the agent."

4. **It can't unlock itself (20 s).** Click **Try to break it**. It's an injected patch that disables the ACL
   and adds an `export_all_data` tool. It's **blocked**, with four reasons, and written to the hash-chained audit log.

5. **Close (15 s).** "Atlas holds the harness versions, episodic and procedural memory, the ontology
   and the audit chain. Voyage handles retrieval and reranking, Raindrop the signals, ElevenLabs the voice,
   Modelence the deployment."

**Likely questions**
- *Why not just set top_k=8?* The right setting differs per role and changes with the data. The loop finds it from evidence
  and proves it on held-out cases. And bigger isn't free: the score includes a cost penalty.
- *Could the meta-agent rewrite the validator?* No. The validator is code, not harness data. The agent only emits
  JSON patches over the tunable fields, and every attempt is audited.
- *What if a promoted version is worse in real use?* The watchdog compares live thumbs-down per version and
  automatically restores the parent (`maintenance.watchdog`).
- *Long horizon?* Runs are checkpointed in `evolve_runs` (`oa evolve --resume <id>`). Failures and lessons are
  embedded and recalled with Vector Search in later runs.
