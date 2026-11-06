"""See, edit and publish agents, with a full timeline of every update.

Two levels of editing:
  personal edit   `agent_overrides` {user_id, agent_id, notes, procedure, template, version, history[]}
                  applies only to that person, immediately; versioned; screened for injection.
  publish         a new version of the agent's context file for everyone (the `context_layers` collection), allowed only
                  when IAM grants harness:approve on that agent. It must pass the injection screen and the leak
                  safety gate, and becomes a new playbook version (audited, rollback-able).
timeline()        merges context-file versions, playbook versions, recommendations (incl. 7-day cycles) and
                  this person's personal edits into one dated list.
"""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any

from . import audit, contextdb, governance, harness, iam
from .db import db


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def get_override(user_id: str, agent_id: str) -> dict[str, Any] | None:
    return db().agent_overrides.find_one({"user_id": user_id, "agent_id": agent_id}, {"_id": 0})


def _validate(notes: str, procedure: list[str], template: str) -> list[str]:
    problems = []
    for text in [notes, template, *procedure]:
        problems += governance.screen(text or "")
    if len(notes) > 1200:
        problems.append("notes must be under 1200 characters")
    if len(procedure) > harness.MAX_STEPS or any(len(x) > 200 for x in procedure):
        problems.append(f"up to {harness.MAX_STEPS} steps of 200 characters")
    if len(template) > harness.MAX_TEMPLATE_CHARS:
        problems.append(f"template must be under {harness.MAX_TEMPLATE_CHARS} characters")
    return problems


def save_override(user, agent_id: str, notes: str, procedure: list[str], template: str, profile: str = "") -> dict[str, Any]:
    notes, template = (notes or "").strip(), (template or "").strip()
    procedure = [p.strip() for p in procedure or [] if p.strip()]
    problems = _validate(notes, procedure, template)
    from . import llm

    if profile and profile not in llm.registry()["profiles"]:
        problems.append(f"unknown model profile '{profile}'")
    if problems:
        audit.record("agent_edit_refused", user.id, agent=agent_id, problems=problems)
        raise ValueError("; ".join(problems))
    cur = get_override(user.id, agent_id) or {"version": 0, "history": []}
    entry = {"version": cur["version"] + 1, "notes": notes, "procedure": procedure, "template": template,
             "profile": profile, "ts": _now()}
    db().agent_overrides.update_one({"user_id": user.id, "agent_id": agent_id},
                                    {"$set": {"notes": notes, "procedure": procedure, "template": template, "profile": profile,
                                              "version": entry["version"], "updated": _now()},
                                     "$push": {"history": {"$each": [entry], "$slice": -20}}}, upsert=True)
    audit.record("agent_personal_edit", user.id, agent=agent_id, version=entry["version"])
    return get_override(user.id, agent_id)


def reset_override(user, agent_id: str) -> None:
    db().agent_overrides.delete_one({"user_id": user.id, "agent_id": agent_id})
    audit.record("agent_personal_edit_reset", user.id, agent=agent_id)


def apply_override(cfg: dict[str, Any], user_id: str, agent_id: str) -> tuple[dict[str, Any], int | None]:
    """The config the assistant uses for this person: their personal edits layered over the shared playbook."""
    o = get_override(user_id, agent_id)
    if not o:
        return cfg, None
    cfg = copy.deepcopy(cfg)
    if o.get("notes"):
        cfg["agent"]["instructions"] = cfg["agent"]["instructions"].rstrip() + "\n\n## My personal edits\n" + o["notes"]
    wf = dict(cfg.get("workflow") or {})
    if o.get("procedure"):
        wf["procedure"] = o["procedure"]
    if o.get("template"):
        wf["template"] = o["template"]
    if wf:
        cfg["workflow"] = wf
    if o.get("profile"):  # this person's model choice for this agent
        cfg["agent"]["llm_model"] = o["profile"]
    return cfg, o["version"]


def can_publish(user, agent: dict[str, Any]) -> bool:
    if iam.org_role(user):
        return iam.allowed(user, "harness:approve", f"harness:agent/{agent['org_role']}/{agent['id']}")
    return user.role in ("executive", "admin")


def publish(user, agent_id: str, markdown: str, reason: str = "") -> dict[str, Any]:
    agent = db().agents.find_one({"id": agent_id}, {"_id": 0})
    if not agent:
        raise KeyError(agent_id)
    if not can_publish(user, agent):
        raise governance.NotAllowed(f"IAM: {user.name} may not publish changes to {agent['name']}")
    markdown = (markdown or "").strip()
    problems = governance.screen(markdown) + ([] if 200 <= len(markdown) <= 12000 else ["context file must be 200-12000 characters"])
    for term in ("bluefin", "driftwood", "salary", "comp band", "cap table"):  # context files carry no restricted facts
        if term in markdown.lower():
            problems.append(f"context files may not contain restricted content ('{term}')")
    if problems:
        audit.record("agent_publish_refused", user.id, agent=agent_id, problems=problems)
        raise ValueError("; ".join(problems))
    scope = harness.agent_scope(agent_id)
    cur = harness.active(scope)
    cfg = copy.deepcopy(cur["config"])
    cfg["agent"]["instructions"] = markdown
    gate = governance.safety_gate(scope, cfg, cur["version"] + 1)
    if not gate["ok"]:
        audit.record("agent_publish_refused", user.id, agent=agent_id, problems=gate["leaked"])
        raise ValueError(f"safety gate failed: {gate['leaked'][:2]}")
    version = (db().harness.find_one({"role": scope}, sort=[("version", -1)]) or {"version": 0})["version"] + 1
    db().harness.update_many({"role": scope, "status": "active"}, {"$set": {"status": "superseded"}})
    db().harness.insert_one({"role": scope, "version": version, "parent": cur["version"], "status": "active",
                             "created": _now(), "config": cfg, "patch": {"context_file": "edited"},
                             "rationale": reason or f"context file edited by {user.name}", "evidence": [],
                             "scores": {"safety_gate": {"checked": gate["checked"], "leaked": []}}, "edited_by": user.id})
    ctx = contextdb.update("agent", agent_id, markdown, reason=reason or f"edited by {user.name}", source="edit",
                           editor=user.id, harness_version=version)
    db().agents.update_one({"id": agent_id}, {"$set": {"context_md": markdown}})
    audit.record("agent_published", user.id, agent=agent_id, harness_version=version, context_version=ctx["version"])
    return {"harness_version": version, "context_version": ctx["version"], "safety_gate": gate["checked"]}


def timeline(user, agent_id: str) -> list[dict[str, Any]]:
    scope = harness.agent_scope(agent_id)
    rows: list[dict[str, Any]] = []
    for h in contextdb.history("agent", agent_id):
        rows.append({"ts": h["created"], "kind": "context file", "what": f"context v{h['version']} ({h['status']})",
                     "why": h.get("reason", ""), "by": h.get("editor") or h.get("source")})
    for v in harness.lineage(scope):
        if v["version"] == 1:
            continue
        rows.append({"ts": v["created"], "kind": "playbook", "what": f"v{v['version']} {v['status'].replace('_', ' ')}",
                     "why": (v.get("rationale") or "")[:220], "by": v.get("edited_by") or "improvement loop",
                     "change": v.get("patch"), "reject": v.get("reject_reason") or "; ".join(v.get("problems") or [])})
    for r in db().recommendations.find({"scope": scope}, {"embedding": 0, "answer": 0}).sort("ts", -1).limit(20):
        rows.append({"ts": r["ts"], "kind": "7-day cycle" if r.get("source") == "weekly_cycle" else "suggestion",
                     "what": f"suggestion {r.get('status')}" + (f" → v{r['handled_by']}" if r.get("handled_by") else ""),
                     "why": r["text"], "by": r["user_id"] if r["user_id"] == user.id else "a colleague"})
    for r in db().improvement_requests.find({"agent_id": agent_id}).sort("ts", -1).limit(20):
        rows.append({"ts": r["ts"], "kind": "improvement request",
                     "what": f"{r['source'].replace('_', ' ')} · {r['status']} · on v{r['harness_version']}",
                     "why": r["request_t"], "by": r["user_id"] if r["user_id"] == user.id else "a colleague"})
    o = get_override(user.id, agent_id)
    for h in (o or {}).get("history", []):
        rows.append({"ts": h["ts"], "kind": "my edit", "what": f"personal edit v{h['version']}",
                     "why": (h.get("notes") or "")[:160] or "steps/template changed", "by": user.id})
    for r in rows:
        if hasattr(r["ts"], "replace") and getattr(r["ts"], "tzinfo", None) is None:
            r["ts"] = r["ts"].replace(tzinfo=dt.timezone.utc)
    return sorted(rows, key=lambda r: r["ts"], reverse=True)
