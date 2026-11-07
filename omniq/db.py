"""The local document store: one SQLite file holds the whole organization layer.

Collections
  users            directory (role, department, team, manages)
  chunks           document chunks + local embeddings + ACL fields (pre-filtered exact vector search)
  objects, links   the ontology graph (schema: config/ontology.yaml); document `mentions` links carry the doc's ACL
  ontology_edits   the ontology's action log, replayed over the source records on every seed
  harness          versioned harness documents per role (the thing that evolves)
  episodes         every assistant run: tokenized I/O, retrieved ids, score (episodic memory)
  lessons          learned rules with embeddings (procedural memory)
  memories         per-user facts (semantic memory)
  feedback         thumbs up/down from employees
  agents           role-specific agent templates
  agent_memory     per-agent memory: user preferences, agent-wide lessons, facts
  conversations    short-term working context per conversation, and its privacy vault (stable tokens)
  recommendations  "what should it do differently?" queue that drives agent evolution
  locks            one evolution run per harness scope at a time (TTL)
  evolve_runs      checkpointed state of each self-improvement run
  audit            hash-chained, append-only event log

See ``docstore.py`` for the storage engine.
"""

from __future__ import annotations

import datetime as dt

from .config import settings
from .docstore import ASCENDING, DESCENDING, Database, DuplicateKeyError

# Names kept for call sites that pass an index name to $vectorSearch; exact search ignores them.
VECTOR_INDEX = "chunks_vector"
LESSON_INDEX = "lessons_vector"
EPISODE_INDEX = "episodes_vector"
MEMORY_INDEX = "agent_memory_vector"


_current: Database | None = None


def db() -> Database:
    global _current
    if _current is None:
        _current = Database(settings.db_path)
    return _current


def use(database: Database) -> Database:
    """Point the process at a different store (tests and experiments use isolated files)."""
    global _current
    _current = database
    return database


def ensure_indexes() -> None:
    d = db()
    d.users.create_index("id", unique=True)
    d.objects.create_index("id", unique=True)
    d.links.create_index([("src", ASCENDING), ("type", ASCENDING)])
    d.links.create_index("dst")
    d.harness.create_index([("role", ASCENDING), ("version", DESCENDING)], unique=True)
    d.audit.create_index("seq", unique=True)
    d.episodes.create_index([("ts", DESCENDING)])
    d.recommendations.create_index([("scope", ASCENDING), ("status", ASCENDING), ("ts", ASCENDING)])
    d.agent_memory.create_index([("agent_id", ASCENDING), ("user_id", ASCENDING), ("kind", ASCENDING)])
    d.locks.create_index("expires", expireAfterSeconds=0)


class Locked(RuntimeError):
    pass


def acquire(name: str, ttl_seconds: int = 420) -> None:
    """A lock that expires (TTL), so a crashed run can't hold it forever."""
    now = dt.datetime.now(dt.timezone.utc)
    db().locks.delete_many({"_id": name, "expires": {"$lt": now}})
    try:
        db().locks.insert_one({"_id": name, "expires": now + dt.timedelta(seconds=ttl_seconds)})
    except DuplicateKeyError:
        raise Locked(f"{name} is already being improved")


def release(name: str) -> None:
    db().locks.delete_one({"_id": name})
