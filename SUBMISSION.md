# Submission: Omniq Atlas

**Repo:** https://github.com/sandeepvijayarao09/omniq-atlas
**Problem statements:** Recursive Harnessing (primary) and Long Horizon Engineering

## One-liner
A governed AI assistant for every employee. Its role-specific agents rewrite their own harness from hard eval
scores and from their users' recommendations, and they cannot loosen their own access controls.

## Description (paste into the form)

Companies hand-tune their AI assistants team by team. Omniq Atlas lets the harness tune itself, safely.

Every role and every role-specific agent (Deal Coach, Renewal Risk Radar, On-call Copilot, Variance Analyst and five more)
has its own **versioned harness document in MongoDB Atlas**. The harness covers rules, retrieval and context policy, tools
and guardrails. Two loops improve it:

- **Hard-metric loop:** graded eval cases (answer / leak / abstain) score the active harness. Each failure is labelled
  a *retrieval miss* or a *reasoning miss*, a meta-agent proposes a patch, and the candidate must win on train **and**
  holdout with no new leaks, or it's rolled back. The loop overrules its own meta-agent when it proposes prompt rules for a
  retrieval problem, and it caught an overfit (train ↑, holdout ↓).
- **User-recommendation loop:** a 👎 asks "what should it do differently?" The recommendation becomes that user's
  preference **immediately**, via per-agent memory in Atlas Vector Search. A scheduler then groups recommendations into
  themes (Voyage), sorts them into personal vs agent-wide, and tests the patch with a **blind LLM-judge replay with
  personal memory off** plus the graded eval guard. Then it promotes or rolls back.

Some guarantees can't be tuned: ACL pre-filtering inside `$vectorSearch`, PII tokenization with an outbound leak guard,
and a hash-chained audit log. Patches that touch them are blocked (try the red-team button). A live watchdog restores the
previous version if real users downvote a new one, and every run is checkpointed and resumable.

Results today, all on the Atlas Sandbox with a local Gemma 4 model, so no employee data leaves the machine:
pro 0.87 → 1.00, manager 0.62 → 0.87, executive 0.33 → 1.00, On-call Copilot 0.75 → 1.00, Renewal Risk 0.91 → 1.00,
with 0 leaks across every version.

**Built with:** MongoDB Atlas (Vector Search with ACL filters, `$graphLookup` ontology, change streams → live SSE,
TTL locks, versioned config docs, audit chain), Voyage AI (voyage-4 embeddings, rerank-3), Raindrop (event + signal
telemetry), ElevenLabs (Scribe STT / Flash TTS), Modelence (edge deploy), Gemma 4 via Ollama.

**Built today vs prior work:** all code was written during the hackathon. The fictional demo company dataset
(`data/seed/`) is reused from the author's earlier prototype.

## 1-minute video shot list (record with QuickTime → New Screen Recording, with your mic on)
1. (0:00–0:08) Title: say the one-liner over the app.
2. (0:08–0:20) Daniel → Deal Coach asks about a 15% discount. Show the answer, then **What the model saw** (tokens).
3. (0:20–0:35) Harness panel → pro → **Evolve ▶**. Stream: scored → proposed → promoted (0.87 → 0.94 → 1.00).
4. (0:35–0:48) Lauren → Renewal Risk Radar: 👎 + recommendation, then the next answer shows **✦ applied your preferences**.
5. (0:48–0:55) **Red-team patch**: blocked with reasons.
6. (0:55–1:00) "MongoDB Atlas holds the harness, the memory and the audit trail. Repo link on screen."
