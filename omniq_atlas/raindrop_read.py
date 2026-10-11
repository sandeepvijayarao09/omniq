"""Read Raindrop back into the learning loop (Raindrop Query API, `raindrop-query`).

The assistant sends every real answer to Raindrop with event_id = the Atlas episode id, and every 👍/👎,
regeneration and suggestion as a signal. Raindrop also detects issues on its own (frustration, task failure…).
The 7-day cycle calls cycle_evidence() to pull a person's events and signals for the window, maps them back to
Atlas episodes by id, and treats Raindrop-flagged answers as evidence against the agent that gave them.

Needs RAINDROP_QUERY_API_KEY (a separate, read-scoped key; the write key cannot read).
"""

from __future__ import annotations

import datetime as dt
import os
from collections import Counter
from functools import lru_cache
from typing import Any

NEGATIVE_HINTS = ("down", "negative", "frustrat", "fail", "regenerat", "wrong", "quarantin", "complain", "issue")


@lru_cache(maxsize=1)
def _client():
    key = os.environ.get("RAINDROP_QUERY_API_KEY", "").strip()
    if not key:
        return None
    from raindrop_query import RaindropQuery

    return RaindropQuery(api_key=key)


def available() -> bool:
    return _client() is not None


def _as_dict(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, list):
        return [_as_dict(x) for x in obj]
    return obj


def _items(resp: Any) -> list[dict[str, Any]]:
    d = _as_dict(resp)
    if isinstance(d, dict):
        for k in ("data", "events", "items", "results", "signals"):
            if isinstance(d.get(k), list):
                return d[k]
        for v in d.values():  # some SDK responses nest the page one level down
            if isinstance(v, dict):
                for k in ("data", "events", "items"):
                    if isinstance(v.get(k), list):
                        return v[k]
    return d if isinstance(d, list) else []


def _signal_names(ev: dict[str, Any]) -> list[str]:
    out = []
    for s in ev.get("signals") or ev.get("signal_matches") or []:
        out.append(str(s.get("name") or s.get("signal") or s.get("id") or s) if isinstance(s, dict) else str(s))
    return out


def _ts(ev: dict[str, Any]) -> dt.datetime | None:
    raw = ev.get("timestamp") or ev.get("created_at") or ev.get("ts")
    if isinstance(raw, dt.datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=dt.timezone.utc)
    try:
        return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None


def cycle_evidence(user_id: str, since: dt.datetime, until: dt.datetime) -> dict[str, Any]:
    """Events and signals Raindrop holds for this person in the window. Never raises: returns a status instead."""
    c = _client()
    if c is None:
        return {"status": "no query key", "events": 0, "signals": {}, "flagged": []}
    try:
        events = _items(c.events.list(user_id=user_id, limit=200))
    except Exception as e:
        return {"status": f"query failed: {str(e)[:120]}", "events": 0, "signals": {}, "flagged": []}
    in_window = [e for e in events if (t := _ts(e)) is None or since <= t < until]
    sig = Counter()
    flagged = []
    for e in in_window:
        names = _signal_names(e)
        sig.update(names)
        if any(h in n.lower() for n in names for h in NEGATIVE_HINTS):
            flagged.append({"event_id": e.get("event_id") or e.get("id"), "signals": names,
                            "input": str(e.get("input") or e.get("user_input") or "")[:160]})
    return {"status": "ok", "events": len(in_window), "signals": dict(sig), "flagged": flagged}


def project_signals() -> dict[str, Any]:
    """Signal definitions Raindrop tracks for the project (for the Impact view)."""
    c = _client()
    if c is None:
        return {"status": "no query key", "signals": []}
    try:
        rows = _items(c.signals.list(limit=50))
        return {"status": "ok", "signals": [r.get("name") or r.get("id") for r in rows if isinstance(r, dict)]}
    except Exception as e:
        return {"status": f"query failed: {str(e)[:120]}", "signals": []}
