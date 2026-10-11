"""Understand what a follow-up means for the answer before it, without asking the user anything.

After an answer, the person's next message in the same conversation is classified:
  correction   the previous answer was wrong or missing something        → negative signal on it
  refinement   right content, different shape (shorter, table, tone)    → this person's format preference
  extension    build on the work ("now also…", "turn this into…")       → the answer was a good base; if the
                                                                           same extension recurs, the agent
                                                                           should start doing it proactively
  new_task     unrelated                                                 → nothing
Runs in the background after the reply is sent. Everything is logged in Atlas `intent_events`, mirrored to
`implicit_signals` / `improvement_requests` / governed `recommendations`, and sent to Raindrop.
"""

from __future__ import annotations

import datetime as dt
import re
import threading
from typing import Any

from . import audit, llm, memory, telemetry
from .db import db

INTENTS = ("correction", "refinement", "extension", "new_task")

HINTS = {
    "correction": re.compile(r"\b(wrong|incorrect|not right|you (missed|forgot)|missing|that'?s not|actually|isn'?t)\b", re.I),
    "refinement": re.compile(r"\b(shorter|longer|concise|simpler|table|bullets?|format|rephrase|rewrite|tone|summar(y|ise|ize)|tl;?dr)\b", re.I),
    "extension": re.compile(r"\b(now|also|next|then|on top|in addition|additionally|turn (this|it) into|based on (this|that)|"
                            r"draft|write (a|the)|create (a|the)|add (a|an) (section|step|plan)|what about)\b", re.I),
}

CLASSIFIER = (
    "You classify how a follow-up message relates to the assistant's previous answer in a work conversation. "
    "correction = the previous answer was wrong or missed something the person needed; "
    "refinement = same content, different shape (length, format, tone); "
    "extension = keep the previous work and do more on top of it (a new deliverable built from it); "
    "new_task = unrelated to the previous answer."
)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _heuristic(followup: str) -> dict[str, Any]:
    for kind in ("correction", "refinement", "extension"):
        if HINTS[kind].search(followup):
            return {"intent": kind, "change": followup.strip()[:160], "confidence": 0.5, "by": "heuristic"}
    return {"intent": "new_task", "change": "", "confidence": 0.4, "by": "heuristic"}


def classify(prev_q: str, prev_a: str, followup: str) -> dict[str, Any]:
    if not llm.available():
        return _heuristic(followup)
    try:
        v = llm.complete_json(CLASSIFIER, f"Previous question: {prev_q[:400]}\nPrevious answer: {prev_a[:900]}\n"
                                          f"Follow-up: {followup[:400]}\n\n"
                                          'Return {"intent": "correction|refinement|extension|new_task", '
                                          '"change": "the change or addition the person wants, in under 15 words, as an '
                                          'instruction (e.g. \'add the exact rollback command\')", "confidence": 0-1}',
                              max_tokens=200)
        kind = str(v.get("intent", "")).strip().lower()
        if kind not in INTENTS:
            return _heuristic(followup)
        return {"intent": kind, "change": str(v.get("change", ""))[:160], "confidence": float(v.get("confidence", 0.6) or 0.6),
                "by": "model"}
    except Exception:
        return _heuristic(followup)


def record(prev: dict[str, Any], followup_event_id: str, followup_text: str, user_id: str) -> dict[str, Any]:
    """Classify and log. Called in a background thread after the follow-up has been answered."""
    from . import governance
    from .assistant import Vault, implicit_signal, name_registry

    v = Vault(name_registry())
    prev_q, prev_a = v.detokenize(prev["question_t"]), v.detokenize(prev.get("answer_t", ""))
    c = classify(prev_q, prev_a, followup_text)
    change = c["change"] or followup_text[:160]
    doc = {"event_id": prev["_id"], "followup_event_id": followup_event_id, "user_id": user_id,
           "agent_id": prev.get("agent_id"), "scope": prev["role"], "harness_version": prev["harness_version"],
           "intent": c["intent"], "change_t": v.tokenize(change), "confidence": c["confidence"], "by": c["by"], "ts": _now()}
    if governance.screen(change):
        doc["quarantined"] = True
        db().intent_events.insert_one(doc)
        return doc
    db().intent_events.insert_one(doc)
    aid = prev.get("agent_id")
    if c["intent"] in ("correction", "refinement"):
        implicit_signal(prev["_id"], user_id, "followup_correction")
        db().improvement_requests.insert_one({"event_id": prev["_id"], "user_id": user_id, "agent_id": aid, "scope": prev["role"],
                                              "harness_version": prev["harness_version"], "request_t": v.tokenize(change),
                                              "source": f"intent:{c['intent']}", "status": "logged", "ts": _now()})
        if aid:  # this person's own preference immediately; governed change for everyone only with a quorum
            memory.add(aid, user_id, "preference", change, source=prev["_id"])
            memory.recommend(aid, prev["role"], user_id, prev["_id"], prev_q, prev_a, change, prev["harness_version"])
            db().recommendations.update_one({"event_id": prev["_id"], "text": change}, {"$set": {"source": f"intent:{c['intent']}"}})
    elif c["intent"] == "extension" and aid:
        text = f"People often follow this kind of request by asking to: {change}. Offer or include it when relevant."
        memory.recommend(aid, prev["role"], user_id, prev["_id"], prev_q, prev_a, text, prev["harness_version"])
        db().recommendations.update_one({"event_id": prev["_id"], "text": text}, {"$set": {"source": "intent:extension"}})
    telemetry.signal(prev["_id"], f"followup_{c['intent']}", negative=c["intent"] in ("correction", "refinement"),
                     comment=v.tokenize(change))
    audit.record("followup_intent", user_id, episode=prev["_id"], agent=aid or "", intent=c["intent"], by=c["by"])
    doc.pop("_id", None)
    return doc


def record_async(prev: dict[str, Any], followup_event_id: str, followup_text: str, user_id: str) -> None:
    def run() -> None:
        try:
            record(prev, followup_event_id, followup_text, user_id)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()
