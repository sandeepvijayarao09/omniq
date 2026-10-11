"""Read-only views for the UI: the ontology graph and the Atlas data layer, always as the signed-in user.

Nothing here bypasses access control: objects, links and documents are filtered with the same
policy as the assistant, and samples are shortened, with vectors collapsed and personal fields left out.
"""

from __future__ import annotations

import re
from typing import Any

from .db import db
from .policy import User, can_read, describe, vector_filter

COLLECTIONS: list[tuple[str, str, str]] = [
    # (name, layer, plain-English purpose)
    ("users", "Directory", "Everyone in the company: role, department, team, who they manage."),
    ("chunks", "Knowledge", "Company documents split into passages, each with a Voyage embedding and access labels."),
    ("objects", "Ontology", "Things the company is made of: customers, deals, incidents, systems, people, teams."),
    ("links", "Ontology", "How those things connect (owns, impacted, deal for, reports to…), walked with $graphLookup."),
    ("agents", "Agents", "The 9 job-specific agent templates: who may use them, persona, focus, tools."),
    ("harness", "Self-improvement", "Every version of every playbook, with its parent, change, evidence and scores."),
    ("evolve_runs", "Self-improvement", "Step-by-step log of each improvement run, streamed live via a change stream."),
    ("episodes", "Memory", "Every answer given (tokenized), plus embedded failures the improver learns from."),
    ("lessons", "Memory", "What the improver learned works or doesn't, recalled with vector search."),
    ("agent_memory", "Memory", "Per-agent memory: each user's preferences, agent-wide lessons, remembered facts."),
    ("conversations", "Memory", "The last few turns of each conversation, for follow-up questions."),
    ("recommendations", "Feedback", "Users' 'do this instead' suggestions waiting for, or handled by, the improver."),
    ("feedback", "Feedback", "Thumbs up and down, per version, which the regression watchdog reads."),
    ("scope_policy", "Governance", "Freeze switches, auto-pause state and run budgets per playbook."),
    ("locks", "Governance", "One improvement run per playbook at a time (expires automatically via a TTL index)."),
    ("audit", "Audit", "Tamper-evident, hash-chained log of every question and every change."),
]

HIDE_FIELDS = {"embedding", "email", "phone", "answer", "question", "text_raw"}


# ---------------------------------------------------------------- ontology

def _visible(user: User, obj: dict[str, Any]) -> bool:
    return can_read(user, obj)


def ontology_types(user: User) -> list[dict[str, Any]]:
    out: dict[str, int] = {}
    for o in db().objects.find({}, {"type": 1, "category": 1}):
        if _visible(user, o):
            out[o["type"]] = out.get(o["type"], 0) + 1
    return [{"type": t, "count": n} for t, n in sorted(out.items(), key=lambda kv: -kv[1])]


def ontology_list(user: User, type_: str = "", q: str = "", limit: int = 60) -> list[dict[str, Any]]:
    flt: dict[str, Any] = {}
    if type_:
        flt["type"] = type_
    if q:
        flt["name"] = {"$regex": re.escape(q[:60]), "$options": "i"}
    rows = []
    for o in db().objects.find(flt, {"_id": 0}).sort("name", 1).limit(400):
        if _visible(user, o):
            rows.append({"id": o["id"], "name": o["name"], "type": o["type"], "category": o.get("category")})
        if len(rows) >= limit:
            break
    return rows


def ontology_object(user: User, oid: str) -> dict[str, Any]:
    o = db().objects.find_one({"id": oid}, {"_id": 0})
    if not o or not _visible(user, o):
        raise KeyError(oid)
    res = list(db().objects.aggregate([
        {"$match": {"id": oid}},
        {"$graphLookup": {"from": "links", "startWith": "$id", "connectFromField": "dst", "connectToField": "src",
                          "as": "out", "maxDepth": 0}},
        {"$graphLookup": {"from": "links", "startWith": "$id", "connectFromField": "src", "connectToField": "dst",
                          "as": "in", "maxDepth": 0}},
    ]))[0]
    ids = {l["dst"] for l in res["out"]} | {l["src"] for l in res["in"]}
    names = {x["id"]: x for x in db().objects.find({"id": {"$in": list(ids)}}, {"_id": 0})}
    links, hidden = [], 0
    for l in res["out"]:
        other = names.get(l["dst"])
        if other and _visible(user, other):
            links.append({"dir": "out", "type": l["type"], "id": other["id"], "name": other["name"], "otype": other["type"]})
        elif other:
            hidden += 1
    for l in res["in"]:
        other = names.get(l["src"])
        if other and _visible(user, other):
            links.append({"dir": "in", "type": l["type"], "id": other["id"], "name": other["name"], "otype": other["type"]})
        elif other:
            hidden += 1
    # documents that mention it, only those this user may read
    docs, seen = [], set()
    pat = re.escape(o["name"])
    for c in db().chunks.find({"text": {"$regex": pat, "$options": "i"}},
                              {"doc_id": 1, "title": 1, "category": 1, "team": 1, "owner": 1}).limit(80):
        if c["doc_id"] not in seen and can_read(user, c):
            seen.add(c["doc_id"])
            docs.append({"doc_id": c["doc_id"], "title": c["title"], "category": c["category"]})
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for l in links:  # one chip per neighbour, listing every relationship
        key = (l["dir"], l["id"])
        if key in merged:
            merged[key]["type"] += f" & {l['type']}"
        else:
            merged[key] = dict(l)
    links = list(merged.values())
    props = {k: v for k, v in (o.get("props") or {}).items() if k not in HIDE_FIELDS}
    return {"id": o["id"], "name": o["name"], "type": o["type"], "category": o.get("category"),
            "aliases": o.get("aliases", []), "props": props, "links": links, "hidden_links": hidden,
            "documents": docs[:8],
            "query": {"$graphLookup": {"from": "links", "startWith": "$id", "connectFromField": "dst",
                                       "connectToField": "src", "maxDepth": 0}}}


# ---------------------------------------------------------------- data layer

def _sample(name: str, user: User) -> dict[str, Any] | None:
    coll = db()[name]
    if name == "chunks":
        doc = next((d for d in coll.find({"category": "general"}).limit(1)), None)
    elif name == "users":
        doc = coll.find_one({"id": user.id})
    elif name in ("agent_memory", "conversations", "feedback", "recommendations"):
        doc = coll.find_one({"user_id": user.id}, sort=[("_id", -1)])  # only your own
    else:
        doc = coll.find_one(sort=[("_id", -1)])
    if not doc:
        return None
    return _shorten(doc)


def _shorten(v: Any, depth: int = 0) -> Any:
    if isinstance(v, dict):
        out = {}
        for k, x in list(v.items())[:14]:
            if k == "embedding" and isinstance(x, list):
                out[k] = f"[{len(x)} floats from Voyage]"
            elif k in ("email", "phone"):
                out[k] = "(hidden)"
            else:
                out[k] = _shorten(x, depth + 1)
        return out
    if isinstance(v, list):
        return [_shorten(x, depth + 1) for x in v[:4]] + ([f"… {len(v) - 4} more"] if len(v) > 4 else [])
    if isinstance(v, str):
        return v if len(v) <= 220 else v[:220] + "…"
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v if isinstance(v, (int, float, bool)) or v is None else str(v)


def data_layer(user: User) -> dict[str, Any]:
    d = db()
    present = set(d.list_collection_names())
    rows = []
    for name, layer, purpose in COLLECTIONS:
        if name not in present:
            continue
        coll = d[name]
        idx = [{"name": i["name"], "keys": dict(i["key"]), **({"ttl_seconds": i["expireAfterSeconds"]} if "expireAfterSeconds" in i else {}),
                **({"unique": True} if i.get("unique") else {})} for i in coll.list_indexes()]
        try:
            sidx = [{"name": s["name"], "type": s.get("type", "search"), "status": s.get("status"),
                     "queryable": s.get("queryable"),
                     "fields": [{"path": f.get("path"), "type": f.get("type"),
                                 **({"dims": f["numDimensions"], "similarity": f.get("similarity")} if f.get("type") == "vector" else {})}
                                for f in (s.get("latestDefinition") or {}).get("fields", [])]}
                    for s in coll.list_search_indexes()]
        except Exception:
            sidx = []
        rows.append({"name": name, "layer": layer, "purpose": purpose, "count": coll.estimated_document_count(),
                     "indexes": idx, "search_indexes": sidx, "sample": _sample(name, user)})
    return {"database": d.name, "you": {"name": user.name, "role": user.role, "may_read": describe(user)},
            "your_vector_filter": vector_filter(user), "collections": rows}
