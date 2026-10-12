"""The harness: everything around the model, stored as versioned documents in Atlas.

One lineage per *scope*: a role ("pro") or a role-specific agent ("agent:deal_coach").
The `role` field on harness documents holds the scope. Each version records its parent, the patch that produced
it, the evidence behind the patch, and its eval scores. The assistant reads the
active version on every request, so a promotion changes behaviour immediately.

Some fields are *invariants*. The evolution loop may propose anything, but a
patch that touches an invariant, or loosens a guardrail, is rejected and the
attempt is recorded. The harness can tune itself; it cannot unlock itself.
"""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any

from .db import db

ROLES = ("basic", "pro", "manager", "admin", "executive")

# Deliberately plain starting point: the loop has to earn every improvement.
DEFAULT_HARNESS: dict[str, Any] = {
    "rules": [],
    "retrieval": {"top_k": 2, "num_candidates": 20, "rerank": False, "query_rewrite": False,
                  "graph_context": False, "use_lessons": False},
    "context": {"max_chunk_chars": 900, "include_memories": True},
    "tools": ["search_docs"],
    "model": {"temperature": 0.0, "max_tokens": 500},
    "guardrails": {"abstain_when_unsupported": False, "cite_sources": False},
    "invariants": {
        "acl_prefilter": True,       # ACL applied as the retrieval pre-filter, before ranking
        "pii_tokenization": True,    # nothing raw reaches a model
        "outbound_guard": True,      # re-scan payload before the call
        "audit": True,               # hash-chained log
        "private_categories": ["board", "hr"],
    },
}

TUNABLE = {"rules", "retrieval", "context", "tools", "model", "guardrails", "workflow"}
MAX_STEPS, MAX_TEMPLATE_CHARS = 8, 600
ALLOWED_TOOLS = {"search_docs", "ontology_lookup", "memory_recall"}
LIMITS = {"top_k": (1, 12), "num_candidates": (10, 200), "max_chunk_chars": (200, 2500),
          "max_tokens": (100, 1500), "temperature": (0.0, 1.0)}
GUARDRAILS_ONE_WAY = {"abstain_when_unsupported", "cite_sources"}  # may be turned on, never off


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def active(role: str) -> dict[str, Any]:
    doc = db().harness.find_one({"role": role, "status": "active"}, sort=[("version", -1)])
    if doc is None:
        doc = bootstrap(role)
    return doc


MAX_RULES, MAX_RULE_CHARS = 12, 450


def agent_scope(agent_id: str) -> str:
    return f"agent:{agent_id}"


def default_config(scope: str) -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_HARNESS)
    if scope.startswith("agent:"):
        tpl = db().agents.find_one({"id": scope[6:]}, {"_id": 0}) or {}
        # The template is fixed identity, not a tunable: the evolver adds rules on top of it.
        llm_cfg = tpl.get("llm") or {}
        cfg["agent"] = {"id": scope[6:], "name": tpl.get("name", scope[6:]), "org_role": tpl.get("org_role"),
                        "instructions": tpl.get("context_md") or tpl.get("instructions", ""),
                        "context_file": tpl.get("context_file", ""),
                        "llm_model": llm_cfg.get("profile") or llm_cfg.get("model"),
                        "focus_categories": tpl.get("focus_categories", []),
                        "allowed_tools": tpl.get("tools", ["search_docs"])}
        cfg["tools"] = list(tpl.get("tools", ["search_docs"]))
        if "temperature" in llm_cfg:
            cfg["model"]["temperature"] = float(llm_cfg["temperature"])
        if "max_tokens" in llm_cfg:
            cfg["model"]["max_tokens"] = int(llm_cfg["max_tokens"])
    return cfg


def bootstrap(role: str) -> dict[str, Any]:
    doc = {"role": role, "version": 1, "parent": None, "status": "active", "created": _now(),
           "config": default_config(role), "patch": None, "rationale": "default harness",
           "evidence": [], "scores": {}}
    db().harness.update_one({"role": role, "version": 1}, {"$setOnInsert": doc}, upsert=True)
    return db().harness.find_one({"role": role, "version": 1})


def reset_all() -> None:
    db().harness.delete_many({})
    for r in ROLES:
        bootstrap(r)
    for a in db().agents.find({}, {"id": 1}):
        bootstrap(agent_scope(a["id"]))


def reset_role(role: str) -> None:
    db().harness.delete_many({"role": role})
    bootstrap(role)


def lineage(role: str) -> list[dict[str, Any]]:
    return list(db().harness.find({"role": role}, {"_id": 0}).sort("version", 1))


class PatchRejected(ValueError):
    pass


def validate_patch(config: dict[str, Any], patch: dict[str, Any]) -> list[str]:
    """Return reasons the patch is unsafe or out of bounds (empty list = OK)."""
    problems: list[str] = []
    for key, val in patch.items():
        if key == "invariants":
            problems.append("patch touches locked invariants")
            continue
        if key not in TUNABLE:
            problems.append(f"unknown harness field '{key}'")
            continue
        if key == "tools":
            allowed = set(config.get("agent", {}).get("allowed_tools") or ALLOWED_TOOLS) & ALLOWED_TOOLS
            bad = set(val) - allowed
            if bad:
                problems.append(f"tools not in allow-list: {sorted(bad)}")
            if "search_docs" not in val:
                problems.append("search_docs cannot be removed")
        if key == "guardrails":
            for g, on in val.items():
                if g in GUARDRAILS_ONE_WAY and config["guardrails"].get(g) and not on:
                    problems.append(f"guardrail '{g}' may not be switched off")
        if key == "workflow":  # an agent's procedure and output template, on top of its context file
            if not isinstance(val, dict) or set(val) - {"procedure", "template"}:
                problems.append("workflow must be {procedure: [steps], template: str}")
            else:
                steps = val.get("procedure", [])
                if steps and (not isinstance(steps, list) or len(steps) > MAX_STEPS
                              or not all(isinstance(x, str) and len(x) <= 200 for x in steps)):
                    problems.append(f"workflow.procedure must be up to {MAX_STEPS} short steps")
                if "template" in val and (not isinstance(val["template"], str) or len(val["template"]) > MAX_TEMPLATE_CHARS):
                    problems.append(f"workflow.template must be a string under {MAX_TEMPLATE_CHARS} chars")
        if key == "rules":
            if not isinstance(val, list) or not all(isinstance(r, str) for r in val):
                problems.append("rules must be a list of strings")
            elif len(val) > MAX_RULES or any(len(r) > MAX_RULE_CHARS for r in val):
                problems.append(f"rules exceed limits ({MAX_RULES} rules, {MAX_RULE_CHARS} chars each)")
        if isinstance(val, dict):
            for k, v in val.items():
                if k in LIMITS and isinstance(v, (int, float)):
                    lo, hi = LIMITS[k]
                    if not lo <= v <= hi:
                        problems.append(f"{key}.{k}={v} outside [{lo}, {hi}]")
    text = str(patch).lower()
    for phrase in ("ignore access", "ignore previous", "bypass", "disable pii", "reveal", "skip audit",
                   "without restriction", "all documents", "other employees' "):
        if phrase in text:
            problems.append(f"patch text contains forbidden instruction '{phrase}'")
    return problems


def minimize(config: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Drop fields that wouldn't change anything, so every version's diff shows only real changes."""
    out: dict[str, Any] = {}
    for key, val in (patch or {}).items():
        if key not in TUNABLE and key != "invariants":  # drop stray fields like 'rationale' or 'lesson'
            continue
        cur = config.get(key)
        if isinstance(val, dict) and isinstance(cur, dict):
            sub = {k: v for k, v in val.items() if cur.get(k) != v}
            if sub:
                out[key] = sub
        elif val != cur:
            out[key] = val
    return out


def apply_patch(config: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    new = copy.deepcopy(config)
    for key, val in patch.items():
        if isinstance(val, dict) and isinstance(new.get(key), dict):
            new[key].update(val)
        else:
            new[key] = val
    new["invariants"] = copy.deepcopy(config["invariants"])  # belt and braces
    if "agent" in config:
        new["agent"] = copy.deepcopy(config["agent"])
    return new


def propose(role: str, patch: dict[str, Any], rationale: str, evidence: list[Any]) -> dict[str, Any]:
    """Store a candidate version (status 'candidate'), or a 'blocked' record if unsafe."""
    from . import governance

    base = active(role)
    problems = validate_patch(base["config"], patch) + governance.check_types(patch)
    if not problems:
        problems = governance.review_rules(base["config"], patch)
    version = (db().harness.find_one({"role": role}, sort=[("version", -1)]) or {"version": 0})["version"] + 1
    doc = {"role": role, "version": version, "parent": base["version"], "created": _now(),
           "patch": patch, "rationale": rationale, "evidence": evidence, "scores": {},
           "status": "blocked" if problems else "candidate",
           "config": base["config"] if problems else apply_patch(base["config"], patch),
           "problems": problems}
    db().harness.insert_one(doc)
    doc.pop("_id", None)
    return doc


def promote(role: str, version: int, scores: dict[str, Any]) -> None:
    h = db().harness
    h.update_many({"role": role, "status": "active"}, {"$set": {"status": "superseded"}})
    h.update_one({"role": role, "version": version}, {"$set": {"status": "active", "scores": scores}})


def reject(role: str, version: int, scores: dict[str, Any], reason: str) -> None:
    db().harness.update_one({"role": role, "version": version},
                            {"$set": {"status": "rolled_back", "scores": scores, "reject_reason": reason}})


def set_scores(role: str, version: int, scores: dict[str, Any]) -> None:
    db().harness.update_one({"role": role, "version": version}, {"$set": {"scores": scores}})
