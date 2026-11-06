"""Data-driven decision making, made visible: every automatic change, the evidence it was decided on, and
how much each part of the local stack is exercised. All numbers are computed from the local store."""

from __future__ import annotations

from collections import Counter
from typing import Any

from . import embed, llm, usage
from .db import db


def _score(v: dict[str, Any]) -> float | None:
    sc = v.get("scores") or {}
    if sc.get("train"):
        return sc["train"].get("pass_rate")
    if sc.get("evals"):
        return sc["evals"].get("pass_rate")
    return None


def decisions() -> dict[str, Any]:
    rows, outcome = [], Counter()
    by_scope: dict[str, list[dict[str, Any]]] = {}
    for v in db().harness.find({}, {"config": 0}).sort([("role", 1), ("version", 1)]):
        by_scope.setdefault(v["role"], []).append(v)
    names = {f"agent:{a['id']}": a["name"] for a in db().agents.find({}, {"id": 1, "name": 1})}
    trend = []
    for scope, vs in by_scope.items():
        parents = {v["version"]: v for v in vs}
        pts = [{"v": v["version"], "score": _score(v), "status": v["status"]} for v in vs if _score(v) is not None]
        if len(vs) > 1:
            trend.append({"scope": scope, "name": names.get(scope, scope), "points": pts,
                          "start": pts[0]["score"] if pts else None,
                          "now": next((p["score"] for p in reversed(pts) if p["status"] == "active"), None)})
        for v in vs:
            if v["version"] == 1:
                continue
            par = parents.get(v.get("parent") or 0, {})
            sc = v.get("scores") or {}
            n = (sc.get("train") or {}).get("n", 0) + (sc.get("holdout") or {}).get("n", 0) or (sc.get("evals") or {}).get("n", 0)
            outcome[v["status"]] += 1
            rows.append({"scope": scope, "name": names.get(scope, scope), "version": v["version"], "created": v["created"],
                         "decision": v["status"], "before": _score(par), "after": _score(v),
                         "replay": sc.get("replay_win_rate"), "leak_checks": (sc.get("safety_gate") or {}).get("checked"),
                         "sample": n, "evidence": len(v.get("evidence") or []),
                         "why": (v.get("reject_reason") or "; ".join(v.get("problems") or []) or v.get("rationale") or "")[:220],
                         "source": "edit" if v.get("edited_by") else ("recommendations" if sc.get("replay_win_rate") is not None else "hard metrics")})
    rows.sort(key=lambda r: r["created"], reverse=True)
    kept = [r for r in rows if r["decision"] in ("active", "superseded") and r["before"] is not None and r["after"] is not None]
    uplift = round(sum(r["after"] - r["before"] for r in kept) / len(kept), 3) if kept else None
    imp = {r["_id"]: r["n"] for r in db().implicit_signals.aggregate([{"$group": {"_id": "$kind", "n": {"$sum": 1}}}])}
    signals = {"answers": db().episodes.count_documents({"source": "chat"}), "implicit": imp,
               "intents": db().intent_events.count_documents({"intent": {"$ne": "new_task"}}),
               "agent_cycles": db().agent_cycles.count_documents({}),
               "ratings": db().feedback.count_documents({}),
               "thumbs_down": db().feedback.count_documents({"good": False}),
               "suggestions": db().recommendations.count_documents({}),
               "improvement_requests": db().improvement_requests.count_documents({}),
               "follow_up_intents": db().intent_events.count_documents({"intent": {"$ne": "new_task"}}),
               "quarantined": db().recommendations.count_documents({"status": "quarantined"}),
               "cycles": db().cycles.count_documents({"status": "done"}),
               "decisions_reviewed_in_cycles": sum(c.get("totals", {}).get("decisions", 0) for c in db().cycles.find({"status": "done"})),
               "graded_eval_cases": _eval_case_count()}
    return {"outcomes": dict(outcome), "total": len(rows), "avg_uplift_kept": uplift, "signals": signals,
            "first_time_right": first_time_right(),
            "trend": sorted(trend, key=lambda t: (t["now"] or 0) - (t["start"] or 0), reverse=True), "decisions": rows[:60]}


def _eval_case_count() -> int:
    from . import evals

    return len(evals.cases()) + len(evals.agent_cases())


def components() -> list[dict[str, Any]]:
    """The local stack and how much each part is exercised (no hosted services)."""
    u = usage.totals()
    d = db()
    return [
        {"component": "Document store (SQLite)", "status": "in use", "depth": "core",
         "how": "System of record for everything: chunks + vectors, ontology graph, agents, playbook versions, "
                "context layers, memory, feedback, cycles, governance, audit. One local file.",
         "features": ["exact vector search with the ACL as a pre-filter", "BM25 + dense hybrid retrieval",
                      "breadth-first ontology traversal", "polling change streams → live SSE", "TTL locks",
                      "versioned config documents", "hash-chained audit"],
         "counts": {"collections": len(d.list_collection_names()), "vector searches": u.get("vector_searches", 0),
                    "graph lookups": u.get("graph_lookups", 0), "hybrid queries": u.get("hybrid_queries", 0),
                    "memory recalls": u.get("memory_recalls", 0), "change streams": u.get("change_streams", 0),
                    "audit events": d.audit.estimated_document_count()}},
        {"component": "Embeddings", "status": embed.describe(), "depth": "core",
         "how": "Local embeddings for every document, memory, lesson and failure; local dense + BM25 reranking "
                "as a tunable the improver can switch on.",
         "counts": {"embed calls": u.get("embed_calls", 0), "texts embedded": u.get("texts_embedded", 0),
                    "playbooks with rerank on": d.harness.count_documents({"status": "active", "config.retrieval.rerank": True})}},
        {"component": "Behaviour signals", "status": "in use", "depth": "learning",
         "how": "Thumbs, rephrasing, corrections, regenerations, copies and classified follow-ups, recorded per answer; "
                "they tell each agent's 7-day cycle where it is failing.",
         "counts": {"answers": d.episodes.count_documents({"source": "chat"}),
                    "implicit signals": d.implicit_signals.estimated_document_count(),
                    "ratings": d.feedback.estimated_document_count(),
                    "classified follow-ups": d.intent_events.estimated_document_count()}},
        {"component": "Model gateway", "status": "in use" if llm.available() else "offline", "depth": "core",
         "how": "Answers, the improver, the judge and the cycle analyst all call named model profiles "
                "(local models by default); private data only goes to private models.",
         "counts": {"model calls": u.get("llm_calls", 0)}},
    ]



def first_time_right(user_id: str | None = None, days: int = 7) -> dict[str, Any]:
    """Share of answers people did not have to rephrase, correct, regenerate or rate down: the outcome that matters."""
    import datetime as dt

    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    flt: dict[str, Any] = {"source": "chat", "ts": {"$gte": since}}
    if user_id:
        flt["user_id"] = user_id
    eps = list(db().episodes.find(flt, {"_id": 1, "agent_id": 1}))
    ids = [e["_id"] for e in eps]
    bad = {x["event_id"] for x in db().implicit_signals.find({"event_id": {"$in": ids}, "positive": False})}
    bad |= {f["event_id"] for f in db().feedback.find({"event_id": {"$in": ids}, "good": False})}
    per: dict[str, list[int]] = {}
    for e in eps:
        k = e.get("agent_id") or "assistant"
        per.setdefault(k, [0, 0])
        per[k][0] += 1
        per[k][1] += e["_id"] not in bad
    names = {a["id"]: a["name"] for a in db().agents.find({}, {"id": 1, "name": 1})}
    rows = sorted(({"agent": names.get(k) or ("General assistant" if k == "assistant" else f"{k} (retired agent)"), "answers": n, "first_time_right": round(ok / n, 3)}
                   for k, (n, ok) in per.items() if n), key=lambda r: -r["answers"])
    total = len(eps)
    return {"answers": total, "rate": round((total - len(bad & set(ids))) / total, 3) if total else None, "per_agent": rows[:10]}
