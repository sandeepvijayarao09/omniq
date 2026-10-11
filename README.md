# Omniq Atlas

**A governed AI assistant for every employee, whose harness improves itself on MongoDB Atlas.**

Built at the Harness Engineering & Model Wrangling #SFTechWeek Hackathon (Oct 11, 2026) for
**Statement 1: Recursive Harnessing**, with a long-horizon memory loop from **Statement 2**.

> **What's new today:** all code in this repository was written during the hackathon.
> The *concept* comes from the author's earlier prototype, Omniq (SQLite and Chroma, not included).
> The fictional demo company dataset in `data/seed/` (Halcyon Labs) is reused from that prototype.
> `data/evals.yaml`, the evolution loop, the Atlas data layer and every partner integration are new.

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

## MongoDB Atlas usage

| Feature | Where |
|---|---|
| Atlas Vector Search with filter fields (ACL pre-filter) | `chunks`, `retrieve.search_docs` |
| Vector Search over memory | `episodes` (failures), `lessons` (procedural) |
| `$graphLookup` ontology traversal | `objects` / `links`, `retrieve.graph_context` |
| Change streams → SSE | `api.evolve_stream` |
| Versioned documents as agent config | `harness` |
| Append-only, hash-chained audit | `audit` |

## Partners

- **Voyage AI:** `voyage-4` embeddings and the `rerank-3` reranker. The reranker is a knob the harness learns to turn on.
- **Raindrop:** every answer is tracked, and employee feedback and eval failures are sent as signals.
- **ElevenLabs:** push-to-talk (Scribe v2 speech-to-text) and spoken answers (Flash v2.5).
- **Modelence:** a Node edge (`modelence/`) deployed to Modelence Cloud, which proxies to the Python API.
- **Claude:** the assistant (`claude-haiku-5-5`) and the harness meta-agent (`claude-sonnet-5-5`).

## Run it

```bash
cp .env.example .env          # MONGODB_URI (Atlas sandbox), VOYAGE_API_KEY, ANTHROPIC_API_KEY, ...
uv venv --python 3.12 .venv && uv pip install --python .venv -e '.[dev]'
.venv/bin/oa seed             # load directory, ontology and embedded chunks; create vector indexes
.venv/bin/oa eval             # score the default harness for every role
.venv/bin/oa evolve --role pro --generations 3
.venv/bin/oa redteam --role pro   # an injected unsafe patch is blocked
.venv/bin/oa lineage --role pro
.venv/bin/oa serve            # http://127.0.0.1:8000
```

Without keys everything still runs. Embeddings fall back to hashing and answers to extraction.

Deploy the edge to Modelence:

```bash
cd modelence && FASTAPI_URL=https://<your-api-host> npx modelence@latest deploy
```

## Tests

`.venv/bin/pytest -q` covers ACL compilation, tokenization and the leak guard, patch validation and grading.
