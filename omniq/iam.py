"""IAM: role-based access control from data/iam/policies.yaml, evaluated like AWS IAM.

explicit Deny > Allow > implicit deny. Roles inherit statements. Variables (${user.manages}, ${user.projects}…)
expand to every value the person has, so "doc:hr/${user.manages}" means "HR notes of any team I manage".

Document access for org members is *compiled* from these policies into the categories and HR teams that
policy.py turns into the $vectorSearch pre-filter, so the database enforces exactly what the policy says.
People outside the four org roles keep the legacy role grants in policy.py.
"""

from __future__ import annotations

import fnmatch
import itertools
import re
from functools import lru_cache
from typing import Any

import yaml

from .config import ROOT

POLICY_FILE = ROOT / "data" / "iam" / "policies.yaml"
PROJECTS_FILE = ROOT / "data" / "projects.yaml"
DOC_CATEGORIES = ("general", "engineering", "sales", "finance", "board")
VAR = re.compile(r"\$\{user\.(\w+)\}")


@lru_cache(maxsize=1)
def policies() -> dict[str, Any]:
    return yaml.safe_load(POLICY_FILE.read_text())


@lru_cache(maxsize=1)
def projects() -> list[dict[str, Any]]:
    return yaml.safe_load(PROJECTS_FILE.read_text()) if PROJECTS_FILE.exists() else []


def org_role(user) -> str | None:
    """Org role from the directory: CTO, Director (eng/product managers), PM (product team), Developer (engineers)."""
    if user.role == "executive" and "technology" in user.title.lower():
        return "cto"
    if user.role == "manager" and user.department in ("engineering", "product"):
        return "director"
    if user.role == "pro" and user.department == "product":
        return "product_manager"
    if user.role == "pro" and user.department == "engineering":
        return "developer"
    return None


def user_projects(user_id: str) -> list[dict[str, Any]]:
    return [p for p in projects() if user_id in (p.get("members") or {})]


def variables(user) -> dict[str, list[str]]:
    manages = list(user.manages) if user.role == "manager" else []
    if user.role == "manager" and not manages:
        manages = [user.team]
    return {"id": [user.id], "team": [user.team], "manages": manages,
            "projects": [p["id"] for p in user_projects(user.id)] or ["__none__"]}


def role_chain(role: str) -> list[str]:
    """The role plus everything it inherits, most specific first, without duplicates."""
    seen: list[str] = []

    def visit(r: str) -> None:
        if r in seen or r not in policies()["roles"]:
            return
        seen.append(r)
        for parent in policies()["roles"][r].get("inherits", []):
            visit(parent)

    visit(role)
    return seen


def _expand(pattern: str, vars_: dict[str, list[str]]) -> list[str]:
    names = VAR.findall(pattern)
    if not names:
        return [pattern]
    combos = itertools.product(*[vars_.get(n, ["__none__"]) for n in names])
    out = []
    for combo in combos:
        p = pattern
        for n, v in zip(names, combo):
            p = p.replace("${user.%s}" % n, v, 1)
        out.append(p)
    return out


def statements(user) -> list[dict[str, Any]]:
    role = org_role(user)
    if not role:
        return []
    out = []
    vars_ = variables(user)
    for r in role_chain(role):
        for st in policies()["roles"][r].get("statements", []):
            out.append({**st, "role": r,
                        "expanded": [x for pat in st["resources"] for x in _expand(pat, vars_)]})
    return out


def evaluate(user, action: str, resources: list[str] | str) -> dict[str, Any]:
    """Decision for one request. Returns {decision, sid, role, reason}."""
    resources = [resources] if isinstance(resources, str) else resources
    role = org_role(user)
    if not role:
        return {"decision": "Legacy", "sid": None, "role": None,
                "reason": f"{user.name} is outside the four org roles; legacy role grants apply"}
    allow = None
    for st in statements(user):
        if not any(fnmatch.fnmatchcase(action, a) for a in st["actions"]):
            continue
        if not any(fnmatch.fnmatchcase(res, pat) for res in resources for pat in st["expanded"]):
            continue
        if st["effect"] == "Deny":
            return {"decision": "Deny", "sid": st["sid"], "role": st["role"],
                    "reason": f"explicit Deny in {st['role']}.{st['sid']}"}
        allow = allow or st
    if allow:
        return {"decision": "Allow", "sid": allow["sid"], "role": allow["role"],
                "reason": f"allowed by {allow['role']}.{allow['sid']}"}
    return {"decision": "Deny", "sid": None, "role": None, "reason": "no statement allows it (implicit deny)"}


@lru_cache(maxsize=512)
def allowed(user, action: str, resource: str) -> bool:
    return evaluate(user, action, resource)["decision"] == "Allow"


# ---------------------------------------------------------------- compiled document access

@lru_cache(maxsize=1)
def hr_teams_known() -> tuple[str, ...]:
    """Every team that has HR notes, read from the seed documents' front matter (no database needed)."""
    teams = set()
    for f in (ROOT / "data" / "seed" / "docs").glob("*.md"):
        fm = yaml.safe_load(f.read_text().split("---", 2)[1]) or {}
        if fm.get("category") == "hr" and fm.get("team"):
            teams.add(fm["team"])
    return tuple(sorted(teams))


@lru_cache(maxsize=512)
def categories(user) -> frozenset[str]:
    return frozenset(c for c in DOC_CATEGORIES if allowed(user, "docs:read", f"doc:category/{c}"))


@lru_cache(maxsize=512)
def hr_teams(user) -> tuple[str, ...]:
    return tuple(t for t in hr_teams_known() if allowed(user, "docs:read", f"doc:hr/{t}"))


def summary(user) -> dict[str, Any]:
    role = org_role(user)
    from .db import db

    agent_rows = list(db().agents.find({}, {"_id": 0, "id": 1, "org_role": 1}))
    usable = [a["id"] for a in agent_rows if allowed(user, "agent:use", f"agent:{a['org_role']}/{a['id']}")]
    improvable = [a["id"] for a in agent_rows
                  if allowed(user, "improve:run", f"harness:agent/{a['org_role']}/{a['id']}")]
    return {"org_role": role, "role_chain": role_chain(role) if role else [], "variables": variables(user),
            "doc_categories": sorted(categories(user)) if role else None, "hr_teams": list(hr_teams(user)) if role else None,
            "agents_usable": usable, "agents_improvable": improvable,
            "projects": [{"id": p["id"], "name": p["name"], **p["members"][user.id]} for p in user_projects(user.id)],
            "statements": [{k: st[k] for k in ("role", "sid", "effect", "actions", "resources", "expanded")}
                           for st in statements(user)]}
