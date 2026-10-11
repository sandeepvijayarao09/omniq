"""Load the demo company into Atlas: directory, ontology graph and embedded document chunks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from . import db as dbm
from .config import settings
from .embed import embed

TYPE_CATEGORY = {
    "Customer": "sales", "Contact": "sales", "Deal": "sales",
    "System": "engineering", "Incident": "engineering", "ActionItem": "engineering",
    "Decision": "engineering", "Project": "engineering",
    "Contract": "finance", "Vendor": "finance",
    "Objective": "general", "Policy": "general", "Person": "general", "Team": "general",
}


def _frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if text.startswith("---"):
        _, fm, body = text.split("---", 2)
        return yaml.safe_load(fm) or {}, body.strip()
    return {}, text


def chunk_markdown(title: str, body: str, max_chars: int = 1400) -> list[str]:
    """Split on headings, then pack paragraphs; every chunk is prefixed with the doc title."""
    sections = re.split(r"\n(?=#{1,3} )", body)
    chunks: list[str] = []
    for sec in sections:
        buf = ""
        for para in sec.split("\n\n"):
            if buf and len(buf) + len(para) > max_chars:
                chunks.append(buf.strip())
                buf = ""
            buf += para + "\n\n"
        if buf.strip():
            chunks.append(buf.strip())
    return [f"{title}\n\n{c}" for c in chunks if len(c) > 20]


def load_users(seed: Path) -> list[dict[str, Any]]:
    org = yaml.safe_load((seed / "org.yaml").read_text())
    return org["users"], org.get("teams", {})


def seed_all(reset: bool = True) -> dict[str, int]:
    d = dbm.db()
    seed = settings.seed_dir
    if reset:
        for c in ("users", "chunks", "objects", "links"):
            d[c].delete_many({})
    dbm.ensure_indexes(wait=False)

    users, teams = load_users(seed)
    d.users.insert_many([dict(u) for u in users])

    # ---- ontology: people and teams from the directory, the rest from ontology.yaml
    objects: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    for u in users:
        objects.append({"id": u["id"], "type": "Person", "name": u["name"], "category": "general",
                        "props": {"title": u.get("title", ""), "team": u.get("team", "")}})
        links.append({"src": u["id"], "type": "member_of", "dst": u.get("team", "")})
        if u.get("reports_to"):
            links.append({"src": u["id"], "type": "reports_to", "dst": u["reports_to"]})
    for tid, tname in teams.items():
        objects.append({"id": tid, "type": "Team", "name": tname, "category": "general", "props": {}})

    onto = yaml.safe_load((seed / "ontology.yaml").read_text())
    for otype, items in onto.items():
        for item in items or []:
            item = dict(item)
            oid = item.pop("id")
            for ltype, dsts in (item.pop("links", {}) or {}).items():
                for dst in dsts if isinstance(dsts, list) else [dsts]:
                    links.append({"src": oid, "type": ltype, "dst": dst})
            if otype == "Team":
                continue
            objects.append({
                "id": oid, "type": otype, "name": item.pop("name", oid),
                "category": item.pop("category", TYPE_CATEGORY.get(otype, "general")),
                "aliases": [a.lower() for a in item.pop("aliases", [])],
                "props": {k: str(v) for k, v in item.items()},
            })
    d.objects.insert_many(objects)
    d.links.insert_many(links)

    # ---- documents -> chunks with ACL fields and Voyage embeddings
    rows: list[dict[str, Any]] = []
    for path in sorted((seed / "docs").glob("*.md")):
        fm, body = _frontmatter(path.read_text())
        title = fm.get("title", path.stem)
        for i, text in enumerate(chunk_markdown(title, body)):
            rows.append({"doc_id": path.stem, "title": title, "chunk": i, "text": text,
                         "category": fm.get("category", "general"),
                         "team": fm.get("team", ""), "owner": fm.get("owner", "")})
    vectors = embed([r["text"] for r in rows], kind="document")
    for r, v in zip(rows, vectors):
        r["embedding"] = v
    d.chunks.insert_many(rows)
    dbm.ensure_indexes(wait=True)
    return {"users": len(users), "objects": len(objects), "links": len(links), "chunks": len(rows)}
