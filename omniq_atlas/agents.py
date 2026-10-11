"""Role-specific agent templates: who may use each agent, its persona, focus and tools.

Templates live in data/agents.yaml and are loaded into Atlas `agents`. Each agent gets its own
harness lineage (scope "agent:<id>") and its own memory layer, so it improves independently.
"""

from __future__ import annotations

from typing import Any

import yaml

from .config import ROOT
from .db import db
from .policy import User

AGENTS_FILE = ROOT / "data" / "agents.yaml"


def load_templates() -> int:
    if not AGENTS_FILE.exists():
        return 0
    items = yaml.safe_load(AGENTS_FILE.read_text()) or []
    db().agents.delete_many({})
    if items:
        db().agents.insert_many([dict(i) for i in items])
    db().agents.create_index("id", unique=True)
    return len(items)


def get(agent_id: str) -> dict[str, Any] | None:
    return db().agents.find_one({"id": agent_id}, {"_id": 0})


def eligible(user: User, agent: dict[str, Any]) -> bool:
    aud = agent.get("audience") or {}
    roles = aud.get("roles") or []
    depts = aud.get("departments") or []
    if roles and user.role not in roles:
        return False
    # executives and admins pass department gates; everyone else must be in the department
    return not depts or user.department in depts or user.role in ("executive", "admin")


def for_user(user: User) -> list[dict[str, Any]]:
    return [a for a in db().agents.find({}, {"_id": 0}).sort("name", 1) if eligible(user, a)]
