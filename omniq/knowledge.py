"""Governed improvement of the knowledge layer: the structured-data engine's business definitions.

The same loop that improves agents' playbooks, applied to the terms the engine answers with:

  sense    database answers people rephrased, regenerated, corrected or rated 👎, grouped by the tables involved
  propose  ONE change: a clearer description for the term behind those tables (what it measures, units, filters)
  gate     2+ distinct people (quorum) · injection screen · no restricted content · never touch a certified field
  prove    re-ask the evidence questions (as the people who asked them) before and after; keep only if more of them
           now produce an answer, and the holdout questions don't get worse
  promote  the new description lands UNCERTIFIED (description_certified=false); a person certifies it in the engine
  revert   otherwise the previous description is restored

Every step is stored in `knowledge_proposals` and the audit log. Omniq never certifies anything.
"""

from __future__ import annotations

import datetime as dt
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

from .config import ROOT
from .db import db

NEGATIVE_SIGNALS = {"rephrased", "regenerated", "followup_correction"}
RESTRICTED_TERMS = ("bluefin", "driftwood", "salary", "comp band", "cap table")
MAX_DESCRIPTION = 400
QUORUM = 2


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------------------------------- the engine's catalog
class Catalog:
    """Read and edit term definitions in Auto Ontology (admin token: var/auto-ontology-token)."""

    def __init__(self, url: str | None = None, token: str | None = None, transport: httpx.BaseTransport | None = None):
        url = (url or os.environ.get("OA_AO_URL", "http://localhost:3000")).rstrip("/")
        if token is None:
            f = Path(os.environ.get("OA_DATA_DIR", ROOT / "var")) / "auto-ontology-token"
            token = f.read_text().strip() if f.exists() else ""
        self._c = httpx.Client(base_url=url, timeout=120, transport=transport, headers={"x-api-key": token})

    @staticmethod
    def _data(r: httpx.Response) -> Any:
        r.raise_for_status()
        body = r.json()
        return body.get("data", body) if isinstance(body, dict) else body

    def terms(self) -> list[dict[str, Any]]:
        out, skip = [], 0
        while True:
            page = self._data(self._c.get("/api/terms", params={"skip": skip, "limit": 100}))
            items = page.get("items", page) if isinstance(page, dict) else page
            out += items
            if len(items) < 100:
                return out
            skip += 100

    def term(self, term_id: str) -> dict[str, Any]:
        return self._data(self._c.get(f"/api/terms/{term_id}"))

    def set_description(self, term_id: str, description: str) -> dict[str, Any]:
        """Always lands uncertified: certification is a person's decision in the engine."""
        return self._data(self._c.patch(f"/api/terms/{term_id}",
                                        json={"description": description, "description_certified": False}))

    def terms_for_tables(self, tables: list[str]) -> list[dict[str, Any]]:
        """Terms whose mapped tables overlap these, most overlap first."""
        want = {t.split(".")[-1].lower() for t in tables}
        scored = []
        for s in self.terms():
            d = self.term(s["id"])
            have = {str(t.get("name") if isinstance(t, dict) else t).split(".")[-1].lower() for t in d.get("tables") or []}
            if want & have:
                scored.append((len(want & have), d))
        return [d for _, d in sorted(scored, key=lambda x: -x[0])]


# ----------------------------------------------------------------------------------------------------- evidence
@dataclass
class Evidence:
    tables: tuple[str, ...]
    episodes: list[str] = field(default_factory=list)
    users: set[str] = field(default_factory=set)
    questions_t: list[str] = field(default_factory=list)


def collect(days: int = 7) -> list[Evidence]:
    """Database answers that people pushed back on, grouped by the tables involved."""
    since = _now() - dt.timedelta(days=days)
    bad = {s["event_id"] for s in db().implicit_signals.find({"kind": {"$in": sorted(NEGATIVE_SIGNALS)}, "ts": {"$gte": since}})}
    bad |= {f["event_id"] for f in db().feedback.find({"good": False})}
    groups: dict[tuple[str, ...], Evidence] = {}
    for ep in db().episodes.find({"ts": {"$gte": since}, "structured.status": "ok"}):
        if ep["_id"] not in bad:
            continue
        key = tuple(sorted(ep["structured"].get("tables") or []))
        if not key:
            continue
        ev = groups.setdefault(key, Evidence(key))
        ev.episodes.append(ep["_id"])
        ev.users.add(ep["user_id"])
        ev.questions_t.append(ep["question_t"])
    return sorted(groups.values(), key=lambda e: (-len(e.users), -len(e.episodes)))


# ------------------------------------------------------------------------------------------------- the decision
def gate(term: dict[str, Any], new_description: str, ev: Evidence) -> list[str]:
    from .governance import screen

    problems = []
    if len(ev.users) < QUORUM:
        problems.append(f"needs the same pattern from {QUORUM}+ people (have {len(ev.users)})")
    if term.get("description_certified"):
        problems.append("the current description is certified: a person must change it")
    text = (new_description or "").strip()
    if not text or text == (term.get("description") or "").strip():
        problems.append("no change proposed")
    if len(text) > MAX_DESCRIPTION:
        problems.append(f"description longer than {MAX_DESCRIPTION} characters")
    problems += screen(text)
    problems += [f"restricted content ('{w}')" for w in RESTRICTED_TERMS if w in text.lower()]
    return problems


DRAFTER = ("You improve the description of a business term in a company's semantic layer, so that a text-to-SQL "
           "engine picks the right tables, columns, units and filters. Be factual and short. Never include names "
           "of people, figures from the data, or instructions to the engine.")


def draft(term: dict[str, Any], ev: Evidence) -> dict[str, str]:
    """Ask the meta model for one better description. Questions are already tokenized."""
    from . import llm

    prompt = (f"Term: {term.get('name')}\nCurrent description: {term.get('description') or '(none)'}\n"
              f"Tables: {', '.join(ev.tables)}\n"
              "Questions people asked whose answers they then rephrased or corrected:\n"
              + "\n".join(f"- {q}" for q in ev.questions_t[:8])
              + f'\n\nReturn {{"description": "under {MAX_DESCRIPTION} characters", "rationale": "one sentence"}}')
    out = llm.complete_json(DRAFTER, prompt, max_tokens=400)
    return {"description": str(out.get("description", "")).strip(), "rationale": str(out.get("rationale", "")).strip()}


Scorer = Callable[[list[tuple[str, str]]], float]


def run(days: int = 7, catalog: Catalog | None = None, scorer: Scorer | None = None,
        drafter: Callable[[dict[str, Any], Evidence], dict[str, str]] = draft,
        holdout: list[tuple[str, str]] | None = None, actor: str = "knowledge-loop") -> list[dict[str, Any]]:
    """One pass of the loop. Returns the proposal records (also stored in `knowledge_proposals`)."""
    from . import audit
    from .assistant import Vault, name_registry

    catalog = catalog or Catalog()
    scorer = scorer or answer_rate
    vault = Vault(name_registry())
    records = []
    for ev in collect(days):
        terms = catalog.terms_for_tables(list(ev.tables))
        if not terms:
            continue
        term = terms[0]
        rec: dict[str, Any] = {"ts": _now(), "term_id": term["id"], "term": term.get("name"), "tables": list(ev.tables),
                               "evidence": ev.episodes, "users": sorted(ev.users), "before": term.get("description")}
        d = drafter(term, ev) if len(ev.users) >= QUORUM and not term.get("description_certified") else {"description": ""}
        rec.update(after=d.get("description"), rationale=d.get("rationale", ""))
        problems = gate(term, d.get("description", ""), ev)
        if problems:
            status = "needs_person" if term.get("description_certified") else "blocked"
            rec.update(status=status, problems=problems)
        else:
            asked = [(u, vault.detokenize(q)) for u, q in zip(_askers(ev), ev.questions_t)][:5]
            base, base_hold = scorer(asked), scorer(holdout or [])
            catalog.set_description(term["id"], d["description"])
            new, new_hold = scorer(asked), scorer(holdout or [])
            rec["scores"] = {"evidence": [base, new], "holdout": [base_hold, new_hold]}
            if new > base and new_hold >= base_hold:
                rec["status"] = "kept_uncertified"
            else:
                catalog.set_description(term["id"], term.get("description") or "")
                rec["status"] = "reverted"
        db().knowledge_proposals.insert_one(dict(rec))
        audit.record("knowledge_proposal", actor, term=rec["term_id"], status=rec["status"], users=len(ev.users),
                     evidence=len(ev.episodes))
        records.append(rec)
    return records


def _askers(ev: Evidence) -> list[str]:
    eps = {e["_id"]: e["user_id"] for e in db().episodes.find({"_id": {"$in": ev.episodes}}, {"user_id": 1})}
    return [eps.get(e, "") for e in ev.episodes]


def answer_rate(questions: list[tuple[str, str]]) -> float:
    """Share of (user, question) pairs the engine answers with SQL, asked with each person's own token."""
    from . import structured

    if not questions:
        return 1.0
    eng = structured.engine()
    ok = 0
    for uid, q in questions:
        try:
            ok += bool(eng.ask(q, uid).sql)
        except Exception:
            pass
    return ok / len(questions)
