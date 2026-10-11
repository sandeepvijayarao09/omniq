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
        if scope.startswith("agent:"):  # Raindrop's failure rate per version, when it has more evidence
            try:
                from . import raindrop_read

                f = raindrop_read.agent_failures(scope[6:], _now() - dt.timedelta(days=7), _now())
                bv = f.get("by_version") or {}
                cur_v, par_v = bv.get(str(ver)), bv.get(str(parent))
                if cur_v and par_v and cur_v["answers"] >= n and par_v["answers"]:
                    rate, n = cur_v["failing"] / cur_v["answers"], cur_v["answers"]
                    base, n0 = par_v["failing"] / par_v["answers"], par_v["answers"]
            except Exception:
                pass
        if n >= MIN_VOTES and rate - base >= REGRESSION_MARGIN:
            reason = f"live failure rate {rate:.0%} on v{ver} (n={n}) vs {base:.0%} on v{parent} (n={n0}) (Atlas feedback + Raindrop)"
            harness.reject(scope, ver, cur.get("scores", {}), reason)
            db().harness.update_one({"role": scope, "version": parent}, {"$set": {"status": "active"}})
            audit.record("harness_auto_rollback", "watchdog", role=scope, version=ver, restored=parent, reason=reason)
            actions.append({"scope": scope, "rolled_back": ver, "restored": parent, "reason": reason})
    return actions


AUTO_EVOLVE_MINUTES = int(__import__("os").environ.get("OA_AUTO_EVOLVE_MINUTES", "20"))


def auto_evolve() -> dict[str, Any] | None:
    """Every AUTO_EVOLVE_MINUTES, give the agent that has waited longest one improvement round against its
    graded cases. Skips while people are chatting (last 2 minutes) so live answers stay fast."""
    if AUTO_EVOLVE_MINUTES <= 0:
        return None
    state = db().scope_policy.find_one({"_id": "__auto_evolve__"}) or {}
    last = state.get("last")
    if last and last.replace(tzinfo=dt.timezone.utc) > _now() - dt.timedelta(minutes=AUTO_EVOLVE_MINUTES):
        return None
    if db().episodes.find_one({"source": "chat", "ts": {"$gt": _now() - dt.timedelta(minutes=2)}}):
        return None
    from . import evolve

    scopes = [f"agent:{a['id']}" for a in db().agents.find({}, {"id": 1})]
    if not scopes:
        return None
    last_run = {r["_id"]: r["t"] for r in db().evolve_runs.aggregate([
        {"$match": {"role": {"$in": scopes}}}, {"$group": {"_id": "$role", "t": {"$max": "$created"}}}])}
    scope = min(scopes, key=lambda s: last_run.get(s) or dt.datetime(1970, 1, 1))
    db().scope_policy.update_one({"_id": "__auto_evolve__"}, {"$set": {"last": _now(), "scope": scope}}, upsert=True)
    run = evolve.run(scope, 1)
    return {"scope": scope, "run": run["_id"], "events": [e["kind"] for e in run["events"]]}
