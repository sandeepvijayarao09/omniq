"""Role and employee context layers, plus @-mention routing.

Every turn the model receives, in order: governance base prompt → role file (data/roles/<org_role>.md)
→ the employee's persona (data/people/<emp-id>.md) → the @-mentioned agent's file → learned rules and
the employee's own preferences → ACL-filtered sources. Role and persona files only shape *how* to answer;
*what* is true still comes from sources the person may read.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .config import ROOT

ROLES_DIR = ROOT / "data" / "roles"
PEOPLE_DIR = ROOT / "data" / "people"
MENTION = re.compile(r"(?<![\w@])@([a-z][a-z0-9_-]{2,40})", re.I)


def _read(path: Path) -> tuple[dict[str, Any], str]:
    if not path.exists():
        return {}, ""
    _, fm, body = path.read_text().split("---", 2)
    return yaml.safe_load(fm) or {}, body.strip()


@lru_cache(maxsize=64)
def role(org_role: str) -> tuple[dict[str, Any], str]:
    return _read(ROLES_DIR / f"{org_role}.md")


@lru_cache(maxsize=256)
def person(emp_id: str) -> tuple[dict[str, Any], str]:
    return _read(PEOPLE_DIR / f"{emp_id}.md")


def team() -> list[dict[str, Any]]:
    """The people who have a persona file, as an org chart (CTO first)."""
    order = {"cto": 0, "director": 1, "product_manager": 2, "developer": 3}
    rows = [_read(p)[0] for p in sorted(PEOPLE_DIR.glob("*.md"))]
    return sorted(rows, key=lambda m: (order.get(m.get("org_role"), 9), m.get("name", "")))


def layers(emp_id: str, org_role: str | None) -> list[tuple[str, str]]:
    """(label, markdown) pairs for the system prompt."""
    out = []
    if org_role:
        _, body = role(org_role)
        if body:
            out.append((f"data/roles/{org_role}.md", body))
    _, body = person(emp_id)
    if body:
        out.append((f"data/people/{emp_id}.md", body))
    return out


def parse_mention(text: str, known: set[str]) -> tuple[str | None, str]:
    """Find the first @agent in the message. Accepts @deploy_guide or @deploy-guide. Returns (agent_id, cleaned text)."""
    for m in MENTION.finditer(text or ""):
        aid = m.group(1).lower().replace("-", "_")
        if aid in known:
            cleaned = (text[: m.start()] + text[m.end():]).strip(" ,:")
            return aid, re.sub(r"\s{2,}", " ", cleaned).strip() or text
    return None, text


def _section(md: str, title: str) -> str:
    m = re.search(rf"^## {re.escape(title)}\s*\n(.*?)(?=^## |\Z)", md, re.S | re.M)
    return m.group(1).strip() if m else ""


def audience_card(emp_id: str, org_role: str | None) -> tuple[str, list[str]]:
    """A compact 'who is asking' card distilled from the role and persona files (small models do better with
    a short card than with whole documents). Returns (card, files it came from)."""
    files, lines = [], []
    pmeta, pbody = person(emp_id)
    rmeta, rbody = role(org_role) if org_role else ({}, "")
    if pbody:
        files.append(f"data/people/{emp_id}.md")
        lines.append(f"Asker: {pmeta.get('name')}, {pmeta.get('title')}" +
                     (f" (reports to {pmeta['reports_to_name']})" if pmeta.get("reports_to_name") else ""))
        about = _section(pbody, "About me")
        week = re.search(r"This week I ([^\n]+)", about)
        if week:
            lines.append(f"This week they {week.group(1).rstrip('.')}.")
        proj = _section(pbody, "My projects and scope of work")
        if proj and "No project" not in proj:
            items = re.findall(r"\*\*(.+?)\*\*.*?My scope: ([^\n]+)", proj, re.S)
            if items:
                lines.append("Their projects: " + "; ".join(f"{n} (their scope: {sc.strip()})" for n, sc in items))
        prefs = [l[2:].strip() for l in _section(pbody, "How I like answers").splitlines() if l.startswith("- ")]
        if prefs:
            lines.append("They like answers that: " + " ".join(prefs))
    if rbody:
        files.insert(0, f"data/roles/{org_role}.md")
        framing = _section(rbody, "How to frame answers for this role")
        if framing:
            lines.append(f"Role ({rmeta.get('title', org_role)}): {framing}")
    return "\n".join(f"- {l}" for l in lines), files
