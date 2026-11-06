"""Live usage counters (`usage` collection, one document per day). Every model, embedding and retrieval call bumps one."""

from __future__ import annotations

import datetime as dt
import threading
from collections import Counter

_pending: Counter = Counter()
_lock = threading.Lock()


def bump(key: str, n: int = 1) -> None:
    with _lock:
        _pending[key] += n
        flush_now = sum(_pending.values()) >= 20
    if flush_now:
        flush()


def flush() -> None:
    from .db import db

    with _lock:
        items = dict(_pending)
        _pending.clear()
    if not items:
        return
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    try:
        db().usage.update_one({"_id": day}, {"$inc": {k.replace(".", "_"): v for k, v in items.items()}}, upsert=True)
    except Exception:
        pass


def totals() -> dict[str, int]:
    from .db import db

    flush()
    out: Counter = Counter()
    for d in db().usage.find({}):
        for k, v in d.items():
            if k != "_id" and isinstance(v, int):
                out[k] += v
    return dict(out)
