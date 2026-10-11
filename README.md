# Omniq Atlas

**A governed AI assistant for every employee, whose harness improves itself on MongoDB Atlas.**

Built at the Harness Engineering & Model Wrangling #SFTechWeek Hackathon (Oct 11, 2026) for
**Statement 1: Recursive Harnessing**, with a long-horizon memory loop from **Statement 2**.

> **What's new today:** all code in this repository was written during the hackathon.
> The *concept* comes from the author's earlier prototype, Omniq (SQLite and Chroma, not included).
> The fictional demo company dataset in `data/seed/` (Halcyon Labs) is reused from that prototype.
> `data/evals.yaml`, the evolution loop, the Atlas data layer and every partner integration are new.

## In plain English

Omniq is an AI assistant for everyone in a company. Each person only ever sees the documents their job allows,
and names and contact details are masked before the AI model sees anything.

Every team, and each of the 9 job-specific agents (Deal Coach, Renewal Risk Radar, On-call Copilot…),
follows a **playbook**: how many documents to read, which rules to follow, when to say "I don't know".
Each version of every playbook is saved in **MongoDB Atlas**, and the playbooks **improve themselves**:

1. **Test.** Practice questions with known answers give each playbook a score.
2. **Find the mistake.** For each wrong answer it asks: did it never find the right document, or did it find it and miss the fact?
3. **Try a fix.** An AI "improver" suggests one change, such as "read 5 documents instead of 2" or a new rule.
4. **Keep it only if it's proven better** on questions it has never seen, with zero privacy leaks. Otherwise the change is undone.
5. **Learn from people.** A 👎 with "do this instead" changes *your* answers right away. When two or more people ask
   for the same thing, the agent tries it for everyone, and keeps it only if a blind side-by-side comparison says it's better.

**What it can never do.** It can't turn off access rules, privacy masking or the audit log.
- Suggestions that try to get around the rules are set aside.
- Big changes (new tools, dropped rules) wait for a manager's approval.
- After three failed attempts in a row it pauses itself.
- If real users dislike a new version, it automatically switches back to the old one.

**Try it:** https://omniq-mv4a4zfo47x-prod.cloud.modelence.app
- Pick a person.
- Ask the agent something.
- Press **Improve itself ▶** and watch it test, fix and decide, in plain sentences.

## The problem

Enterprise assistants are tuned by hand. Someone edits the prompt, retrieval settings and guardrails for
each team, then hopes nothing regresses. Nobody wants an agent that tunes *itself*, because it could
also loosen its own access controls.

Omniq Atlas splits the harness into two parts:

- **Tunable:** rules, retrieval and context policy, tools and output guardrails. These are versioned
  per role in Atlas and evolve from hard eval scores and employee feedback.
- **Locked invariants:** ACL pre-filtering inside `$vectorSearch`, PII tokenization, the outbound leak
  guard and the hash-chained audit log. A patch that touches these is **blocked** and recorded.

## How a request works

```
employee ─► JWT ─► active harness for their role (Atlas `harness`)
         ─► $vectorSearch with the ACL as a pre-filter  (+ Voyage rerank, + $graphLookup ontology,
            + adopted lessons, + personal memories, as the harness allows)
         ─► PII → [PERSON_1] tokens ─► outbound guard ─► Claude ─► de-tokenize
         ─► episode (Atlas) + hash-chained audit (Atlas) + Raindrop event
```

## Governance of the autonomous cycle (`omniq_atlas/governance.py`)

Both loops end in one function, `governance.decide()`.

| Layer | Guard |
|---|---|
| Inputs | Injection screening at intake (flagged recommendations are quarantined); a 2-user quorum before any agent-wide change; 10 recommendations per user per agent per day |
| Changes | Validator (locked invariants, bounds, per-agent tool allow-list, rule caps), knob type checks, LLM safety review of new rule text |
| Promotion | Must beat the parent on evidence (holdout, or blind replay plus eval guard) **and** pass an absolute safety gate: every leak case for the audience |
| Risk | Tool changes, dropped rules and output-budget blowups go to `awaiting_approval` for an admin or executive |
| Runtime | One run per scope (TTL lock), 6 runs per hour, auto-pause for 30 min after 3 failed candidates, freeze switch, manual rollback, crash recovery, live watchdog |
| Record | Every proposal, block, quarantine, approval, rollback and pause goes into the hash-chained audit log |

## How the harness evolves (`omniq_atlas/evolve.py`)

1. Score the active harness for a role on the **train** split of `data/evals.yaml`. Each case is an
   answer, leak or abstain case, graded with exact checks. A leak costs extra.
2. Gather evidence: failing cases, employee 👎 (also sent to **Raindrop** as signals), similar past failures
   and lessons recalled with **Atlas Vector Search** (episodic and procedural memory).
3. A Claude meta-agent proposes **one** patch, for example `{"retrieval": {"rerank": true, "top_k": 6}}`.
4. The validator rejects anything that touches invariants, removes guardrails or adds tools outside the allow-list.
5. The candidate runs on train **and holdout**. It's promoted only if it wins with no new leaks.
   Otherwise it's rolled back. Either way the outcome becomes a lesson.
6. Every step is appended to an `evolve_runs` document, which **checkpoints** the run (`oa evolve --resume <id>`).
   The UI streams it live through a **MongoDB change stream**.

## Role-specific agents that keep improving from their users (`agents.py`, `memory.py`, `improve.py`)

Nine agents (`data/agents.yaml`) are gated by role and department: Deal Coach, Renewal Risk Radar, On-call Copilot,
Deploy & Architecture Guide, Escalation Triage, Team Pulse, Variance Analyst, Executive Brief and Policy Helper.
Each agent has:

- **its own harness lineage** (`agent:<id>`), seeded from the template's persona, focus categories and tool allow-list.
  The persona and allow-list are fixed identity; the evolver can only add rules and tune knobs.
- **its own context policy**: retrieval searches the agent's focus categories first, intersected with the user's
  grants (focus can narrow access, never widen it), then fills from everything else the user may read.
- **its own memory layer** in Atlas Vector Search (`agent_memory`):
  - *preferences* (per user) apply to that user's next answer immediately
  - *lessons* (agent-wide) come from adopted improvements and are opt-in per harness version
  - *facts* come from `/remember`
  - recent *conversation turns* give follow-ups their context
  Users can view and delete their own memories, never anyone else's or the agent's lessons.

**The recurring improvement loop.** A 👎 asks "what should it do differently next time?"

1. The recommendation becomes the user's preference right away, and is queued in `recommendations`.
2. When an agent has 2+ pending, the scheduler, which runs every minute, starts `improve.run`:
   - groups the recommendations into themes by Voyage similarity
   - the meta-agent triages each theme as *personal* (already handled by that user's memory) or *agent-wide*
   - it drafts one patch; the validator checks it, with one self-repair attempt if it objects
3. The candidate is tested two ways:
   - a **blind, order-randomized LLM-judge replay** of the recommended questions, *with personal memory off*,
     to test whether the change helps every user
   - the **agent's graded eval guard**: no score regression, no new leaks
4. It's promoted (the theme becomes an agent-wide lesson) or rolled back.

**Production guards.** A **watchdog** auto-restores the parent version if live thumbs-down on a new version is
clearly worse. Memory **consolidation** collapses near-duplicate preferences so the newest instruction wins.
Small eval sets are scored twice, and a case passes only if it passes every time, so a lucky run can't promote a change.

## MongoDB Atlas usage

| Feature | Where |
|---|---|
| Atlas Vector Search with filter fields (ACL pre-filter) | `chunks`, `retrieve.search_docs` |
| Vector Search over memory | `episodes` (failures), `lessons` (procedural) |
| `$graphLookup` ontology traversal | `objects` / `links`, `retrieve.graph_context` |
| Vector Search over per-agent memory, filtered by agent and owner | `agent_memory`, `memory.recall` |
| Change streams → SSE | `api.evolve_stream` |
| TTL index locks (one improvement run per scope) | `locks`, `db.acquire` |
| Versioned documents as agent config | `harness` |
| Append-only, hash-chained audit | `audit` |

## Partners

- **Voyage AI:** `voyage-4` embeddings and the `rerank-3` reranker. The reranker is a knob the harness learns to turn on.
- **Raindrop:** every answer is tracked, and employee feedback and eval failures are sent as signals.
- **ElevenLabs:** push-to-talk (Scribe v2 speech-to-text) and spoken answers (Flash v2.5).
- **Modelence:** a Node edge (`modelence/`) deployed to Modelence Cloud, which proxies to the Python API.
- **Models:** any OpenAI-compatible endpoint. The demo runs **Gemma 4 (e4b) locally through Ollama**, so employee data
  never leaves the laptop. Set `ANTHROPIC_API_KEY` to switch to Claude.

## Run it

```bash
cp .env.example .env          # MONGODB_URI (Atlas sandbox), VOYAGE_API_KEY, ANTHROPIC_API_KEY, ...
uv venv --python 3.12 .venv && uv pip install --python .venv -e '.[dev]'
.venv/bin/oa seed             # load directory, ontology and embedded chunks; create vector indexes
.venv/bin/oa eval             # score the default harness for every role
.venv/bin/oa evolve --role pro --generations 3
.venv/bin/oa redteam --role pro   # an injected unsafe patch is blocked
.venv/bin/oa agents                         # agents and their active harness versions
.venv/bin/oa evolve --role agent:oncall_helper  # hard-metric loop for one agent
.venv/bin/oa improve --agent deal_coach     # recommendation-driven loop for one agent
.venv/bin/oa lineage --role pro
.venv/bin/oa serve            # http://127.0.0.1:8000
```

Without keys everything still runs. Embeddings fall back to hashing and answers to extraction.

**Live demo:** https://omniq-mv4a4zfo47x-prod.cloud.modelence.app (Modelence edge → Cloudflare tunnel → API and local Gemma).
`scripts/run_live.sh` restarts the API and tunnel.

Deploy the edge to Modelence:

```bash
cd modelence && FASTAPI_URL=https://<your-api-host> npx modelence@latest deploy
```

## Tests

`.venv/bin/pytest -q` covers ACL compilation, tokenization and the leak guard, patch validation and grading.
