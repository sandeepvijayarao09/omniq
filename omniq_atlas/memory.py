"""The per-agent context and memory layer, all in Atlas.

agent_memory   one collection, vector-indexed, filtered by agent and owner:
  kind=preference  user-scoped: "for me, always show ARR in $K" — applied to that user immediately
  kind=lesson      agent-wide (user_id "*"): adopted by the improvement loop after it proved itself
  kind=fact        user-scoped facts from /remember
conversations  short-term working context: the last turns of a conversation
recommendations the queue of "what should it do differently?" feedback that drives agent evolution

Everything here is stored raw inside the Atlas boundary (like the documents) and is tokenized
by the assistant on the way out, exactly like retrieved passages.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from . import embed
from .db import MEMORY_INDEX, db

GLOBAL = "*"
MAX_PREFS_PER_USER = 25


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def add(agent_id: str, user_id: str, kind: str, text: str, source: str = "", **extra: Any) -> str:
    text = text.strip()[:500]
    doc = {"agent_id": agent_id, "user_id": user_id, "kind": kind, "text": text, "status": "active",
           "source": source, "ts": _now(), "uses": 0, "embedding": embed.embed_one(text, "document"), **extra}
    if kind == "preference":
        # cap per user per agent: retire the oldest instead of growing without bound
        n = db().agent_memory.count_documents({"agent_id": agent_id, "user_id": user_id, "kind": kind, "status": "active"})
        if n >= MAX_PREFS_PER_USER:
            oldest = db().agent_memory.find_one({"agent_id": agent_id, "user_id": user_id, "kind": kind,
                                                 "status": "active"}, sort=[("ts", 1)])
            db().agent_memory.update_one({"_id": oldest["_id"]}, {"$set": {"status": "retired"}})
    return str(db().agent_memory.insert_one(doc).inserted_id)


def recall(agent_id: str, user_id: str, query: str, k: int = 6) -> list[dict[str, Any]]:
    """Most relevant active memories for this agent: the user's own plus agent-wide lessons."""
    from . import usage

    usage.bump("atlas_vector_searches")
    usage.bump("atlas_memory_recalls")
    try:
        rows = list(db().agent_memory.aggregate([
            {"$vectorSearch": {"index": MEMORY_INDEX, "path": "embedding", "queryVector": embed.embed_one(query),
                               "numCandidates": 100, "limit": k,
                               "filter": {"$and": [{"agent_id": {"$eq": agent_id}},
                                                   {"user_id": {"$in": [user_id, GLOBAL]}},
                                                   {"status": {"$eq": "active"}}]}}},
            {"$project": {"embedding": 0, "score": {"$meta": "vectorSearchScore"}}},
        ]))
    except Exception:
        rows = []
    # preferences are explicit instructions from this user: always include them, even if not similar
    have = {r["_id"] for r in rows}
    rows += [r for r in db().agent_memory.find({"agent_id": agent_id, "user_id": user_id, "kind": "preference",
                                               "status": "active"}, {"embedding": 0}).sort("ts", -1).limit(8)
             if r["_id"] not in have]
    if rows:
        db().agent_memory.update_many({"_id": {"$in": [r["_id"] for r in rows]}}, {"$inc": {"uses": 1}})
    return rows


def list_for(agent_id: str, user_id: str) -> list[dict[str, Any]]:
    return list(db().agent_memory.find({"agent_id": agent_id, "user_id": {"$in": [user_id, GLOBAL]},
                                        "status": "active"}, {"embedding": 0}).sort("ts", -1).limit(50))


def forget(memory_id: Any, user_id: str) -> bool:
    """Users can delete their own memories (never agent-wide lessons)."""
    from bson import ObjectId

    res = db().agent_memory.update_one({"_id": ObjectId(str(memory_id)), "user_id": user_id},
                                       {"$set": {"status": "deleted"}})
    return res.modified_count == 1


# ---- short-term conversation context

def remember_turn(convo_id: str, user_id: str, agent_id: str, question: str, answer: str) -> None:
    if not convo_id:
        return
    db().conversations.update_one(
        {"_id": convo_id, "user_id": user_id},
        {"$push": {"turns": {"$each": [{"q": question[:1000], "a": answer[:4000], "agent": agent_id, "ts": _now()}], "$slice": -40}},
         "$set": {"agent_id": agent_id, "updated": _now()}}, upsert=True)


def recent_turns(convo_id: str, user_id: str, n: int = 3) -> list[dict[str, Any]]:
    if not convo_id:
        return []
    doc = db().conversations.find_one({"_id": convo_id, "user_id": user_id}) or {}
    return doc.get("turns", [])[-n:]


# ---- recommendation queue

def recommend(agent_id: str, scope: str, user_id: str, event_id: str, question: str, answer: str, text: str,
              harness_version: int) -> None:
    db().recommendations.insert_one({
        "agent_id": agent_id, "scope": scope, "user_id": user_id, "event_id": event_id, "question": question,
        "answer": answer[:2000], "text": text.strip()[:500], "harness_version": harness_version,
        "status": "pending", "ts": _now(), "embedding": embed.embed_one(text, "document")})


def pending(scope: str, limit: int = 20) -> list[dict[str, Any]]:
    return list(db().recommendations.find({"scope": scope, "status": "pending"}).sort("ts", 1).limit(limit))


def pending_counts() -> dict[str, int]:
    return {r["_id"]: r["n"] for r in db().recommendations.aggregate([
        {"$match": {"status": "pending"}}, {"$group": {"_id": "$scope", "n": {"$sum": 1}}}])}
