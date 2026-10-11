"""Access-filtered retrieval: $vectorSearch with the ACL as a pre-filter, Voyage rerank,
and ontology context via $graphLookup. All knobs come from the active harness."""

from __future__ import annotations

import re
from typing import Any

from . import embed
from .db import EPISODE_INDEX, LESSON_INDEX, VECTOR_INDEX, db
from .policy import User, can_read, vector_filter


def _vsearch(user: User, qvec: list[float], cfg: dict[str, Any], limit: int, focus: list[str] | None):
    from . import usage

    usage.bump("atlas_vector_searches")
    pipeline = [
        {"$vectorSearch": {"index": VECTOR_INDEX, "path": "embedding", "queryVector": qvec,
                           "numCandidates": max(int(cfg["retrieval"]["num_candidates"]), limit * 10),
                           "limit": limit, "filter": vector_filter(user, focus)}},
        {"$project": {"embedding": 0, "score": {"$meta": "vectorSearchScore"}}},
    ]
    return [h for h in db().chunks.aggregate(pipeline) if can_read(user, h)]  # second line of defence


def _search_acl(user: User, focus: list[str] | None) -> dict[str, Any]:
    """The same access rule as vector_filter(), expressed for Atlas Search ($search compound filter)."""
    cats = sorted(user.open_categories() & set(focus) if focus else user.open_categories()) or ["__none__"]
    should: list[dict[str, Any]] = [{"in": {"path": "category", "value": cats}}]
    if user.hr_scope() and (not focus or "hr" in focus):
        should.append({"compound": {"must": [{"equals": {"path": "category", "value": "hr"}},
                                             {"in": {"path": "team", "value": list(user.hr_scope())}}]}})
    should.append({"compound": {"must": [{"equals": {"path": "category", "value": "personal"}},
                                         {"equals": {"path": "owner", "value": user.id}}]}})
    return {"compound": {"should": should, "minimumShouldMatch": 1}}


def _hybrid(user: User, query: str, qvec: list[float], cfg: dict[str, Any], limit: int, focus: list[str] | None):
    """Reciprocal-rank fusion of semantic ($vectorSearch) and keyword ($search) results in one Atlas query.
    Exact identifiers ("ADR-007", "SEV1", "$42,000") that embeddings blur are caught by the keyword side."""
    from . import usage

    usage.bump("atlas_rankfusion_queries")
    pipeline = [
        {"$rankFusion": {"input": {"pipelines": {
            "vector": [{"$vectorSearch": {"index": VECTOR_INDEX, "path": "embedding", "queryVector": qvec,
                                          "numCandidates": max(int(cfg["retrieval"]["num_candidates"]), limit * 10),
                                          "limit": limit * 2, "filter": vector_filter(user, focus)}}],
            "text": [{"$search": {"index": "chunks_text", "compound": {
                "must": [{"text": {"query": query, "path": ["text", "title"]}}],
                "filter": [_search_acl(user, focus)]}}}, {"$limit": limit * 2}]}},
            "combination": {"weights": {"vector": 1, "text": 0.7}}}},
        {"$limit": limit},
        {"$project": {"embedding": 0}},
    ]
    return [h for h in db().chunks.aggregate(pipeline) if can_read(user, h)]  # second line of defence


def search_docs(user: User, query: str, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Agent focus first, then the rest of what the user may read to fill any gap."""
    r = cfg["retrieval"]
    if r.get("hybrid"):
        try:
            return _search_with(_hybrid, user, query, cfg)
        except Exception:
            pass  # index still building or unsupported: fall back to vector-only
    return _search_with(None, user, query, cfg)


def _search_with(fn, user: User, query: str, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    r = cfg["retrieval"]
    top_k = int(r["top_k"])
    fetch = top_k * 4 if r.get("rerank") else top_k
    qvec = embed.embed_one(query)
    focus = (cfg.get("agent") or {}).get("focus_categories") or None
    run = (lambda f: fn(user, query, qvec, cfg, fetch, f)) if fn else (lambda f: _vsearch(user, qvec, cfg, fetch, f))
    hits = run(focus)
    if focus and len(hits) < fetch:
        seen = {(h["doc_id"], h["chunk"]) for h in hits}
        hits += [h for h in run(None) if (h["doc_id"], h["chunk"]) not in seen][: fetch - len(hits)]
    if r.get("rerank") and hits:
        order = embed.rerank(query, [h["text"] for h in hits], top_k)
        hits = [dict(hits[i], score=s) for i, s in order]
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


def graph_context(user: User, query: str) -> list[dict[str, Any]]:
    """One hop around linked entities with $graphLookup, filtered by object category."""
    from . import usage

    seeds = [o for o in link_entities(query) if can_read(user, o)]
    usage.bump("atlas_graphlookups", 2 * len(seeds))
    out = []
    for o in seeds:
        res = list(db().objects.aggregate([
            {"$match": {"id": o["id"]}},
            {"$graphLookup": {"from": "links", "startWith": "$id", "connectFromField": "dst",
                              "connectToField": "src", "as": "out", "maxDepth": 0}},
            {"$graphLookup": {"from": "links", "startWith": "$id", "connectFromField": "src",
                              "connectToField": "dst", "as": "in", "maxDepth": 0}},
        ]))
        if not res:
            continue
        ids = {l["dst"] for l in res[0]["out"]} | {l["src"] for l in res[0]["in"]}
        names = {x["id"]: x for x in db().objects.find({"id": {"$in": list(ids)}}, {"_id": 0})}
        rels = []
        for l in res[0]["out"]:
            if l["dst"] in names and can_read(user, names[l["dst"]]):
                rels.append(f"{l['type']} → {names[l['dst']]['name']}")
        for l in res[0]["in"]:
            if l["src"] in names and can_read(user, names[l["src"]]):
                rels.append(f"{names[l['src']]['name']} {l['type']} →")
        props = "; ".join(f"{k}: {v}" for k, v in o.get("props", {}).items())
        out.append({"title": f"Ontology: {o['type']} {o['name']}",
                    "text": f"{o['type']} {o['name']}. {props}. Links: {', '.join(rels[:12])}"})
    return out


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
                    min_sim: float = 0.3) -> list[dict[str, Any]]:
    """Up to k extra passages from documents linked to the asker's projects (data/projects.yaml), ranked by
    similarity to the question. Every passage still has to pass the person's access control."""
    from . import iam

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
