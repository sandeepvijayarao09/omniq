"""MongoDB Atlas: one database holds the whole organization layer.

Collections
  users            directory (role, department, team, manages)
  chunks           document chunks + Voyage embeddings + ACL fields (vector index)
  objects, links   the ontology graph, traversed with $graphLookup
  harness          versioned harness documents per role (the thing that evolves)
  episodes         every assistant run: tokenized I/O, retrieved ids, score (episodic memory)
  lessons          learned rules with embeddings (procedural memory)
  memories         per-user facts (semantic memory)
  feedback         thumbs up/down from employees
  evolve_runs      checkpointed state of each self-improvement run
  audit            hash-chained, append-only event log
"""

from __future__ import annotations

import time
from functools import lru_cache

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.database import Database
from pymongo.operations import SearchIndexModel

from .config import settings

VECTOR_INDEX = "chunks_vector"
LESSON_INDEX = "lessons_vector"
EPISODE_INDEX = "episodes_vector"


@lru_cache(maxsize=1)
def client() -> MongoClient:
    return MongoClient(settings.mongodb_uri, appname="omniq-atlas", serverSelectionTimeoutMS=10000)


def db() -> Database:
    return client()[settings.mongodb_db]


def _vector_index(name: str, filters: list[str]) -> SearchIndexModel:
    fields = [{"type": "vector", "path": "embedding", "numDimensions": settings.embed_dim, "similarity": "cosine"}]
    fields += [{"type": "filter", "path": f} for f in filters]
    return SearchIndexModel(definition={"fields": fields}, name=name, type="vectorSearch")


def ensure_indexes(wait: bool = True) -> None:
    d = db()
    for name in ("chunks", "lessons", "episodes", "objects", "links", "harness", "audit"):
        if name not in d.list_collection_names():
            d.create_collection(name)
    d.users.create_index("id", unique=True)
    d.objects.create_index("id", unique=True)
    d.links.create_index([("src", ASCENDING), ("type", ASCENDING)])
    d.links.create_index("dst")
    d.harness.create_index([("role", ASCENDING), ("version", DESCENDING)], unique=True)
    d.audit.create_index("seq", unique=True)
    d.episodes.create_index([("ts", DESCENDING)])

    wanted = {
        "chunks": _vector_index(VECTOR_INDEX, ["category", "team", "owner"]),
        "lessons": _vector_index(LESSON_INDEX, ["role", "status"]),
        "episodes": _vector_index(EPISODE_INDEX, ["role", "failed"]),
    }
    for coll, model in wanted.items():
        existing = {ix["name"] for ix in d[coll].list_search_indexes()}
        if model.document["name"] not in existing:
            d[coll].create_search_index(model)
    if wait:
        _wait_ready([(c, m.document["name"]) for c, m in wanted.items()])


def _wait_ready(pairs: list[tuple[str, str]], timeout: float = 180) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        pending = [
            (c, n) for c, n in pairs
            if not any(ix.get("queryable") for ix in db()[c].list_search_indexes(n))
        ]
        if not pending:
            return
        time.sleep(2)
    raise TimeoutError(f"search indexes not ready: {pending}")
