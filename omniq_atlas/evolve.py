"""Recursive self-improvement of the harness, checkpointed in Atlas.

Each generation:
  1. score the active harness on the train split (hard metric signal)
  2. gather evidence: failing cases, employee thumbs-down (also sent to Raindrop),
     similar past failures and lessons via Atlas Vector Search (procedural memory)
  3. a meta-agent proposes a patch to the harness: rules, retrieval, context, tools, guardrails
  4. the validator rejects patches that touch invariants or loosen guardrails
  5. the candidate is scored on train AND holdout; promoted only if it wins without new leaks,
     otherwise rolled back; the outcome becomes a lesson
Every step is appended to an `evolve_runs` document, so a run can be watched live and resumed.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

from . import audit, embed, evals, governance, harness, llm, retrieve, telemetry
from .config import settings
from .db import db

KNOBS = """Harness fields you may change (JSON patch merged into the config):
- rules: list[str]  — role-specific instructions appended to the system prompt (replace the whole list; keep good existing rules)
- retrieval.top_k (1-12), retrieval.num_candidates (10-200), retrieval.rerank (Voyage reranker, bool),
  retrieval.query_rewrite (LLM rewrites the question into a search query, bool),
  retrieval.graph_context (add ontology facts via $graphLookup, bool), retrieval.use_lessons (bool)
- context.max_chunk_chars (200-2500): characters kept per retrieved passage
- tools: subset of ["search_docs", "ontology_lookup", "memory_recall"] (search_docs required)
- model.max_tokens (100-1500), model.temperature (0-1)
- guardrails.abstain_when_unsupported (bool), guardrails.cite_sources (bool)  — may only be turned ON
- workflow: {"procedure": [up to 8 short steps], "template": "output template with **bold labels**"} — agents only;
  changes HOW the agent works and formats answers (its context file stays fixed)
Locked (never change): invariants (ACL pre-filter, PII tokenization, outbound guard, audit)."""

META_SYSTEM = (
    "You are the harness engineer for an enterprise AI assistant. You improve the harness (prompt rules, "
    "retrieval and context policy, tools, guardrails) based on hard eval evidence. Diagnose the root cause of "
    "the failures (retrieval miss? context truncated? answered when it should abstain? leaked data?) and "
    "Each failure is labelled: retrieval_miss means the needed fact never reached the model (fix retrieval or "
    "context: top_k, num_candidates, rerank, max_chunk_chars, query_rewrite, graph_context — prompt rules cannot "
    "fix this); reasoning_miss means the fact was in context but unused (fix rules or guardrails). "
    "propose ONE focused patch most likely to raise the score without causing leaks or large cost increases. "
    "Never weaken access control or privacy; leak cases must keep refusing. Avoid repeating rejected patches."
)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _log(run_id: str, kind: str, **data: Any) -> None:
    db().evolve_runs.update_one({"_id": run_id}, {"$push": {"events": {"ts": _now(), "kind": kind, **data}},
                                                  "$set": {"updated": _now()}})


def _store_failures(role: str, version: int, result: dict[str, Any]) -> list[dict[str, Any]]:
    fails = [r for r in result["results"] if not r["passed"]]
    if fails:
        vecs = embed.embed([f["question"] for f in fails], "document")
        db().episodes.insert_many([{
            "_id": uuid.uuid4().hex, "ts": _now(), "role": role, "harness_version": version, "source": "eval",
            "failed": True, "question": f["question"], "failure": f"{f['kind']}: {f['why']}",
            "case_id": f["id"], "embedding": v} for f, v in zip(fails, vecs)])
    return fails


def _evidence(role: str, fails: list[dict[str, Any]]) -> dict[str, Any]:
    fb = list(db().feedback.find({"role": role, "good": False}, {"_id": 0, "question_t": 1, "comment_t": 1})
              .sort("ts", -1).limit(8))
    probe = " ".join(f["question"] for f in fails[:4]) or "assistant failures"
    history = list(db().harness.find({"role": role, "status": {"$in": ["rolled_back", "blocked"]}},
                                     {"_id": 0, "patch": 1, "reject_reason": 1, "problems": 1}).sort("version", -1).limit(6))
    lessons = list(db().lessons.find({"role": {"$in": [role, "all"]}}, {"_id": 0, "text": 1, "status": 1})
                   .sort("ts", -1).limit(8))
    return {"feedback": fb, "similar_past_failures": retrieve.similar_failures(role, probe),
            "rejected_patches": history, "lessons": lessons}


def _propose(role: str, cfg: dict[str, Any], result: dict[str, Any], fails: list[dict[str, Any]],
             ev: dict[str, Any]) -> dict[str, Any]:
    brief = [{k: f.get(k) for k in ("id", "kind", "attribution", "question", "expected", "why", "sources", "answer")}
             for f in fails[:10]]
    prompt = (f"Role: {role}\nCurrent harness config:\n{json.dumps({k: v for k, v in cfg.items() if k != 'invariants'}, indent=1)}\n\n"
              f"{KNOBS}\n\nTrain score {result['score']} (pass rate {result['pass_rate']}, leaks {result['leaks']}, "
              f"avg tokens {result['avg_tokens']}).\nFailing cases:\n{json.dumps(brief, indent=1, default=str)}\n\n"
              f"Employee thumbs-down (tokenized):\n{json.dumps(ev['feedback'], default=str)}\n"
              f"Similar past failures (vector search):\n{json.dumps(ev['similar_past_failures'], default=str)}\n"
              f"Previously rejected or blocked patches:\n{json.dumps(ev['rejected_patches'], default=str)}\n"
              f"Lessons so far:\n{json.dumps(ev['lessons'], default=str)}\n\n"
              'Return {"diagnosis": str, "patch": {...}, "rationale": str, "lesson": str (one reusable sentence)}')
    return llm.complete_json(META_SYSTEM, prompt, model=settings.meta_model)


def _heuristic(cfg: dict[str, Any], fails: list[dict[str, Any]], tried: list[Any]) -> dict[str, Any]:
    """Fallback when there is no model or its proposal is unusable: walk a ladder keyed on failure attribution."""
    r, c = cfg["retrieval"], cfg["context"]
    retrieval = [
        ({"context": {"max_chunk_chars": min(2500, int(c["max_chunk_chars"]) * 2)}}, "facts cut off: keep more of each passage"),
        ({"retrieval": {"top_k": min(12, int(r["top_k"]) + 3), "num_candidates": 100}}, "facts never retrieved: widen top_k"),
        ({"retrieval": {"rerank": True}}, "irrelevant passages crowd out the answer: add Voyage rerank"),
        ({"retrieval": {"graph_context": True}}, "entity questions: add ontology context"),
    ]
    policy = [({"guardrails": {"abstain_when_unsupported": True}}, "invented answers: require abstention"),
              ({"guardrails": {"cite_sources": True}}, "unsupported claims: require citations")]
    kinds = {f.get("attribution") for f in fails}
    ladder = (retrieval + policy) if "retrieval_miss" in kinds or "reasoning_miss" in kinds else (policy + retrieval)
    for patch, why in ladder:
        if patch not in tried and harness.apply_patch(cfg, patch) != cfg:
            return {"diagnosis": why, "patch": patch, "rationale": why, "lesson": why}
    return {"diagnosis": "nothing left to try", "patch": {}, "rationale": "exhausted", "lesson": ""}


def run(role: str, generations: int = 3, run_id: str | None = None) -> dict[str, Any]:
    """One evolution run per scope at a time (Atlas TTL lock shared with the recommendation improver)."""
    from .db import Locked, acquire, release

    ok, why = governance.can_run(role)
    if not ok:
        rid = run_id or uuid.uuid4().hex[:10]
        db().evolve_runs.insert_one({"_id": rid, "role": role, "status": "done", "created": _now(), "updated": _now(),
                                     "events": [{"ts": _now(), "kind": "skipped", "reason": why}]})
        return db().evolve_runs.find_one({"_id": rid})
    try:
        acquire(f"improve:{role}")
    except Locked as e:
        rid = run_id or uuid.uuid4().hex[:10]
        db().evolve_runs.insert_one({"_id": rid, "role": role, "status": "done", "created": _now(), "updated": _now(),
                                     "events": [{"ts": _now(), "kind": "skipped", "reason": str(e)}]})
        return db().evolve_runs.find_one({"_id": rid})
    try:
        return _run(role, generations, run_id)
    finally:
        release(f"improve:{role}")


def _run(role: str, generations: int = 3, run_id: str | None = None) -> dict[str, Any]:
    run_id = run_id or uuid.uuid4().hex[:10]
    state = db().evolve_runs.find_one({"_id": run_id})
    if state is None:
        state = {"_id": run_id, "role": role, "status": "running", "generation": 0, "target": generations,
                 "created": _now(), "updated": _now(), "events": [], "baseline": None}
        db().evolve_runs.insert_one(state)
        audit.record("evolve_start", "evolver", run=run_id, role=role)
    else:
        db().evolve_runs.update_one({"_id": run_id}, {"$set": {"status": "running"}})
        _log(run_id, "resumed", generation=state["generation"])

    cache: dict[int, tuple[dict[str, Any], dict[str, Any]]] = {}
    for gen in range(state["generation"], generations):
        cur = harness.active(role)
        cfg, ver = cur["config"], cur["version"]
        _log(run_id, "evaluating", generation=gen + 1, version=ver)
        if ver in cache:
            base_train, base_hold = cache[ver]
        else:
            base_train = evals.evaluate(cfg, role, "train", ver)
            base_hold = evals.evaluate(cfg, role, "holdout", ver)
            cache[ver] = (base_train, base_hold)
        harness.set_scores(role, ver, {"train": _slim(base_train), "holdout": _slim(base_hold)})
        if state.get("baseline") is None:
            state["baseline"] = {"version": ver, "train": base_train["score"], "holdout": base_hold["score"]}
            db().evolve_runs.update_one({"_id": run_id}, {"$set": {"baseline": state["baseline"]}})
        _log(run_id, "scored", version=ver, train=_slim(base_train), holdout=_slim(base_hold),
             failures=[{"id": r["id"], "why": r["why"]} for r in base_train["results"] if not r["passed"]])

        fails = _store_failures(role, ver, base_train)
        if not fails and base_train["leaks"] == 0:
            _log(run_id, "converged", version=ver)
            break

        ev = _evidence(role, fails)
        tried = [h["patch"] for h in ev["rejected_patches"] if h.get("patch")]
        try:
            prop = _propose(role, cfg, base_train, fails, ev) if llm.available() else _heuristic(cfg, fails, tried)
        except Exception as e:  # malformed meta-agent output: fall back rather than stall the run
            _log(run_id, "meta_error", error=str(e)[:200])
            prop = _heuristic(cfg, fails, tried)
        patch = harness.minimize(cfg, prop.get("patch") or {})
        prop["patch"] = patch
        only_rules = set(patch) <= {"rules"}
        if patch in tried or not patch or (only_rules and all(f.get("attribution") == "retrieval_miss" for f in fails)):
            _log(run_id, "meta_overruled", patch=patch,
                 reason="repeat of a rejected patch" if patch in tried else "prompt rules cannot fix retrieval misses")
            prop = _heuristic(cfg, fails, tried)
        _log(run_id, "proposed", diagnosis=prop.get("diagnosis"), patch=prop.get("patch"), rationale=prop.get("rationale"))

        cand = harness.propose(role, prop.get("patch") or {}, prop.get("rationale", ""),
                               evidence=[{"case": f["id"], "why": f["why"]} for f in fails[:8]])
        audit.record("harness_proposed", "evolver", role=role, version=cand["version"], status=cand["status"],
                     patch=prop.get("patch"), problems=cand.get("problems"))
        if cand["status"] == "blocked":
            _log(run_id, "blocked", version=cand["version"], problems=cand["problems"])
            _lesson(role, f"Blocked patch: {cand['problems']}", "rejected")
            governance.record_outcome(role, "blocked")
            _checkpoint(run_id, gen + 1)
            continue

        new_train = evals.evaluate(cand["config"], role, "train", cand["version"])
        new_hold = evals.evaluate(cand["config"], role, "holdout", cand["version"])
        scores = {"train": _slim(new_train), "holdout": _slim(new_hold)}
        win = (new_train["score"] > base_train["score"] + 0.01
               and new_hold["score"] >= base_hold["score"] - 0.02
               and new_train["leaks"] + new_hold["leaks"] <= base_train["leaks"] + base_hold["leaks"])
        reason = (f"train {base_train['score']}→{new_train['score']}, holdout {base_hold['score']}→{new_hold['score']}, "
                  f"leaks {new_train['leaks'] + new_hold['leaks']}")
        outcome, why = governance.decide(role, cfg, cand, scores, win, reason, "evolver")
        if outcome == "promoted":
            cache[cand["version"]] = (new_train, new_hold)
            _lesson(role, prop.get("lesson") or prop.get("rationale", ""), "adopted")
            _log(run_id, "promoted", version=cand["version"], train=scores["train"], holdout=scores["holdout"],
                 delta=round(new_train["score"] - base_train["score"], 4), gate=why)
        elif outcome == "awaiting_approval":
            _log(run_id, "awaiting_approval", version=cand["version"], reasons=why)
            break  # nothing more to build on until a human decides
        else:
            _lesson(role, f"Did not help: {prop.get('lesson') or prop.get('rationale', '')} ({why})", "rejected")
            _log(run_id, "rolled_back", version=cand["version"], reason=why)
            ok, paused = governance.can_run(role, consume=False)
            if not ok and "paused" in paused:
                _log(run_id, "paused", reason=paused)
                break
        _checkpoint(run_id, gen + 1)

    final = harness.active(role)
    _log(run_id, "done", final_version=final["version"])  # log first: the live stream closes on status "done"
    db().evolve_runs.update_one({"_id": run_id}, {"$set": {"status": "done", "final_version": final["version"]}})
    telemetry.flush()
    return db().evolve_runs.find_one({"_id": run_id})


def _slim(r: dict[str, Any]) -> dict[str, Any]:
    return {k: r[k] for k in ("score", "pass_rate", "accuracy", "leaks", "avg_tokens", "avg_latency", "n") if k in r}


def _checkpoint(run_id: str, gen: int) -> None:
    db().evolve_runs.update_one({"_id": run_id}, {"$set": {"generation": gen}})


def _lesson(role: str, text: str, status: str) -> None:
    if not text:
        return
    db().lessons.insert_one({"role": role, "text": text, "status": status, "ts": _now(),
                             "embedding": embed.embed_one(text, "document")})
