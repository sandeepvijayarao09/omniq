"""Long-running upkeep, run by the scheduler next to the improvement loop.

consolidate()  keeps per-agent memory coherent as it grows: near-duplicate preferences from the same
               user collapse into the newest one (a changed mind wins), so memory never accumulates
               contradictory instructions.
watchdog()     production signal after promotion: if real users thumbs-down the active version clearly
               more often than its parent, the parent is restored automatically.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from . import audit, harness
from .db import db

DUP_SIMILARITY = 0.92
MIN_VOTES = 5          # live votes on the new version before the watchdog may judge it
REGRESSION_MARGIN = 0.25  # thumbs-down rate must be this much worse than the parent's


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def consolidate() -> int:
    retired = 0
    groups = db().agent_memory.aggregate([
        {"$match": {"status": "active", "kind": "preference"}},
        {"$sort": {"ts": -1}},
        {"$group": {"_id": {"a": "$agent_id", "u": "$user_id"}, "items": {"$push": {"id": "$_id", "v": "$embedding"}}}},
        {"$match": {"items.1": {"$exists": True}}},
    ])
    for g in groups:
        kept: list[list[float]] = []
        for item in g["items"]:  # newest first
            v = item["v"]
            if any(sum(a * b for a, b in zip(v, k)) >= DUP_SIMILARITY for k in kept):
                db().agent_memory.update_one({"_id": item["id"]}, {"$set": {"status": "superseded", "superseded_at": _now()}})
                retired += 1
            else:
                kept.append(v)
    if retired:
        audit.record("memory_consolidated", "system", retired=retired)
    return retired


def _down_rate(scope: str, version: int) -> tuple[float, int]:
    rows = list(db().feedback.aggregate([
        {"$match": {"role": scope, "harness_version": version}},
        {"$group": {"_id": None, "n": {"$sum": 1}, "down": {"$sum": {"$cond": ["$good", 0, 1]}}}}]))
    if not rows:
        return 0.0, 0
    return rows[0]["down"] / rows[0]["n"], rows[0]["n"]


def watchdog() -> list[dict[str, Any]]:
    actions = []
    for cur in db().harness.find({"status": "active", "parent": {"$ne": None}}):
        scope, ver, parent = cur["role"], cur["version"], cur["parent"]
        rate, n = _down_rate(scope, ver)
        base, n0 = _down_rate(scope, parent)
        if n >= MIN_VOTES and rate - base >= REGRESSION_MARGIN:
            reason = f"live thumbs-down {rate:.0%} on v{ver} (n={n}) vs {base:.0%} on v{parent} (n={n0})"
            harness.reject(scope, ver, cur.get("scores", {}), reason)
            db().harness.update_one({"role": scope, "version": parent}, {"$set": {"status": "active"}})
            audit.record("harness_auto_rollback", "watchdog", role=scope, version=ver, restored=parent, reason=reason)
            actions.append({"scope": scope, "rolled_back": ver, "restored": parent, "reason": reason})
    return actions
