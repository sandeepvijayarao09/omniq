"""Data-driven decision making, made visible: every automatic change, the evidence it was decided on, and
how much each partner technology is actually exercised. All numbers are computed from Atlas."""

from __future__ import annotations

from collections import Counter
from typing import Any

from . import embed, llm, raindrop_read, telemetry, usage
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
    signals = {"answers": db().episodes.count_documents({"source": "chat"}),
               "ratings": db().feedback.count_documents({}),
               "thumbs_down": db().feedback.count_documents({"good": False}),
               "suggestions": db().recommendations.count_documents({}),
               "quarantined": db().recommendations.count_documents({"status": "quarantined"}),
               "cycles": db().cycles.count_documents({"status": "done"}),
               "decisions_reviewed_in_cycles": sum(c.get("totals", {}).get("decisions", 0) for c in db().cycles.find({"status": "done"})),
               "graded_eval_cases": 24 + 60}
    return {"outcomes": dict(outcome), "total": len(rows), "avg_uplift_kept": uplift, "signals": signals,
            "trend": sorted(trend, key=lambda t: (t["now"] or 0) - (t["start"] or 0), reverse=True), "decisions": rows[:60]}


def partners() -> dict[str, Any]:
    u = usage.totals()
    d = db()
    vec = sum(1 for c in ("chunks", "episodes", "lessons", "agent_memory") for ix in d[c].list_search_indexes())
    return [
        {"partner": "MongoDB Atlas", "status": "in use", "depth": "core",
         "how": "System of record for everything: documents + vectors, ontology graph, agents, playbook versions, "
                "context layers, memory, feedback, cycles, governance, audit. Hackathon Sandbox cluster.",
         "features": [f"{vec} Vector Search indexes with ACL filter fields", "$vectorSearch pre-filter compiled from IAM",
                      "$graphLookup ontology traversal", "change streams → live SSE", "TTL index locks",
                      "versioned config documents", "hash-chained audit"],
         "counts": {"collections": len(d.list_collection_names()), "vector searches": u.get("atlas_vector_searches", 0),
                    "graph lookups": u.get("atlas_graphlookups", 0), "memory recalls": u.get("atlas_memory_recalls", 0),
                    "change streams": u.get("atlas_change_streams", 0), "audit events": d.audit.estimated_document_count()},
         "gaps": ["Atlas Agent Engine not used", "no Atlas Search / $rankFusion hybrid yet", "Automated Embeddings not used"]},
        {"partner": "Voyage AI", "status": "in use" if embed.available() else "not configured", "depth": "core",
         "how": "voyage-4 embeddings for every document, memory, lesson and failure (via the Atlas AI Model API key); "
                "rerank-3 available as a tunable the improver can switch on.",
         "counts": {"embed calls": u.get("voyage_embed_calls", 0), "texts embedded": u.get("voyage_texts_embedded", 0),
                    "rerank calls": u.get("voyage_rerank_calls", 0),
                    "playbooks with rerank on": d.harness.count_documents({"status": "active", "config.retrieval.rerank": True})},
         "gaps": ["rerank is off in most active playbooks"]},
        {"partner": "Raindrop", "status": "in use" if telemetry.available() else "not configured", "depth": "telemetry",
         "how": "Every real answer is an event (event id = Atlas episode id); 👍/👎, regenerations, suggestions and "
                "quarantines are signals. The 7-day cycle reads them back with the Query API as independent evidence.",
         "counts": {"events stored in Raindrop (Query API)": raindrop_read.event_total() or 0,
                    "signal types": len(raindrop_read.project_signals().get("signals", [])),
                    "events sent since counters started": u.get("raindrop_events", 0),
                    "signals sent since counters started": u.get("raindrop_signals", 0),
                    "cycles that read Raindrop back": d.cycles.count_documents({"raindrop.status": "ok"})},
         "features": ["write SDK: events + signals", "Query API read-back into the 7-day cycle"
                      + ("" if raindrop_read.available() else " (waiting for RAINDROP_QUERY_API_KEY)")],
         "gaps": [] if raindrop_read.available() else ["add RAINDROP_QUERY_API_KEY so each 7-day cycle reads "
                                                       "Raindrop's events and detected signals back"]},
        {"partner": "Modelence", "status": "deployed", "depth": "deployment",
         "how": "Public URL omniq-mv4a4zfo47x-prod.cloud.modelence.app: a Node edge on Modelence Cloud proxying to the API.",
         "counts": {}, "gaps": ["thin proxy only; the app logic runs on the laptop because the model is local"]},
        {"partner": "Local LLM (Gemma 4 via Ollama)", "status": "in use" if llm.available() else "offline", "depth": "core",
         "how": "Answers, the improver, the judge and the cycle analyst. Employee data never leaves the machine.",
         "counts": {"model calls": u.get("llm_calls", 0)}, "gaps": []},
    ]
