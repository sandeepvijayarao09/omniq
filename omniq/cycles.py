"""The definitive 7-day improvement cycle, per person.

Every 7 days (or on demand) for each person:
  1. Collect every decision they made in the cycle, from the store:
       questions asked (and which @agent), 👍/👎, regenerations, "do this instead" suggestions, memories,
       approvals / rejections / freezes they made, improvement runs they started.
  2. Measure: usage per agent, satisfaction, regenerations, "not in the sources" answers, topics.
  3. An analyst model reads the ledger and returns:
       - personal context: 3-6 short "learned" bullets about how this person works and what they need
       - per agent they used: a better workflow (procedure / output template) and rules, only where the
         evidence shows the agent fell short
  4. Apply, through the same governance as everything else:
       - personal context → a new version of their persona in `context_layers` (screened for injection)
       - agent context/workflow → a recommendation per agent in `recommendations` (source "weekly_cycle").
         The recommendation loop then decides: two-user quorum, blind replay, eval guard, safety gate,
         and approval for template changes. One person's cycle can never rewrite an agent for everyone alone.
  5. Write a cycle report to `cycles`, and record it in the audit log.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import uuid
from collections import Counter
from typing import Any

from . import audit, contextdb, governance, llm, memory
from .config import settings
from .db import db

CYCLE_DAYS = 7
ABSTAIN = re.compile(r"(couldn'?t|could not|unable to) find|not (in|contain(ed)?) (the )?(sources|provided)|don'?t contain", re.I)

ANALYST = (
    "You review one employee's last 7 days with an internal AI assistant and its agents. From their decisions "
    "(what they asked, which agents they used, what they rated, regenerated or asked to change), work out what "
    "they actually need, and how each agent should improve for them. Be specific and evidence-based. Never include "
    "secrets, salaries, health or HR details; describe working patterns, not private facts. Implicit signals matter "
    "most: 'rephrased' or 'followup_correction' mean the answer missed; 'copied' means it was useful; repeated "
    "'extension' follow-ups show what the agent should start including proactively. Never suggest weakening "
    "access control or privacy."
)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def ledger(user_id: str, since: dt.datetime, until: dt.datetime) -> dict[str, Any]:
    """Every decision this person made in the window."""
    w = {"$gte": since, "$lt": until}
    eps = list(db().episodes.find({"user_id": user_id, "source": "chat", "ts": w},
                                  {"embedding": 0}).sort("ts", 1))
    fb = list(db().feedback.find({"user_id": user_id, "ts": w}))
    recs = list(db().recommendations.find({"user_id": user_id, "ts": w}, {"embedding": 0}))
    mems = list(db().agent_memory.find({"user_id": user_id, "ts": w}, {"embedding": 0}))
    acts = list(db().audit.find({"actor": user_id, "ts": {"$gte": since.isoformat(), "$lt": until.isoformat()},
                                 "kind": {"$in": ["harness_approved", "harness_rejected", "harness_frozen",
                                                  "harness_unfrozen", "harness_manual_rollback", "evolve_start"]}}))
    by_event = {f["event_id"]: f for f in fb}
    intents = list(db().intent_events.find({"user_id": user_id, "ts": w}, {"_id": 0, "agent_id": 1, "intent": 1, "change_t": 1}))
    imp: dict[str, set[str]] = {}
    for x in db().implicit_signals.find({"user_id": user_id, "ts": w}):
        imp.setdefault(x["event_id"], set()).add(x["kind"])
    asks = []
    for e in eps:
        f = by_event.get(e["_id"])
        asks.append({"agent": e.get("agent_id") or "assistant", "question": e["question_t"][:200],
                     "rating": None if not f else ("up" if f["good"] else "down"),
                     "regenerated": bool(f and f.get("comment_t") == "regenerated"),
                     "not_found": bool(ABSTAIN.search(e.get("answer_t", ""))), "latency": e.get("latency"),
                     "implicit": sorted(imp.get(e["_id"], set()))})
    per_agent: dict[str, dict[str, int]] = {}
    for a in asks:
        s = per_agent.setdefault(a["agent"], {"asks": 0, "up": 0, "down": 0, "regenerated": 0, "not_found": 0})
        s["asks"] += 1
        s["up"] += a["rating"] == "up"
        s["down"] += a["rating"] == "down"
        s["regenerated"] += a["regenerated"]
        s["not_found"] += a["not_found"]
        s["rephrased"] = s.get("rephrased", 0) + ("rephrased" in a["implicit"])
        s["corrected"] = s.get("corrected", 0) + ("followup_correction" in a["implicit"])
        s["copied"] = s.get("copied", 0) + ("copied" in a["implicit"])
    from . import signals

    rd = signals.cycle_evidence(user_id, since, until)
    flagged_ids = {f["event_id"] for f in rd["flagged"]}
    for e, a in zip(eps, asks):
        if e["_id"] in flagged_ids:
            a["flagged"] = next(f["signals"] for f in rd["flagged"] if f["event_id"] == e["_id"])
            per_agent[a["agent"]]["flagged"] = per_agent[a["agent"]].get("flagged", 0) + 1
    for it in intents:
        a = it.get("agent_id") or "assistant"
        if a in per_agent:
            per_agent[a][it["intent"]] = per_agent[a].get(it["intent"], 0) + 1
    return {"asks": asks, "per_agent": per_agent, "signals": rd,
            "follow_up_intents": [{"agent": i.get("agent_id") or "assistant", "intent": i["intent"], "change": i["change_t"]}
                                  for i in intents if i["intent"] != "new_task"][:30],
            "suggestions": [{"agent": r.get("agent_id"), "text": r["text"], "status": r.get("status")} for r in recs],
            "memories": [{"agent": m.get("agent_id"), "kind": m["kind"], "text": m["text"]} for m in mems],
            "governance_actions": [{"kind": a["kind"], "scope": a["data"].get("role")} for a in acts],
            "totals": {"decisions": len(asks) + len(fb) + len(recs) + len(mems) + len(acts), "asks": len(asks),
                       "ratings": len(fb), "suggestions": len(recs), "memories": len(mems), "governance": len(acts),
                       "answers_in_window": rd["events"], "flagged": len(rd["flagged"])}}


def _analyse(user_name: str, L: dict[str, Any]) -> dict[str, Any]:
    prompt = (f"Employee: {user_name}\nTotals: {json.dumps(L['totals'])}\nPer agent: {json.dumps(L['per_agent'])}\n"
              f"Questions (oldest first): {json.dumps(L['asks'][-40:])}\nSuggestions they made: {json.dumps(L['suggestions'])}\n"
              f"Preferences/memories: {json.dumps(L['memories'])}\nGovernance actions: {json.dumps(L['governance_actions'])}\n"
              f"What they asked for on top of answers (classified follow-ups): {json.dumps(L.get('follow_up_intents', []))}\n"
              f"Negative signals on their answers (local monitoring): {json.dumps({k: L['signals'][k] for k in ('events', 'signals')})}\n\n"
              'Return {"summary": str (2 sentences), "learned": [3-6 short bullets about how they work and what they need], '
              '"agents": [{"agent": id, "why": str (evidence), "rules": [0-2 short rules], '
              '"procedure": [optional, up to 6 steps], "template": optional str}]}  '
              "Only include an agent when the evidence shows a gap (downvotes, regenerations, not-found answers, suggestions).")
    return llm.complete_json(ANALYST, prompt, model=settings.meta_model, max_tokens=1500)


def _heuristic(L: dict[str, Any]) -> dict[str, Any]:
    top = Counter(a["agent"] for a in L["asks"]).most_common(3)
    learned = [f"Uses {a} most ({n} questions this cycle)." for a, n in top]
    agents = [{"agent": a, "why": f"{s['down']} downvotes, {s['regenerated']} regenerations",
               "rules": ["Lead with the direct answer and the exact numbers."]}
              for a, s in L["per_agent"].items() if a != "assistant" and (s["down"] or s["regenerated"])]
    return {"summary": f"{L['totals']['asks']} questions this cycle.", "learned": learned, "agents": agents}


def _with_learned(md: str, bullets: list[str], cycle_label: str) -> str:
    section = f"## Learned from my last cycle\n_{cycle_label}_\n" + "\n".join(f"- {b}" for b in bullets) + "\n"
    if "## Learned from my last cycle" in md:
        return re.sub(r"## Learned from my last cycle.*?(?=^## |\Z)", section + "\n", md, flags=re.S | re.M)
    marker = "## Use this to tailor every answer"
    return md.replace(marker, section + "\n" + marker) if marker in md else md + "\n\n" + section


def run(user_id: str, days: int = CYCLE_DAYS, until: dt.datetime | None = None) -> dict[str, Any]:
    from .assistant import get_user

    user = get_user(user_id)
    until = until or _now()
    since = until - dt.timedelta(days=days)
    cid = uuid.uuid4().hex[:10]
    L = ledger(user_id, since, until)
    report: dict[str, Any] = {"_id": cid, "user_id": user_id, "since": since, "until": until, "created": _now(),
                              "totals": L["totals"], "per_agent": L["per_agent"], "status": "running"}
    db().cycles.insert_one(report)
    if L["totals"]["asks"] == 0:
        db().cycles.update_one({"_id": cid}, {"$set": {"status": "done", "summary": "No activity this cycle."}})
        return db().cycles.find_one({"_id": cid})

    try:
        A = _analyse(user.name, L) if llm.available() else _heuristic(L)
    except Exception as e:
        A = {**_heuristic(L), "analyst_error": str(e)[:200]}

    # 1) personal context: a new persona version (screened)
    learned = [b.strip()[:200] for b in (A.get("learned") or []) if isinstance(b, str) and b.strip()][:6]
    flagged = [b for b in learned if governance.screen(b)]
    learned = [b for b in learned if b not in flagged]
    persona_change = None
    cur = contextdb.active("person", user_id)
    if learned and cur:
        label = f"cycle {since:%Y-%m-%d} → {until:%Y-%m-%d}, {L['totals']['decisions']} decisions"
        new = contextdb.update("person", user_id, _with_learned(cur["markdown"], learned, label),
                               reason=f"7-day cycle {cid}: {A.get('summary', '')[:160]}", source="cycle", cycle=cid)
        persona_change = {"from": cur["version"], "to": new["version"], "learned": learned}

    # 2) agent context/workflow: queue governed recommendations (quorum, replay, guard, safety gate decide)
    queued = []
    known = {a["id"] for a in db().agents.find({}, {"id": 1})}
    allowed_agents = {a for a in L["per_agent"] if a in known}
    names = {a["name"].lower(): a["id"] for a in db().agents.find({}, {"id": 1, "name": 1})}

    def resolve(raw: str) -> str:
        r = raw.strip().lstrip("@").lower()
        return names.get(r) or r.replace(" ", "_").replace("-", "_")

    items = [dict(i, agent=resolve(str(i.get("agent", "")))) for i in (A.get("agents") or []) if isinstance(i, dict)]
    covered = {i["agent"] for i in items}
    for aid, st in L["per_agent"].items():  # every agent with hard negative evidence gets reviewed
        if aid in allowed_agents and aid not in covered and (st["down"] or st["regenerated"] or st.get("flagged")
                                                             or st.get("rephrased") or st.get("corrected")):
            own = [x["text"] for x in L["suggestions"] if x["agent"] == aid]
            items.append({"agent": aid, "why": f"{st['down']} downvotes, {st['regenerated']} regenerations, "
                                               f"{st.get('rephrased', 0)} rephrased, {st.get('corrected', 0)} corrected, "
                                               f"{st.get('flagged', 0)} flagged in {st['asks']} asks",
                          "rules": own[:2] or ["Lead with the direct answer and the exact numbers, then the steps."]})
    for item in items:
        aid = item["agent"]
        if aid not in allowed_agents:
            continue
        parts = [*(item.get("rules") or [])[:2]]
        if item.get("procedure"):
            parts.append("Workflow: " + " → ".join(str(x) for x in item["procedure"][:6]))
        if item.get("template"):
            parts.append("Output: " + str(item["template"])[:200])
        text = (" ".join(parts) or "")[:480]
        if not text or governance.screen(text):
            continue
        ep = db().episodes.find_one({"user_id": user_id, "agent_id": aid, "source": "chat"}, sort=[("ts", -1)])
        if not ep:
            continue
        from .assistant import Vault, name_registry

        v = Vault(name_registry())
        memory.recommend(aid, f"agent:{aid}", user_id, ep["_id"], v.detokenize(ep["question_t"]),
                         v.detokenize(ep.get("answer_t", "")), text, ep["harness_version"])
        db().recommendations.update_one({"event_id": ep["_id"], "text": text}, {"$set": {"source": "weekly_cycle", "cycle": cid}})
        queued.append({"agent": aid, "why": item.get("why", ""), "recommendation": text})

    out = {"status": "done", "signals": {k: L["signals"][k] for k in ("status", "events", "signals")},
           "summary": A.get("summary", ""), "learned": learned, "quarantined": flagged,
           "persona_change": persona_change, "agent_recommendations": queued, "analyst_error": A.get("analyst_error")}
    db().cycles.update_one({"_id": cid}, {"$set": out})
    audit.record("cycle_completed", "system", user=user_id, cycle=cid, decisions=L["totals"]["decisions"],
                 persona_version=persona_change and persona_change["to"], agent_recommendations=len(queued))
    return db().cycles.find_one({"_id": cid})


def due(user_id: str) -> bool:
    last = db().cycles.find_one({"user_id": user_id, "status": "done"}, sort=[("until", -1)])
    return not last or last["until"].replace(tzinfo=dt.timezone.utc) <= _now() - dt.timedelta(days=CYCLE_DAYS)


def tick() -> list[str]:
    """Scheduler hook: run the cycle for everyone whose last cycle ended 7+ days ago and who was active since."""
    from .context import team

    done = []
    for m in team():
        uid = m.get("id")
        if uid and due(uid) and db().episodes.find_one({"user_id": uid, "source": "chat"}):
            run(uid)
            done.append(uid)
    return done


# ================================================================ agent-level 7-day cycle

def agent_ledger(agent_id: str, since: dt.datetime, until: dt.datetime) -> dict[str, Any]:
    """Everything every user did with this agent in the window."""
    w = {"$gte": since, "$lt": until}
    eps = list(db().episodes.find({"agent_id": agent_id, "source": "chat", "ts": w}, {"embedding": 0}))
    fb = {f["event_id"]: f for f in db().feedback.find({"event_id": {"$in": [e["_id"] for e in eps]}})}
    users = Counter(e["user_id"] for e in eps)
    down = [e for e in eps if e["_id"] in fb and not fb[e["_id"]]["good"]]
    regen = [e for e in down if fb[e["_id"]].get("comment_t") == "regenerated"]
    not_found = [e for e in eps if ABSTAIN.search(e.get("answer_t", ""))]
    pending = db().recommendations.count_documents({"scope": f"agent:{agent_id}", "status": "pending"})
    implicit = Counter(x["kind"] for x in db().implicit_signals.find({"event_id": {"$in": [e["_id"] for e in eps]}}))
    return {"asks": len(eps), "users": len(users), "up": sum(1 for f in fb.values() if f["good"]), "down": len(down),
            "rephrased": implicit.get("rephrased", 0), "corrected": implicit.get("followup_correction", 0),
            "copied": implicit.get("copied", 0),
            "regenerated": len(regen), "not_found": len(not_found), "pending_suggestions": pending,
            "satisfaction": round(sum(1 for f in fb.values() if f["good"]) / len(fb), 3) if fb else None,
            "worst_questions": [e["question_t"][:160] for e in (down + not_found)[:6]]}


RELAY = ("scored", "proposed", "meta_overruled", "repair", "blocked", "replaying", "evaluated", "promoted", "rolled_back",
         "awaiting_approval", "converged", "themes", "triaged", "below_quorum", "quarantined")
PRIORITY = ("promoted", "awaiting_approval", "rolled_back", "blocked", "converged", "skipped", "paused", "done")


def _relay(run_id: str, sub: dict[str, Any]) -> str:
    """Copy a sub-run's key steps into this cycle's stream (so every stage shows), and return its real outcome."""
    from .evolve import _log

    for e in sub.get("events", []):
        if e["kind"] in RELAY:
            _log(run_id, e["kind"], **{k: v for k, v in e.items() if k not in ("ts", "kind")})
    kinds = {e["kind"] for e in sub.get("events", [])}
    return next((k for k in PRIORITY if k in kinds), "done")


def run_agent(agent_id: str, days: int = CYCLE_DAYS, run_id: str | None = None, actor: str = "scheduler") -> dict[str, Any]:
    """Improve one agent from the last `days` of evidence: suggestions first (governed), then hard metrics."""
    from . import evolve, improve
    from .evolve import _log

    scope = f"agent:{agent_id}"
    until = _now()
    since = until - dt.timedelta(days=days)
    run_id = run_id or uuid.uuid4().hex[:10]
    db().evolve_runs.insert_one({"_id": run_id, "role": scope, "kind": "agent_cycle", "status": "running",
                                 "created": _now(), "updated": _now(), "events": [], "actor": actor})
    L = agent_ledger(agent_id, since, until)
    _log(run_id, "cycle_ledger", days=days, **L)
    outcome: dict[str, Any] = {"ledger": L}

    # 0a) where this agent is failing, by kind and by playbook version (local behaviour signals)
    from . import signals

    rd = signals.agent_failures(agent_id, since, until)
    outcome["failures"] = {k: rd.get(k) for k in ("status", "answers", "failing", "failure_rate", "by_kind", "by_version")}
    _log(run_id, "live_failures", answers=rd["answers"], failing=rd["failing"], failure_rate=rd["failure_rate"],
         by_kind=rd["by_kind"], by_version=rd["by_version"], examples=[x["question"][:90] for x in rd["examples"][:4]])

    # 0b) live evidence: answers people marked as failing become evidence when 2+ people are affected
    added = _live_signal_recommendations(agent_id, since, until, extra_event_ids=rd.get("failing_event_ids", []))
    if added:
        _log(run_id, "cycle_step", step=f"Found {added} answers that real users rephrased, corrected, regenerated or rated down")
        L["pending_suggestions"] += added

    # 1) what people asked for (governed: quorum, blind replay, eval guard, safety gate, approval for templates)
    if L["pending_suggestions"]:
        _log(run_id, "cycle_step", step="Learning from behaviour patterns (rephrasing, corrections, regenerations, ratings)")
        r = improve.run(scope)
        res = _relay(run_id, r)
        last = next((e for e in reversed(r["events"]) if e["kind"] == res), {})
        outcome["suggestions"] = res
        _log(run_id, "cycle_result", step="suggestions", result=res,
             detail=str(last.get("reason") or last.get("reasons") or last.get("replay") or "")[:200], run=r["_id"])

    # 2) hard metrics, with last week's downvoted questions in the evidence the improver sees
    _log(run_id, "cycle_step", step="Testing against graded questions and last week's failures")
    r = evolve.run(scope, 2)
    res = _relay(run_id, r)
    last = next((e for e in reversed(r["events"]) if e["kind"] == res), {})
    scored = next((e for e in reversed(r["events"]) if e["kind"] == "scored"), None)
    outcome["metrics"] = res
    _log(run_id, "cycle_result", step="metrics", result=res,
         detail=(f"practice {scored['train']['pass_rate']:.0%}, unseen {scored['holdout']['pass_rate']:.0%}" if scored else str(last.get("reason", "")))[:200],
         run=r["_id"])

    from . import harness as hz

    final = hz.active(scope)
    db().agent_cycles.insert_one({"agent_id": agent_id, "since": since, "until": until, "created": _now(), "actor": actor,
                                  "run": run_id, "final_version": final["version"], **outcome})
    audit.record("agent_cycle_completed", actor, agent=agent_id, run=run_id, final_version=final["version"],
                 asks=L["asks"], down=L["down"])
    _log(run_id, "done", final_version=final["version"])  # log first: the live stream closes on status "done"
    db().evolve_runs.update_one({"_id": run_id}, {"$set": {"status": "done", "final_version": final["version"]}})
    return db().evolve_runs.find_one({"_id": run_id})


LIVE_SIGNAL_TEXT = ("Real users rated this answer down, rephrased the question, corrected it or regenerated it. Give a more direct, complete and actionable "
                    "answer: lead with what to do, fill every section of the output template with specifics from the "
                    "sources (names, numbers, runbook steps), and say plainly what the sources do not cover.")


def _live_signal_recommendations(agent_id: str, since: dt.datetime, until: dt.datetime,
                                 extra_event_ids: list[str] | None = None) -> int:
    from .assistant import Vault, name_registry

    w = {"$gte": since, "$lt": until}
    eps = {e["_id"]: e for e in db().episodes.find({"agent_id": agent_id, "source": "chat", "ts": w})}
    bad = [f for f in db().feedback.find({"event_id": {"$in": list(eps)}, "good": False})]
    bad += [{"event_id": i, "user_id": eps[i]["user_id"]} for i in (extra_event_ids or []) if i in eps]  # flagged by local signals
    bad += [{"event_id": x["event_id"], "user_id": x["user_id"]} for x in
            db().implicit_signals.find({"event_id": {"$in": list(eps)}, "positive": False})]
    if len({f["user_id"] for f in bad}) < 2:  # same quorum as suggestions: one unhappy person stays personal
        return 0
    seen_ev: set[str] = set()
    bad = [f for f in bad if not (f["event_id"] in seen_ev or seen_ev.add(f["event_id"]))]
    have = {r["event_id"] for r in db().recommendations.find({"event_id": {"$in": [f["event_id"] for f in bad]}}, {"event_id": 1})}
    v = Vault(name_registry())
    n = 0
    for f in bad:
        if f["event_id"] in have:
            continue
        e = eps[f["event_id"]]
        memory.recommend(agent_id, f"agent:{agent_id}", e["user_id"], e["_id"], v.detokenize(e["question_t"]),
                         v.detokenize(e.get("answer_t", "")), LIVE_SIGNAL_TEXT, e["harness_version"])
        db().recommendations.update_one({"event_id": e["_id"], "text": LIVE_SIGNAL_TEXT}, {"$set": {"source": "live_signal"}})
        n += 1
    return n


def agent_due(agent_id: str) -> bool:
    last = db().agent_cycles.find_one({"agent_id": agent_id}, sort=[("until", -1)])
    return not last or last["until"].replace(tzinfo=dt.timezone.utc) <= _now() - dt.timedelta(days=CYCLE_DAYS)


SPIKE_MIN_ANSWERS, SPIKE_RATE, SPIKE_COOLDOWN_H = 5, 0.3, 6


def failure_spikes() -> list[dict[str, Any]]:
    """Agents whose failure rate over the last 24h crossed the threshold, not improved in the cooldown."""
    from . import signals

    rates = signals.failure_rates(_now() - dt.timedelta(hours=24))
    out = []
    for aid, r in rates.items():
        if r["answers"] >= SPIKE_MIN_ANSWERS and r["rate"] >= SPIKE_RATE:
            recent = db().agent_cycles.find_one({"agent_id": aid, "created": {"$gt": _now() - dt.timedelta(hours=SPIKE_COOLDOWN_H)}})
            if not recent:
                out.append({"agent": aid, **r})
    return sorted(out, key=lambda x: -x["rate"])


def agent_tick(limit: int = 1) -> list[str]:
    """Scheduler: first any agent whose failure rate spiked (event-driven), then weekly cycles that are due."""
    done = []
    for spike in failure_spikes()[:limit]:
        audit.record("failure_spike", "signals", agent=spike["agent"], rate=spike["rate"], answers=spike["answers"])
        run_agent(spike["agent"], actor=f"failure spike: {spike['failing']}/{spike['answers']} failing in 24h")
        done.append(spike["agent"])
    if done:
        return done
    for a in db().agents.find({}, {"id": 1}):
        if len(done) >= limit:
            break
        if agent_due(a["id"]):
            run_agent(a["id"])
            done.append(a["id"])
    return done
