"""The context layer in MongoDB Atlas: every role, person and agent Markdown file as versioned documents.

Collection `context_layers`
  {kind: role|person|agent, key, version, markdown, meta, status: active|superseded, source, reason, created}

Files in data/ are the seed: sync_from_files() stores v1, and a new version whenever a file changes on disk.
The 7-day cycle writes new versions of a person's context (source "cycle") with the reason it changed.
Readers get the active version; the history is kept, so any change can be inspected or rolled back.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import time
from pathlib import Path
from typing import Any

import yaml
from .docstore import ASCENDING, DESCENDING

from .config import ROOT
from .db import db

SOURCES = {"role": ROOT / "data" / "roles", "person": ROOT / "data" / "people", "agent": ROOT / "data" / "agents"}
_cache: dict[tuple[str, str], tuple[float, dict[str, Any] | None]] = {}
TTL = 5.0


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _split(raw: str) -> tuple[dict[str, Any], str]:
    _, fm, body = raw.split("---", 2)
    return yaml.safe_load(fm) or {}, body.strip()


def ensure_indexes() -> None:
    c = db().context_layers
    c.create_index([("kind", ASCENDING), ("key", ASCENDING), ("version", DESCENDING)], unique=True)
    c.create_index([("kind", ASCENDING), ("key", ASCENDING), ("status", ASCENDING)])


def sync_from_files() -> dict[str, int]:
    """Store each file as v1, or as a new version if its content changed since the last file-sourced version."""
    ensure_indexes()
    added = {"role": 0, "person": 0, "agent": 0}
    for kind, folder in SOURCES.items():
        pattern = "*/*.md" if kind == "agent" else "*.md"
        for path in sorted(folder.glob(pattern)):
            meta, body = _split(path.read_text())
            key = meta.get("id") or meta.get("org_role") or path.stem
            digest = hashlib.sha256(body.encode()).hexdigest()
            last_file = db().context_layers.find_one({"kind": kind, "key": key, "source": "file"}, sort=[("version", -1)])
            if last_file and last_file.get("digest") == digest:
                continue
            _write(kind, key, body, meta, source="file", reason=f"loaded from {path.relative_to(ROOT)}",
                   digest=digest, file=str(path.relative_to(ROOT)))
            added[kind] += 1
    _cache.clear()
    return added


def _write(kind: str, key: str, markdown: str, meta: dict[str, Any], source: str, reason: str, **extra: Any) -> dict[str, Any]:
    c = db().context_layers
    last = c.find_one({"kind": kind, "key": key}, sort=[("version", -1)])
    version = (last["version"] + 1) if last else 1
    c.update_many({"kind": kind, "key": key, "status": "active"}, {"$set": {"status": "superseded"}})
    doc = {"kind": kind, "key": key, "version": version, "markdown": markdown, "meta": meta, "status": "active",
           "source": source, "reason": reason, "created": _now(), **extra}
    c.insert_one(doc)
    _cache.pop((kind, key), None)
    doc.pop("_id", None)
    return doc


def active(kind: str, key: str) -> dict[str, Any] | None:
    hit = _cache.get((kind, key))
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    try:
        doc = db().context_layers.find_one({"kind": kind, "key": key, "status": "active"}, {"_id": 0})
    except Exception:
        doc = None
    _cache[(kind, key)] = (time.time(), doc)
    return doc


def history(kind: str, key: str) -> list[dict[str, Any]]:
    return list(db().context_layers.find({"kind": kind, "key": key}, {"_id": 0, "markdown": 0}).sort("version", -1))


def update(kind: str, key: str, markdown: str, reason: str, source: str = "cycle", **extra: Any) -> dict[str, Any]:
    cur = active(kind, key) or {"meta": {}}
    return _write(kind, key, markdown, cur.get("meta", {}), source=source, reason=reason, **extra)


def rollback(kind: str, key: str, version: int) -> None:
    c = db().context_layers
    if not c.find_one({"kind": kind, "key": key, "version": version}):
        raise KeyError(f"{kind}:{key} v{version}")
    c.update_many({"kind": kind, "key": key, "status": "active"}, {"$set": {"status": "superseded"}})
    c.update_one({"kind": kind, "key": key, "version": version}, {"$set": {"status": "active"}})
    _cache.pop((kind, key), None)
