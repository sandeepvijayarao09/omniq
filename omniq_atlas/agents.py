"""Role-specific agents for a four-role engineering org: Developer, Product Manager, Director, CTO.

Each agent is one Markdown file, data/agents/<org_role>/<id>.md:
  front matter  identity, audience, LLM connection (model, temperature, max tokens), retrieval focus,
                tools, starter questions
  body          the agent's context layer, sent to the model verbatim as its instructions

Hierarchy: CTO (3) > Director (2) > Product Manager / Developer (1). A user can use the agents of their
own org role and, when an agent inherits upward, the agents of every role below them. Executives and
admins can use all. Org roles never widen document access: retrieval still runs under the person's ACL.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config import ROOT
from .db import db
from .policy import User

AGENTS_DIR = ROOT / "data" / "agents"

ORG_ROLES = {
    "developer": {"title": "Developer", "rank": 1},
    "product_manager": {"title": "Product Manager", "rank": 1},
    "director": {"title": "Director", "rank": 2},
    "cto": {"title": "CTO", "rank": 3},
}


def org_role(user: User) -> str | None:
    """Derive a person's org role from the directory (role, department, title)."""
    if user.role == "executive" and "technology" in user.title.lower():
        return "cto"
    if user.role == "manager" and user.department in ("engineering", "product"):
        return "director"
    if user.role == "pro" and user.department == "product":
        return "product_manager"
    if user.role == "pro" and user.department == "engineering":
        return "developer"
    return None


def parse(path: Path) -> dict[str, Any]:
    raw = path.read_text()
    _, fm, body = raw.split("---", 2)
    meta = yaml.safe_load(fm) or {}
    meta["context_md"] = body.strip()
    meta["context_file"] = str(path.relative_to(ROOT))
    return meta


def load_templates() -> int:
    items = [parse(p) for p in sorted(AGENTS_DIR.glob("*/*.md"))]
    db().agents.delete_many({})
    if items:
        db().agents.insert_many([dict(i) for i in items])
    db().agents.create_index("id", unique=True)
    return len(items)


def get(agent_id: str) -> dict[str, Any] | None:
    return db().agents.find_one({"id": agent_id}, {"_id": 0})


def eligible(user: User, agent: dict[str, Any]) -> bool:
    if user.role in ("executive", "admin"):
        return True
    mine = org_role(user)
    if not mine:
        return False
    aud = agent.get("audience") or {}
    roles = aud.get("org_roles") or [agent.get("org_role")]
    if mine in roles:
        return True
    if aud.get("inherit_upward", True):
        floor = min(ORG_ROLES[r]["rank"] for r in roles if r in ORG_ROLES)
        return ORG_ROLES[mine]["rank"] > floor
    return False


def for_user(user: User) -> list[dict[str, Any]]:
    order = {r: i for i, r in enumerate(["developer", "product_manager", "director", "cto"])}
    rows = [a for a in db().agents.find({}, {"_id": 0, "context_md": 0}) if eligible(user, a)]
    return sorted(rows, key=lambda a: (order.get(a.get("org_role"), 9), a["name"]))
