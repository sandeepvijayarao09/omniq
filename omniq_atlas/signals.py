"""Where each agent is failing, computed locally from behaviour the system already records.

Every answer is an ``episodes`` document. What people do next is recorded
against that episode id:

  feedback               thumbs up / down (+ comment, + "do this instead")
  implicit_signals       rephrased, regenerated, copied, followup_correction
  intent_events          the classified intent of a follow-up (correction, refinement, extension...)
  improvement_requests   explicit "improve this" requests

An answer counts as *failing* when it drew any negative signal, or when the
answer itself says the sources didn't contain the answer. These functions feed
the 7-day cycles, the regression watchdog and the failure-spike trigger.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict
from typing import Any

from .db import db

FAILURE_KINDS = {
    "thumbs_down": "rated down", "rephrased": "had to rephrase", "followup_correction": "corrected it",
    "followup_refinement": "asked for a different shape", "regenerated": "regenerated",
    "recommendation": "said what to change", "improvement_request": "asked for an improvement",
    "not_found": "said it couldn't find it",
}
NOT_FOUND = re.compile(r"(couldn'?t|could not|unable to) find|not (in|contain(ed)?) (the )?(sources|provided)"
                       r"|don'?t contain", re.I)


def _aware(t: Any) -> dt.datetime | None:
    if isinstance(t, dt.datetime):
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    return None


def _kinds_by_event(event_ids: list[str]) -> dict[str, set[str]]:
    """Negative signal kinds per episode id, across every local signal collection."""
    out: dict[str, set[str]] = defaultdict(set)
    if not event_ids:
        return out
    ids = {"$in": event_ids}
    for f in db().feedback.find({"event_id": ids}, {"event_id": 1, "good": 1, "recommendation_t": 1}):
        if not f.get("good"):
            out[f["event_id"]].add("thumbs_down")
        if f.get("recommendation_t"):
            out[f["event_id"]].add("recommendation")
    for s in db().implicit_signals.find({"event_id": ids, "positive": False}, {"event_id": 1, "kind": 1}):
        out[s["event_id"]].add(s["kind"])
    for i in db().intent_events.find({"event_id": ids, "intent": {"$in": ["correction", "refinement"]}},
                                     {"event_id": 1, "intent": 1}):
        out[i["event_id"]].add(f"followup_{i['intent']}")
    for r in db().improvement_requests.find({"event_id": ids}, {"event_id": 1}):
        out[r["event_id"]].add("improvement_request")
    return out


def _episodes(query: dict[str, Any], since: dt.datetime, until: dt.datetime | None) -> list[dict[str, Any]]:
    q = {"source": "chat", "ts": {"$gte": since, **({"$lt": until} if until else {})}, **query}
    return list(db().episodes.find(q, {"embedding": 0}))


def _failing(ep: dict[str, Any], kinds: dict[str, set[str]]) -> set[str]:
    k = set(kinds.get(ep["_id"], set()))
    if NOT_FOUND.search(ep.get("answer_t") or ""):
        k.add("not_found")
    return k


def agent_failures(agent_id: str, since: dt.datetime, until: dt.datetime) -> dict[str, Any]:
    """Failing answers for one agent in the window: by kind, by playbook version, with examples."""
    eps = _episodes({"agent_id": agent_id}, since, until)
    kinds = _kinds_by_event([e["_id"] for e in eps])
    by_kind: Counter[str] = Counter()
    by_version: dict[str, dict[str, int]] = {}
    failing = []
    for e in eps:
        v = str(e.get("harness_version", "?"))
        bv = by_version.setdefault(v, {"answers": 0, "failing": 0})
        bv["answers"] += 1
        why = _failing(e, kinds)
        if why:
            bv["failing"] += 1
            labels = sorted(FAILURE_KINDS.get(w, w) for w in why)
            by_kind.update(labels)
            failing.append({"event_id": e["_id"], "version": v, "why": labels,
                            "question": str(e.get("question_t") or "")[:180]})
    n = len(eps)
    return {"status": "ok", "answers": n, "failing": len(failing),
            "failure_rate": round(len(failing) / n, 3) if n else None,
            "by_kind": dict(by_kind.most_common()), "by_version": by_version, "examples": failing[:8],
            "failing_event_ids": [f["event_id"] for f in failing]}


def failure_rates(since: dt.datetime) -> dict[str, dict[str, Any]]:
    """Per-agent failure rate since `since` (for the spike trigger)."""
    eps = [e for e in _episodes({}, since, None) if e.get("agent_id")]
    kinds = _kinds_by_event([e["_id"] for e in eps])
    out: dict[str, dict[str, Any]] = {}
    for e in eps:
        row = out.setdefault(e["agent_id"], {"answers": 0, "failing": 0})
        row["answers"] += 1
        if _failing(e, kinds):
            row["failing"] += 1
    for row in out.values():
        row["rate"] = round(row["failing"] / row["answers"], 3) if row["answers"] else 0.0
    return out


def cycle_evidence(user_id: str, since: dt.datetime, until: dt.datetime) -> dict[str, Any]:
    """This person's answers in the window, signal counts, and the answers that drew negative signals."""
    eps = _episodes({"user_id": user_id}, since, until)
    kinds = _kinds_by_event([e["_id"] for e in eps])
    sig: Counter[str] = Counter()
    flagged = []
    for e in eps:
        why = _failing(e, kinds)
        sig.update(why)
        if why:
            flagged.append({"event_id": e["_id"], "signals": sorted(why), "agent_id": e.get("agent_id"),
                            "input": str(e.get("question_t") or "")[:160]})
    return {"status": "ok", "events": len(eps), "signals": dict(sig), "flagged": flagged}


def event_total() -> int:
    return db().episodes.count_documents({"source": "chat"})
