"""Structured data: answer questions from company databases through a structured-data engine, under Omniq's access.

The engine (NVIDIA Auto Ontology by default) owns the database side: it compiles the warehouse into business terms,
measures and join paths, writes SQL, checks it against the data and runs it. Omniq owns everything around it:

  who may ask      IAM action `data:query`
  what it may see  every table the engine's SQL touches must be in a category the person may read
                   (data/warehouse/acl.yaml, same categories as documents). Anything else is withheld: the rows are
                   never shown to the person or sent to a model. SQL that cannot be parsed is withheld too.
  answerability    the engine's coverage grade decides whether to ask at all, so the agent says "the data can't
                   answer that" instead of guessing (harness knob `retrieval.structured_min_coverage`)
  privacy          results enter the prompt like any other source, so the vault tokenizes them; finance/board
                   results route to private models
  audit            the SQL, tables and decision are stored on the episode

The engine sits behind `Engine`, so another text-to-SQL engine can replace Auto Ontology.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import httpx
import yaml

from .config import ROOT

ACL_FILE = ROOT / "data" / "warehouse" / "acl.yaml"
MAX_ROWS = 25
MAX_CELL = 120


# --------------------------------------------------------------------------------------------- access to tables
@lru_cache(maxsize=1)
def load_acl() -> dict[str, Any]:
    return yaml.safe_load(ACL_FILE.read_text())


def table_category(name: str, acl: dict[str, Any] | None = None) -> str:
    acl = acl or load_acl()
    name = name.lower()
    if name in (acl.get("tables") or {}):
        return acl["tables"][name]
    schema = name.split(".")[0] if "." in name else ""
    return (acl.get("schemas") or {}).get(schema, acl.get("default", "general"))


@lru_cache(maxsize=1)
def catalog() -> list[dict[str, Any]]:
    """The warehouse catalog written by scripts/build_warehouse.py (tables, columns, notes, values, category)."""
    f = Path(os.environ.get("OA_DATA_DIR", ROOT / "var")) / "warehouse" / "catalog.json"
    try:
        return list(json.loads(f.read_text())["tables"])
    except (OSError, ValueError, KeyError):
        return []


@lru_cache(maxsize=1)
def known_tables() -> frozenset[str]:
    """Qualified table names from the warehouse catalog, used to resolve unqualified names in SQL."""
    return frozenset(t["name"].lower() for t in catalog())


def catalog_coverage(user, question: str) -> dict[str, Any]:
    """Omniq's own answerability check, independent of the engine: are the question's terms present in the tables
    this person may query (names, columns, column notes, categorical values)? The engine's coverage grade is an
    embedding-distance cut-off tuned for its default embedder and scored a question about advertising spend as fully
    covered on local embeddings, then invented SQL for it. Restricted tables are left out, so they grade like
    nonexistent ones."""
    from . import answerability, iam

    allowed = iam.categories(user)
    sources = []
    for t in catalog():
        if t.get("category") not in allowed:
            continue
        words = [t["name"], *t.get("columns", []), t.get("description", ""), *t.get("column_notes", {}).values(),
                 *(v for vals in t.get("values", {}).values() for v in vals)]
        sources.append({"title": t["name"], "text": " ".join(str(w).replace("_", " ") for w in words)})
    return answerability.grade(question, sources)


def sql_tables(sql: str) -> list[str] | None:
    """Every base table a query reads, schema-qualified where possible. None if the SQL can't be parsed."""
    import sqlglot
    from sqlglot import exp

    try:
        trees = [t for t in sqlglot.parse(sql, read="postgres") if t is not None]
    except sqlglot.errors.ParseError:
        return None
    if not trees:
        return None
    out: set[str] = set()
    for tree in trees:
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        for t in tree.find_all(exp.Table):
            name = (t.name or "").lower()
            if not name or (not t.db and name in ctes):
                continue
            out.add(f"{t.db.lower()}.{name}" if t.db else name)
    return sorted(out)


def _qualify(name: str) -> str | None:
    """The catalog's name for a table, or None. Tables outside the catalog are never attributed to a category."""
    if "." in name:
        return name if name in known_tables() else None
    matches = [t for t in known_tables() if t.split(".", 1)[1] == name]
    return matches[0] if len(matches) == 1 else None


def authorize(user, sql: str) -> dict[str, Any]:
    """May this person see the result of this SQL? Fail closed on anything we can't attribute.

    Only the person's own access decides. An agent's focus categories rank documents first but don't restrict
    them, and tables follow the same rule, so an agent never withholds a figure the person could read elsewhere.
    """
    from . import iam

    tables = sql_tables(sql)
    if tables is None:
        return {"ok": False, "tables": [], "denied": [], "reason": "the query could not be parsed"}
    allowed = iam.categories(user)
    denied, unknown = [], []
    for t in tables:
        q = _qualify(t)
        if q is None:
            unknown.append(t)
        elif table_category(q) not in allowed:
            denied.append(q)
    if unknown:
        return {"ok": False, "tables": tables, "denied": unknown, "reason": f"unknown table(s): {', '.join(unknown)}"}
    if denied:
        return {"ok": False, "tables": tables, "denied": denied,
                "reason": f"needs data you may not see ({', '.join(sorted({table_category(d) for d in denied}))})"}
    return {"ok": True, "tables": tables, "denied": [], "reason": ""}


# ------------------------------------------------------------------------------------------------------ engines
@dataclass
class Coverage:
    coverage: float
    uncovered: list[str] = field(default_factory=list)


@dataclass
class Result:
    answer: str
    sql: str
    rows: list[dict[str, Any]]
    row_count: int
    steps: list[str] = field(default_factory=list)


class EngineError(RuntimeError):
    pass


class Engine(Protocol):
    name: str

    def available(self) -> bool: ...
    def coverage(self, question: str, user_id: str) -> Coverage: ...
    def ask(self, question: str, user_id: str) -> Result: ...


class AutoOntology:
    """NVIDIA Auto Ontology over its public API (the web app's origin, the same API its MCP server uses).

    Each employee should have their own API token (var/auto-ontology-tokens.json: {"emp-1001": "..."}), so the
    engine's own RBAC applies too. OA_AO_TOKEN is a shared fallback; Omniq's table check applies either way.
    """

    name = "auto-ontology"

    def __init__(self, url: str | None = None, tokens: dict[str, str] | None = None, shared_token: str | None = None,
                 transport: httpx.BaseTransport | None = None, timeout: float = 600.0):
        self.url = (url or os.environ.get("OA_AO_URL", "http://localhost:3000")).rstrip("/")
        self.tokens = tokens if tokens is not None else _load_tokens()
        self.shared = shared_token if shared_token is not None else (os.environ.get("OA_AO_TOKEN", "") or _shared_token())
        self._client = httpx.Client(base_url=self.url, timeout=timeout, transport=transport)
        self._up: tuple[float, bool] = (0.0, False)

    def token_for(self, user_id: str) -> tuple[str, bool]:
        if user_id in self.tokens:
            return self.tokens[user_id], True
        if self.shared:
            return self.shared, False
        raise EngineError(f"no Auto Ontology API token for {user_id}")

    def _headers(self, user_id: str) -> dict[str, str]:
        return {"x-api-key": self.token_for(user_id)[0]}

    def available(self) -> bool:
        ts, up = self._up
        if time.time() - ts < 30:
            return up
        try:
            up = self._client.get("/api/health", timeout=3).status_code < 500
        except httpx.HTTPError:
            up = False
        self._up = (time.time(), up)
        return up

    def coverage(self, question: str, user_id: str) -> Coverage:
        r = self._client.post("/api/question-entity-coverage", json={"question": question},
                              headers=self._headers(user_id), timeout=60)
        if r.status_code >= 400:
            raise EngineError(f"coverage failed: HTTP {r.status_code}")
        data = r.json()
        data = data.get("data", data)
        return Coverage(float(data.get("coverage") or 0.0), list(data.get("uncovered_entities") or []))

    def ask(self, question: str, user_id: str) -> Result:
        steps: list[str] = []
        answer: dict[str, Any] | None = None
        with self._client.stream("POST", "/api/chat/completions", json={"question": question},
                                 headers={**self._headers(user_id), "Accept": "text/event-stream"}) as r:
            if r.status_code >= 400:
                raise EngineError(f"ask failed: HTTP {r.status_code}")
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    ev = json.loads(payload)
                except ValueError:
                    continue
                if ev.get("type") == "step":
                    steps.append(str(ev.get("label") or ev.get("node") or "step"))
                elif ev.get("type") == "result":
                    answer = ev.get("answer") or {}
                elif ev.get("type") == "error":
                    raise EngineError(str(ev.get("message") or "engine error"))
        if answer is None:
            raise EngineError("the engine returned no answer")
        rows = _rows(answer.get("sql_response_from_db") or answer.get("result"))
        return Result(str(answer.get("response") or ""), str(answer.get("sql_code") or ""), rows, len(rows), steps)


def _load_tokens() -> dict[str, str]:
    f = Path(os.environ.get("OA_DATA_DIR", ROOT / "var")) / "auto-ontology-tokens.json"
    try:
        return dict(json.loads(f.read_text()))
    except (OSError, ValueError):
        return {}


def _shared_token() -> str:
    """The development token minted by scripts/auto_ontology_bootstrap.py, if any."""
    f = Path(os.environ.get("OA_DATA_DIR", ROOT / "var")) / "auto-ontology-token"
    try:
        return f.read_text().strip()
    except OSError:
        return ""


def _rows(raw: Any) -> list[dict[str, Any]]:
    """Rows as dicts from any shape the engine uses. Auto Ontology v1.2.0 sends a list of JSON strings, each an
    array of row objects: `["[{\"sum\": 730000.0}]"]`."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if isinstance(raw, dict):
        if isinstance(raw.get("rows"), list) and isinstance(raw.get("columns"), list):
            return [dict(zip(raw["columns"], r)) for r in raw["rows"]]
        raw = raw.get("data") or raw.get("rows") or []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for r in raw:
        if isinstance(r, dict):
            out.append(r)
        elif isinstance(r, (str, list)):  # a nested result set, possibly JSON-encoded
            out += _rows(r)
    return out


_engine: Engine | None = None


def engine() -> Engine | None:
    """The configured engine, or None when structured data is switched off (OA_STRUCTURED=off)."""
    global _engine
    if os.environ.get("OA_STRUCTURED", "auto").lower() == "off":
        return None
    if _engine is None:
        _engine = AutoOntology()
    return _engine


# ------------------------------------------------------------------------------------------------------ the tool
def query(user, question: str, cfg: dict[str, Any], eng: Engine | None = None) -> dict[str, Any]:
    """One structured-data lookup for one question. Always returns a record; never raises into the answer path.

    status: off | unavailable | denied | unanswerable | withheld | error | ok
    """
    from . import iam

    knobs = cfg.get("retrieval") or {}
    eng = eng or engine()
    rec: dict[str, Any] = {"status": "off", "engine": getattr(eng, "name", None)}
    if eng is None:
        return rec
    if not iam.allowed(user, "data:query", "data:warehouse/halcyon"):
        return {**rec, "status": "denied", "reason": "IAM: data:query not allowed"}
    cat = catalog_coverage(user, question)
    rec.update(catalog_coverage=cat["coverage"], uncovered=cat["uncovered"])
    if cat["coverage"] < float(knobs.get("structured_min_catalog", 0.6)):
        return {**rec, "status": "unanswerable"}  # nothing in the data this person may query mentions these terms
    try:
        if not eng.available():
            return {**rec, "status": "unavailable"}
        cov = eng.coverage(question, user.id)
        rec.update(coverage=round(cov.coverage, 3), uncovered=cov.uncovered)
        if cov.coverage < float(knobs.get("structured_min_coverage", 0.5)):
            return {**rec, "status": "unanswerable"}
        res = eng.ask(question, user.id)
    except (EngineError, httpx.HTTPError) as e:
        return {**rec, "status": "error", "reason": str(e)[:200]}
    gate = authorize(user, res.sql) if res.sql else {"ok": False, "tables": [], "denied": [], "reason": "no SQL returned"}
    rec.update(sql=res.sql, tables=gate["tables"], steps=res.steps[:20], row_count=res.row_count)
    if not gate["ok"]:
        return {**rec, "status": "withheld", "reason": gate["reason"], "denied": gate["denied"]}
    cats = sorted({table_category(_qualify(t) or t) for t in gate["tables"]})
    return {**rec, "status": "ok", "answer": res.answer, "rows": res.rows[:MAX_ROWS],
            "categories": cats}


def _cell(v: Any) -> str:
    """Numbers as people write them (730000.0 → 730,000), so the model copies a readable figure."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"


def as_source(rec: dict[str, Any]) -> dict[str, Any] | None:
    """Render a lookup for the prompt, as a source the model must cite. Only `ok` results carry data."""
    if rec["status"] == "ok":
        rows = rec.get("rows") or []
        cols = list(rows[0].keys()) if rows else []
        table = "\n".join(" | ".join(_cell(r.get(c, ""))[:MAX_CELL] for c in cols) for r in rows)
        text = (f"Result from the company databases ({rec['row_count']} row(s); tables: {', '.join(rec['tables'])}).\n"
                f"Engine's answer: {rec.get('answer', '')}\n"
                + (f"Columns: {' | '.join(cols)}\n{table}\n" if rows else "")
                + f"SQL: {rec['sql']}")
        return {"title": "Company data (SQL)", "text": text, "category": (rec.get("categories") or ["general"])[-1],
                "categories": rec.get("categories") or []}
    if rec["status"] == "unanswerable":
        missing = ", ".join(rec.get("uncovered") or []) or "the terms in the question"
        return {"title": "Company data (SQL)", "category": "general", "categories": [],
                "text": f"The company databases do not cover {missing}. Say so; do not estimate these numbers."}
    if rec["status"] == "withheld":
        return {"title": "Company data (SQL)", "category": "general", "categories": [],
                # Same wording whether or not restricted data exists, so a refusal confirms nothing.
                "text": "The company databases can't answer this with the data this employee has access to. Say "
                        "so plainly and give no figures."}
    return None
