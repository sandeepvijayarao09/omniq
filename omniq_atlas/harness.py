"""The harness: everything around the model, stored as versioned documents in Atlas.

One lineage per role. Each version records its parent, the patch that produced
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
        "acl_prefilter": True,       # ACL enforced inside $vectorSearch
        "pii_tokenization": True,    # nothing raw reaches a model
        "outbound_guard": True,      # re-scan payload before the call
        "audit": True,               # hash-chained log
        "private_categories": ["board", "hr"],
    },
}

TUNABLE = {"rules", "retrieval", "context", "tools", "model", "guardrails"}
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


def bootstrap(role: str) -> dict[str, Any]:
    doc = {"role": role, "version": 1, "parent": None, "status": "active", "created": _now(),
           "config": copy.deepcopy(DEFAULT_HARNESS), "patch": None, "rationale": "default harness",
           "evidence": [], "scores": {}}
    db().harness.update_one({"role": role, "version": 1}, {"$setOnInsert": doc}, upsert=True)
    return db().harness.find_one({"role": role, "version": 1})


def reset_all() -> None:
    db().harness.delete_many({})
    for r in ROLES:
        bootstrap(r)


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
            bad = set(val) - ALLOWED_TOOLS
            if bad:
                problems.append(f"tools not in allow-list: {sorted(bad)}")
            if "search_docs" not in val:
                problems.append("search_docs cannot be removed")
        if key == "guardrails":
            for g, on in val.items():
                if g in GUARDRAILS_ONE_WAY and config["guardrails"].get(g) and not on:
                    problems.append(f"guardrail '{g}' may not be switched off")
        if key == "rules" and not isinstance(val, list):
            problems.append("rules must be a list")
        if isinstance(val, dict):
            for k, v in val.items():
                if k in LIMITS and isinstance(v, (int, float)):
                    lo, hi = LIMITS[k]
                    if not lo <= v <= hi:
                        problems.append(f"{key}.{k}={v} outside [{lo}, {hi}]")
    text = str(patch).lower()
    for phrase in ("ignore access", "bypass", "disable pii", "reveal", "skip audit"):
        if phrase in text:
            problems.append(f"patch text contains forbidden instruction '{phrase}'")
    return problems


def apply_patch(config: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    new = copy.deepcopy(config)
    for key, val in patch.items():
        if isinstance(val, dict) and isinstance(new.get(key), dict):
            new[key].update(val)
        else:
            new[key] = val
    new["invariants"] = copy.deepcopy(config["invariants"])  # belt and braces
    return new


def propose(role: str, patch: dict[str, Any], rationale: str, evidence: list[Any]) -> dict[str, Any]:
    """Store a candidate version (status 'candidate'), or a 'blocked' record if unsafe."""
    base = active(role)
    problems = validate_patch(base["config"], patch)
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
