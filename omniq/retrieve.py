"""Access-filtered retrieval. All knobs come from the active harness.

- Dense: exact vector search with the ACL as a *pre-filter* (unauthorized chunks are never ranked).
- Hybrid: reciprocal-rank fusion of dense search and BM25 over the same ACL-filtered pool.
- Rerank: local fusion of dense and lexical scores (embed.rerank).
- Mention boost (ontology.mention_boost): documents that mention an entity named in the question move up.
- Ontology facts: ontology.py (object, property and link ACL)."""

from __future__ import annotations

import re
from typing import Any

from . import embed
from .db import EPISODE_INDEX, LESSON_INDEX, VECTOR_INDEX, db
from .policy import User, can_read, vector_filter


def _vsearch(user: User, qvec: list[float], cfg: dict[str, Any], limit: int, focus: list[str] | None):
    from . import usage

    usage.bump("vector_searches")
    pipeline = [
        {"$vectorSearch": {"index": VECTOR_INDEX, "path": "embedding", "queryVector": qvec,
                           "numCandidates": max(int(cfg["retrieval"]["num_candidates"]), limit * 10),
                           "limit": limit, "filter": vector_filter(user, focus)}},
        {"$project": {"embedding": 0, "score": {"$meta": "vectorSearchScore"}}},
    ]
    return [h for h in db().chunks.aggregate(pipeline) if can_read(user, h)]  # second line of defence


def _hybrid(user: User, query: str, qvec: list[float], cfg: dict[str, Any], limit: int, focus: list[str] | None):
    """Reciprocal-rank fusion of dense and BM25 keyword results over the same ACL-filtered pool.
    Exact identifiers ("ADR-007", "SEV1", "$42,000") that embeddings blur are caught by the keyword side."""
    from . import usage
    from .lexical import BM25

    usage.bump("hybrid_queries")
    dense = _vsearch(user, qvec, cfg, limit * 2, focus)
    pool = [c for c in db().chunks.find(vector_filter(user, focus), {"embedding": 0}) if can_read(user, c)]
    if not pool:
        return dense[:limit]
    lex = BM25([f"{c.get('title', '')} {c['text']}" for c in pool]).scores(query)
    text_rank = sorted(range(len(pool)), key=lambda i: -lex[i])[: limit * 2]
    fused: dict[tuple[str, int], list[Any]] = {}
    for r, h in enumerate(dense):  # weights as before: vector 1.0, text 0.7, RRF constant 60
        fused.setdefault((h["doc_id"], h["chunk"]), [0.0, h])[0] += 1.0 / (60 + r)
    for r, i in enumerate(text_rank):
        if lex[i] <= 0:
            continue
        c = pool[i]
        fused.setdefault((c["doc_id"], c["chunk"]), [0.0, c])[0] += 0.7 / (60 + r)
    ranked = sorted(fused.values(), key=lambda x: -x[0])[:limit]
    return [dict(h, score=s) for s, h in ranked]


def search_docs(user: User, query: str, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Agent focus first, then the rest of what the user may read to fill any gap."""
    r = cfg["retrieval"]
    if r.get("hybrid"):
        try:
            return _search_with(_hybrid, user, query, cfg)
        except Exception:  # noqa: BLE001  (hybrid is an optimization; dense search is always available)
            pass
    return _search_with(None, user, query, cfg)


def _search_with(fn, user: User, query: str, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    from . import ontology

    r = cfg["retrieval"]
    top_k = int(r["top_k"])
    boost = bool(ontology.settings(cfg)["mention_boost"])
    fetch = top_k * 4 if r.get("rerank") or boost else top_k  # a boost needs candidates below the cut to promote
    qvec = embed.embed_one(query)
    focus = (cfg.get("agent") or {}).get("focus_categories") or None
    run = (lambda f: fn(user, query, qvec, cfg, fetch, f)) if fn else (lambda f: _vsearch(user, qvec, cfg, fetch, f))
    hits = run(focus)
    if focus and len(hits) < fetch:
        seen = {(h["doc_id"], h["chunk"]) for h in hits}
        hits += [h for h in run(None) if (h["doc_id"], h["chunk"]) not in seen][: fetch - len(hits)]
    if r.get("rerank") and hits:
        order = embed.rerank(query, [h["text"] for h in hits], len(hits) if boost else top_k)
        hits = [dict(hits[i], score=s) for i, s in order]
    if boost:
        hits = ontology.boost(user, query, hits, cfg)
    limit = int(cfg["context"]["max_chunk_chars"])
    for h in hits:
        h["text"] = h["text"][:limit]
        h.pop("_id", None)
    return hits[:top_k]


def link_entities(text: str) -> list[dict[str, Any]]:
    """Find ontology objects named in the text (names and aliases)."""
    low = text.lower()
    found = []
    for o in db().objects.find({"type": {"$nin": ["Person"]}}, {"_id": 0}):
        names = [o["name"].lower(), *o.get("aliases", [])]
        if any(len(n) > 3 and re.search(rf"\b{re.escape(n)}\b", low) for n in names):
            found.append(o)
    return found[:6]


def recall_lessons(role: str, query: str, k: int = 4) -> list[str]:
    try:
        rows = db().lessons.aggregate([
            {"$vectorSearch": {"index": LESSON_INDEX, "path": "embedding", "queryVector": embed.embed_one(query),
                               "numCandidates": 50, "limit": k, "filter": {"status": {"$eq": "adopted"}}}},
            {"$project": {"_id": 0, "text": 1}},
        ])
        return [r["text"] for r in rows]
    except Exception:
        return []


def similar_failures(role: str, text: str, k: int = 5) -> list[dict[str, Any]]:
    try:
        return list(db().episodes.aggregate([
            {"$vectorSearch": {"index": EPISODE_INDEX, "path": "embedding", "queryVector": embed.embed_one(text),
                               "numCandidates": 100, "limit": k,
                               "filter": {"$and": [{"role": {"$eq": role}}, {"failed": {"$eq": True}}]}}},
            {"$project": {"_id": 0, "question": 1, "failure": 1, "harness_version": 1}},
        ]))
    except Exception:
        return []


def project_context(user: User, query: str, have: set[tuple[str, int]], k: int = 2,
                    min_sim: float | None = None) -> list[dict[str, Any]]:
    """Up to k extra passages from documents linked to the asker's projects (data/projects.yaml), ranked by
    similarity to the question. Every passage still has to pass the person's access control."""
    from . import iam

    from .calibration import threshold

    min_sim = threshold("project_doc") if min_sim is None else min_sim
    doc_ids = sorted({d for p in iam.user_projects(user.id) for d in p.get("docs", [])})
    if not doc_ids:
        return []
    q = embed.embed_one(query)
    scored = []
    for c in db().chunks.find({"doc_id": {"$in": doc_ids}}):
        if (c["doc_id"], c["chunk"]) in have or not can_read(user, c):
            continue
        sim = sum(a * b for a, b in zip(q, c["embedding"]))
        if sim >= min_sim:
            scored.append((sim, c))
    scored.sort(key=lambda x: -x[0])
    names = {p["id"]: p["name"] for p in iam.user_projects(user.id)}
    out = []
    for sim, c in scored[:k]:
        proj = next((names[p["id"]] for p in iam.user_projects(user.id) if c["doc_id"] in p.get("docs", [])), "")
        out.append({"title": f"{c['title']} (your project: {proj})", "text": c["text"], "doc_id": c["doc_id"],
                    "chunk": c["chunk"], "score": round(sim, 3)})
    return out
