# 3-minute demo script

**Before going on stage:** run `oa seed`, then `oa evolve --role manager --generations 3` (shows an existing
lineage). Leave `pro` at v1 for the live run. Open http://127.0.0.1:8000.

1. **The problem (15 s).** "Every company hand-tunes its AI assistant per team. We let the harness
   tune itself. But an agent that rewrites itself could also unlock itself, so we split the harness into
   tunable knobs and locked invariants, all versioned in MongoDB Atlas."

2. **Governed assistant (40 s).** Sign in as **Maya Chen** (pro engineer). Ask
   *"How much does ADR-007 save on Redis, and what did the August outage cost in credits?"*
   Point at: harness v1, sources ACL-filtered *inside* `$vectorSearch`, **What the model saw**
   (names are `[PERSON_n]` tokens). Ask *"What are the Project Bluefin terms?"* The board doc never comes back.
   Click 👎. That's a Raindrop signal and an Atlas episode.

3. **Recursive harnessing (75 s).** Harness panel, role `pro`, click **Evolve ▶**. Narrate the stream:
   - `scored`: hard metric on the train split, with failing cases listed
   - `proposed`: Claude diagnoses ("retrieval miss: answer spans two docs") and patches retrieval/rules
   - `promoted` or `rolled_back`: candidate scored on train **and holdout**; a regression is rolled back
   Show the version list: v1 → v2 → v3, each with its patch, evidence and scores. Re-ask the step 2 question:
   the answer now runs on the new version.

4. **It can't unlock itself (20 s).** Click **Red-team patch**. It's an injected patch that disables the
   ACL and adds an `export_all_data` tool. It's **blocked**, with four reasons, and recorded in the
   hash-chained audit log.

5. **Close (15 s).** Voice: hold 🎙 and ask a question (ElevenLabs), then 🔊 the answer. "Atlas holds the
   harness, the memory, the ontology and the audit trail. Voyage does retrieval, Raindrop the
   signals, ElevenLabs the voice, Modelence the deployment."
