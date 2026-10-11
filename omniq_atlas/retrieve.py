"""Access-filtered retrieval: $vectorSearch with the ACL as a pre-filter, Voyage rerank,
and ontology context via $graphLookup. All knobs come from the active harness."""

from __future__ import annotations

import re
from typing import Any

from . import embed
from .db import EPISODE_INDEX, LESSON_INDEX, VECTOR_INDEX, db
from .policy import User, can_read, vector_filter


def _vsearch(user: User, qvec: list[float], cfg: dict[str, Any], limit: int, focus: list[str] | None):
    pipeline = [
        {"$vectorSearch": {"index": VECTOR_INDEX, "path": "embedding", "queryVector": qvec,
                           "numCandidates": max(int(cfg["retrieval"]["num_candidates"]), limit * 10),
                           "limit": limit, "filter": vector_filter(user, focus)}},
        {"$project": {"embedding": 0, "score": {"$meta": "vectorSearchScore"}}},
    ]
    return [h for h in db().chunks.aggregate(pipeline) if can_read(user, h)]  # second line of defence


def search_docs(user: User, query: str, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Agent focus first, then the rest of what the user may read to fill any gap."""
    r = cfg["retrieval"]
    top_k = int(r["top_k"])
    fetch = top_k * 4 if r.get("rerank") else top_k
    qvec = embed.embed_one(query)
    focus = (cfg.get("agent") or {}).get("focus_categories") or None
    hits = _vsearch(user, qvec, cfg, fetch, focus)
    if focus and len(hits) < fetch:
        seen = {(h["doc_id"], h["chunk"]) for h in hits}
        hits += [h for h in _vsearch(user, qvec, cfg, fetch, None) if (h["doc_id"], h["chunk"]) not in seen][: fetch - len(hits)]
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
    seeds = [o for o in link_entities(query) if can_read(user, o)]
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
