"""Tamper-evident audit log: each event stores the hash of the previous one.

Only tokenized text is written. `verify()` walks the chain and reports the first break.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from pymongo import DESCENDING
from pymongo.errors import DuplicateKeyError

from .db import db

GENESIS = "0" * 64


def _digest(prev: str, body: dict[str, Any]) -> str:
    return hashlib.sha256((prev + json.dumps(body, sort_keys=True, default=str)).encode()).hexdigest()


def record(kind: str, actor: str, **data: Any) -> dict[str, Any]:
    for _ in range(5):  # retry on concurrent appends
        last = db().audit.find_one(sort=[("seq", DESCENDING)])
        seq = (last["seq"] + 1) if last else 1
        prev = last["hash"] if last else GENESIS
        body = {"seq": seq, "ts": dt.datetime.now(dt.timezone.utc).isoformat(), "kind": kind,
                "actor": actor, "data": data}
        doc = {**body, "prev": prev, "hash": _digest(prev, body)}
        try:
            db().audit.insert_one(doc)
            doc.pop("_id", None)
            return doc
        except DuplicateKeyError:
            continue
    raise RuntimeError("audit append contention")


def verify() -> dict[str, Any]:
    prev, n = GENESIS, 0
    for doc in db().audit.find({}, {"_id": 0}).sort("seq", 1):
        body = {k: doc[k] for k in ("seq", "ts", "kind", "actor", "data")}
        if doc["prev"] != prev or doc["hash"] != _digest(prev, body):
            return {"ok": False, "broken_at": doc["seq"], "checked": n}
        prev, n = doc["hash"], n + 1
    return {"ok": True, "checked": n}


def tail(n: int = 30) -> list[dict[str, Any]]:
    return list(db().audit.find({}, {"_id": 0}).sort("seq", -1).limit(n))
