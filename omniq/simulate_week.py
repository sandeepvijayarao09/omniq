"""Replay a realistic week of decisions for one person, backdated across the last 7 days, so the 7-day cycle
has real history to learn from (demo / testing). Every turn goes through the real pipeline and model."""

from __future__ import annotations

import datetime as dt
import random

from . import assistant
from .db import db

SCRIPTS = {
    "developer": [("release_agent", "Can I deploy on a Friday afternoon?", "up", ""),
                  ("verifier_agent", "Verify: ship the gateway connection-limit change on Friday at 3pm straight to 100% of traffic.", "down",
                   "Always show the exact rule text and runbook section for every failed check."),
                  ("debug_agent", "Users are getting logged out and ingest alerts are delayed. What do I do?", "regen", ""),
                  ("debug_agent", "Who is on call the week of Oct 20 and how do I escalate a SEV1?", "down",
                   "Put the exact person to page and the escalation time limit first."),
                  ("build_agent", "Plan: move user sessions from Redis to Postgres.", "up", ""),
                  ("code_review_agent", "Review: PR switches session reads to Postgres in one step, with no dual-write and no flag.", "down",
                   "List blocking issues with the ADR number for each."),
                  ("release_agent", "At what error rate or latency should I roll back a canary?", "up", "")],
    "product_manager": [("spec_agent", "Draft a one-page spec for SSO/SAML GA.", "down", "Always include success metrics with numbers and a GA date."),
                        ("prioritization_agent", "What is on the Q4 roadmap and which OKR does each item support?", "up", ""),
                        ("research_agent", "What did research find about how on-call engineers use Pulse?", "regen", ""),
                        ("launch_agent", "What has to happen before a feature launches behind a flag?", "down", "Give a checklist with an owner per item."),
                        ("metrics_agent", "What are the company OKRs and key results this half?", "up", "")],
    "director": [("status_report_agent", "Are we on track for the reliability OKR?", "down", "Lead with red/amber/green and the decision I need to make."),
                 ("incident_review_agent", "Which August outage action items are still open, and who owns them?", "up", ""),
                 ("risk_agent", "Which Q4 roadmap items are at risk and why?", "regen", ""),
                 ("people_agent", "How is my team doing and who might be at risk of burnout?", "up", "")],
    "cto": [("strategy_agent", "What are the biggest technical risks to the roadmap?", "down", "Three bullets max, then the decision you recommend."),
            ("board_brief_agent", "Prepare the technology update for the next board meeting.", "up", ""),
            ("vendor_agent", "What do we spend on managed Kafka and when does it renew?", "regen", ""),
            ("security_agent", "Where are we on SOC 2 Type II?", "up", "")],
}


def run(user_id: str, seed: int = 7) -> dict:
    from .agents import org_role

    user = assistant.get_user(user_id)
    role = org_role(user)
    rnd = random.Random(seed + hash(user_id) % 1000)
    now = dt.datetime.now(dt.timezone.utc)
    script = SCRIPTS.get(role, [])
    done = 0
    for i, (agent, q, action, rec) in enumerate(script):
        when = now - dt.timedelta(days=6.5 - i * (6.0 / max(1, len(script))), hours=rnd.randint(0, 5))
        r = assistant.ask(user_id, q, agent_id=agent, convo_id=f"week-{user_id}-{i}")
        db().episodes.update_one({"_id": r["event_id"]}, {"$set": {"ts": when}})
        if action == "up":
            assistant.feedback(r["event_id"], user_id, True)
        elif action == "down":
            assistant.feedback(r["event_id"], user_id, False, recommendation=rec)
        elif action == "regen":
            assistant.feedback(r["event_id"], user_id, False, comment="regenerated")
        for coll, key in (("feedback", "event_id"), ("recommendations", "event_id")):
            db()[coll].update_many({key: r["event_id"]}, {"$set": {"ts": when}})
        db().agent_memory.update_many({"source": r["event_id"]}, {"$set": {"ts": when}})
        done += 1
    return {"user": user.name, "role": role, "turns": done}
