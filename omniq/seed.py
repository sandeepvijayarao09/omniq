"""Load the company into the document store: directory, ontology graph and embedded document chunks.

The ontology is built and checked against its schema by ``ontology.build_graph``; documents are then linked to the
objects they mention, and the ontology's edit log is replayed over the fresh records."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from . import db as dbm
from . import ontology
from .config import settings
from .embed import embed


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
    dbm.ensure_indexes()

    users, teams = load_users(seed)
    d.users.insert_many([dict(u) for u in users])

    # ---- ontology: people, teams and who leads them from the directory, the rest from ontology.yaml (schema-checked)
    onto = yaml.safe_load((seed / "ontology.yaml").read_text())
    objects, links = ontology.build_graph(users, teams, onto)
    d.objects.insert_many(objects)
    d.links.insert_many(links)

    # ---- documents -> chunks with ACL fields and local embeddings
    rows: list[dict[str, Any]] = []
    docs: list[dict[str, Any]] = []
    for path in sorted((seed / "docs").glob("*.md")):
        fm, body = _frontmatter(path.read_text())
        title = fm.get("title", path.stem)
        docs.append({"doc_id": path.stem, "title": title, "text": body, "category": fm.get("category", "general"),
                     "team": fm.get("team", ""), "owner": fm.get("owner", "")})
        for i, text in enumerate(chunk_markdown(title, body)):
            rows.append({"doc_id": path.stem, "title": title, "chunk": i, "text": text,
                         "category": fm.get("category", "general"),
                         "team": fm.get("team", ""), "owner": fm.get("owner", "")})
    vectors = embed([r["text"] for r in rows], kind="document")
    for r, v in zip(rows, vectors):
        r["embedding"] = v
    d.chunks.insert_many(rows)
    mentions = ontology.mention_links(docs, objects)  # each carries its document's access fields
    if mentions:
        d.links.insert_many(mentions)
    edits = ontology.replay()  # actions people ran, on top of the fresh source records
    dbm.ensure_indexes()
    return {"users": len(users), "objects": len(objects), "links": len(links), "mentions": len(mentions),
            "edits_replayed": edits, "chunks": len(rows)}
