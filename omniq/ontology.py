"""Ontology v2: the organization as typed objects, links and governed actions, read with three levels of access.

Sources (all built at seed time into the `objects` and `links` collections):
- the directory (people, teams, reporting lines, who leads which team) from ``data/seed/org.yaml``,
- the records a CRM / ITSM / finance / planning connector would sync, from ``data/seed/ontology.yaml``,
- every document, linked to the objects it mentions by the entity linker (``mentions`` links).
Every record is checked against the typed schema in ``config/ontology.yaml`` (object types and their properties,
link types with labels and inverses, action types).

Access, enforced on every read and the same for the model, the UI and actions:
1. object: ``can_read`` on its category (a single object can override its type's category: a board-only project);
2. property: a property can be stricter than its object (incident service credits are finance data);
3. link: visible only if both ends are. A ``mentions`` link starts at a document, not an object, and carries the
   document's own access fields, so "mentioned in" only ever lists documents the person may read.
A restricted object and a nonexistent one look the same: ``get`` returns None, facts leave it out, and actions and
history answer exactly as they would for an unknown id.

What the model gets (``sources``): the visible properties and links of the entities named in the question, one or
two hops out, as plain-text records that the assistant tokenizes with everything else. Documents that mention those
entities can be ranked higher (``boost``). Each step is a harness switch (``cfg["ontology"]``) so the research
ablations can turn it off one at a time; the switches that would weaken access or privacy are research-only.

Changes go through actions only. Each run is checked (role, department and, for some actions, a relationship to the
object such as being its CSM), applied, appended to the ``ontology_edits`` log and audited; seeding replays the log
over the fresh source records, so edits survive a re-seed.
"""

from __future__ import annotations

import datetime as dt
import re
import threading
import uuid
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import yaml

from .config import ROOT
from .db import db
from .policy import User, can_read

SCHEMA_FILE = ROOT / "config" / "ontology.yaml"
MAX_SEEDS = 6        # entities a question can seed facts from
MAX_BLOCKS = 12      # object records per answer
MAX_LIST = 12        # neighbours listed per link label
EXPAND_PER_LABEL = 4  # neighbours per label followed on the second hop
BOOST_RANKS = 3      # a document that mentions a linked entity is ranked as if it were this many places higher
NO_EXPAND = ("Person", "Team")  # hubs: following them on hop 2 floods the facts with members and reports


class OntologyError(ValueError):
    pass


# ---------------------------------------------------------------- schema

@dataclass(frozen=True)
class PropertyDef:
    name: str
    type: str = "string"
    label: str = ""
    category: str | None = None  # stricter than the object's category
    options: tuple[str, ...] = ()
    required: bool = False

    @property
    def display(self) -> str:
        return self.label or self.name.replace("_", " ").capitalize()


@dataclass(frozen=True)
class ObjectType:
    name: str
    label: str
    plural: str
    category: str
    properties: dict[str, PropertyDef]


@dataclass(frozen=True)
class LinkType:
    name: str
    src: str
    dst: str  # an object type, or "*"
    label: str
    inverse: str


@dataclass(frozen=True)
class ActionType:
    name: str
    label: str
    object_type: str
    roles: frozenset[str]
    departments: frozenset[str]
    requires_link: tuple[str, ...]
    bypass_roles: frozenset[str]
    parameters: dict[str, PropertyDef]


@dataclass(frozen=True)
class Schema:
    object_types: dict[str, ObjectType]
    link_types: dict[str, LinkType]
    action_types: dict[str, ActionType]

    @classmethod
    def load(cls, path: Path | str) -> "Schema":
        raw = yaml.safe_load(Path(path).read_text())

        def props(spec: dict[str, Any] | None) -> dict[str, PropertyDef]:
            return {n: PropertyDef(n, p.get("type", "string"), p.get("label", ""), p.get("category"),
                                   tuple(p.get("options", ())), bool(p.get("required", False)))
                    for n, p in (spec or {}).items()}

        ots = {n: ObjectType(n, s.get("label", n), s.get("plural", n + "s"), s["category"], props(s.get("properties")))
               for n, s in raw["object_types"].items()}
        lts = {}
        for n, s in raw["link_types"].items():
            for end in (s["from"], s["to"]):
                if end not in ("*", "Document") and end not in ots:
                    raise OntologyError(f"link '{n}' references unknown type '{end}'")
            lts[n] = LinkType(n, s["from"], s["to"], s.get("label", n), s.get("inverse", n))
        acts = {}
        for n, s in (raw.get("action_types") or {}).items():
            if s["object_type"] not in ots:
                raise OntologyError(f"action '{n}' targets unknown type '{s['object_type']}'")
            acts[n] = ActionType(n, s.get("label", n), s["object_type"], frozenset(s.get("roles") or ()),
                                 frozenset(s.get("departments") or ()), tuple(s.get("requires_link") or ()),
                                 frozenset(s.get("bypass_roles") or ()), props(s.get("parameters")))
        return cls(ots, lts, acts)


@lru_cache(maxsize=1)
def schema() -> Schema:
    return Schema.load(SCHEMA_FILE)


def settings(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """The harness's ontology switches, with the strict defaults for anything a stored version doesn't have."""
    from .harness import DEFAULT_HARNESS

    return {**DEFAULT_HARNESS["ontology"], **((cfg or {}).get("ontology") or {})}


# ---------------------------------------------------------------- building (seed time)

def build_graph(users: list[dict[str, Any]], teams: dict[str, str], records: dict[str, list[dict[str, Any]]],
                sch: Schema | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Objects and links from the directory and the connector records, checked against the schema."""
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
    links += _leads(users)
    for otype, items in (records or {}).items():
        for item in items or []:
            item = dict(item)
            oid = item.pop("id")
            for ltype, dsts in (item.pop("links", {}) or {}).items():
                for dst in dsts if isinstance(dsts, list) else [dsts]:
                    links.append({"src": oid, "type": ltype, "dst": dst})
            if otype == "Team":  # teams come from the directory; records only add their links
                continue
            ot = (sch or schema()).object_types.get(otype)
            objects.append({
                "id": oid, "type": otype, "name": item.pop("name", oid),
                "category": item.pop("category", ot.category if ot else "general"),
                "aliases": [a.lower() for a in item.pop("aliases", [])],
                "props": {k: str(v) for k, v in item.items()},
            })
    return objects, validate(objects, links, sch)


def _leads(users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Who leads each team: a manager who manages their own team, else its most senior member who doesn't report to
    someone in the same team."""
    out, led = [], set()
    for u in users:
        if u.get("role") in ("manager", "admin") and u.get("team") in (u.get("manages") or []):
            out.append({"src": u["id"], "type": "leads", "dst": u["team"]})
            led.add(u["team"])
    by_team: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for u in users:
        if u.get("team"):
            by_team[u["team"]].append(u)
    for team, members in by_team.items():
        if team in led:
            continue
        ids = {m["id"] for m in members}
        lead = next((m for m in members if m.get("role") in ("manager", "admin", "executive")
                     and m.get("reports_to") not in ids), None)
        if lead:
            out.append({"src": lead["id"], "type": "leads", "dst": team})
    return out


def validate(objects: list[dict[str, Any]], links: list[dict[str, Any]],
             sch: Schema | None = None) -> list[dict[str, Any]]:
    """Raise on anything the schema doesn't allow; return the links whose ends both exist (a connector may sync a
    link before, or after, the record at its other end)."""
    sch = sch or schema()
    by_id: dict[str, dict[str, Any]] = {}
    for o in objects:
        ot = sch.object_types.get(o["type"])
        if ot is None:
            raise OntologyError(f"unknown object type '{o['type']}' ({o['id']})")
        if o["id"] in by_id:
            raise OntologyError(f"duplicate object id '{o['id']}'")
        unknown = set(o.get("props") or {}) - set(ot.properties)
        if unknown:
            raise OntologyError(f"{o['type']} {o['id']} has unknown properties {sorted(unknown)}")
        by_id[o["id"]] = o
    kept = []
    for l in links:
        lt = sch.link_types.get(l["type"])
        if lt is None:
            raise OntologyError(f"unknown link type '{l['type']}'")
        src, dst = by_id.get(l["src"]), by_id.get(l["dst"])
        if src is None or dst is None:
            continue
        if src["type"] != lt.src or (lt.dst != "*" and dst["type"] != lt.dst):
            raise OntologyError(f"link '{l['type']}' cannot join {src['type']} {l['src']} -> {dst['type']} {l['dst']}")
        kept.append(l)
    return kept


def mention_links(docs: Iterable[dict[str, Any]], objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One ``mentions`` link per (document, object it names). The link carries the document's access fields."""
    linker = Linker(objects)
    out = []
    for d in docs:
        for oid in linker.link(f"{d['title']}\n{d.get('text', '')}"):
            out.append({"src": f"doc:{d['doc_id']}", "type": "mentions", "dst": oid, "doc_id": d["doc_id"],
                        "category": d.get("category", "general"), "team": d.get("team", ""),
                        "owner": d.get("owner", "")})
    return out


# ---------------------------------------------------------------- entity linking

class Linker:
    """Finds objects named in text: names and aliases (case-insensitive), and people's first names and surnames
    (capitalized, only when one person has that name part). A team's name alone ("Platform", "Sales") is too
    ambiguous; "Platform team" or a multi-word name ("Data & ML") links."""

    def __init__(self, objects: Iterable[dict[str, Any]]):
        exact: dict[str, str] = {}
        parts: dict[str, set[str]] = defaultdict(set)
        from .pii import COMMON_WORDS

        for o in objects:
            names = list(o.get("aliases") or [])
            if o["type"] == "Team":
                names.append(f"{o['name']} team")
                if " " in o["name"] or "&" in o["name"]:
                    names.append(o["name"])
            else:
                names.append(o["name"])
            for n in names:
                if len(n.strip()) >= 3:
                    exact.setdefault(n.strip().lower(), o["id"])
            if o["type"] == "Person":
                for p in o["name"].split():
                    if len(p) > 2 and p.lower() not in COMMON_WORDS:
                        parts[p].add(o["id"])
        self._exact = exact
        self._parts = {p: next(iter(ids)) for p, ids in parts.items() if len(ids) == 1}
        alt = "|".join(re.escape(n) for n in sorted(exact, key=len, reverse=True))
        self._re = re.compile(rf"(?i)(?<![\w-])(?:{alt})(?![\w-])") if exact else None
        alt_cs = "|".join(re.escape(p) for p in sorted(self._parts, key=len, reverse=True))
        self._re_cs = re.compile(rf"(?<![\w-])(?:{alt_cs})(?![\w-])") if self._parts else None

    def link(self, text: str) -> list[str]:
        """Object ids named in the text, in order of first mention. No access check: callers filter."""
        found: dict[str, int] = {}
        spans: list[tuple[int, int]] = []
        if self._re:
            for m in self._re.finditer(text):
                oid = self._exact.get(m.group(0).lower())
                if oid:
                    found.setdefault(oid, m.start())
                    spans.append(m.span())
        if self._re_cs:
            for m in self._re_cs.finditer(text):
                if any(s <= m.start() < e for s, e in spans):
                    continue
                found.setdefault(self._parts[m.group(0)], m.start())
        return sorted(found, key=found.get)


@dataclass
class Graph:
    objects: dict[str, dict[str, Any]]
    out: dict[str, list[dict[str, Any]]]
    inn: dict[str, list[dict[str, Any]]]
    linker: Linker


_cache: dict[str, Any] = {"key": None, "graph": None}
_generation = 0
_lock = threading.Lock()


def invalidate() -> None:
    """Forget the in-memory graph (after a seed or an action changed objects or links)."""
    global _generation
    _generation += 1


def graph() -> Graph:
    """The whole graph in memory (a few hundred objects), rebuilt when the store, its size or the generation
    changes. Every read below goes through the access checks; this is only an index."""
    d = db()
    key = (id(d), _generation, d.objects.count_documents({}), d.links.count_documents({}))
    with _lock:
        if _cache["key"] != key:
            objs = {o["id"]: o for o in d.objects.find({}, {"_id": 0})}
            out: dict[str, list[dict[str, Any]]] = defaultdict(list)
            inn: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for l in d.links.find({}, {"_id": 0}):
                out[l["src"]].append(l)
                inn[l["dst"]].append(l)
            _cache.update(key=key, graph=Graph(objs, out, inn, Linker(objs.values())))
        return _cache["graph"]


# ---------------------------------------------------------------- access-controlled reads

def visible(user: User, obj: dict[str, Any] | None) -> bool:
    return obj is not None and can_read(user, obj)


def get(user: User, oid: str) -> dict[str, Any] | None:
    """The object, or None if it doesn't exist *or* the person may not see it: the two are indistinguishable."""
    obj = graph().objects.get(oid)
    return obj if visible(user, obj) else None


def properties(user: User, obj: dict[str, Any], *, property_acl: bool = True) -> dict[str, str]:
    """The object's properties this person may see. Unknown properties are hidden (fail closed)."""
    ot = schema().object_types.get(obj["type"])
    out = {}
    for name, value in (obj.get("props") or {}).items():
        pd = ot.properties.get(name) if ot else None
        if property_acl:
            if pd is None:
                continue
            if pd.category and not can_read(user, {**_acl(obj), "category": pd.category}):
                continue
        out[name] = value
    return out


def _acl(obj: dict[str, Any]) -> dict[str, Any]:
    return {"category": obj.get("category", "general"), "team": obj.get("team", ""), "owner": obj.get("owner", "")}


def link_visible(user: User, link: dict[str, Any], *, link_acl: bool = True) -> bool:
    """Both ends visible. A mention's document end is checked on the link itself (it carries the document's ACL)."""
    if not link_acl:
        return True
    g = graph()
    src_ok = can_read(user, link) if link["type"] == "mentions" else visible(user, g.objects.get(link["src"]))
    return src_ok and visible(user, g.objects.get(link["dst"]))


def neighbors(user: User, oid: str, *, link_acl: bool = True) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """(label, other end, link) for every visible link touching the object, mentions excluded. With ``link_acl``
    off (research ablation O1) the other end is listed even if the person can't see it."""
    g = graph()
    sch = schema()
    rows = []
    for l in g.out.get(oid, ()):
        other = g.objects.get(l["dst"])
        if other is not None and link_visible(user, l, link_acl=link_acl):
            rows.append((sch.link_types[l["type"]].label, other, l))
    for l in g.inn.get(oid, ()):
        if l["type"] == "mentions":
            continue
        other = g.objects.get(l["src"])
        if other is not None and link_visible(user, l, link_acl=link_acl):
            rows.append((sch.link_types[l["type"]].inverse, other, l))
    return sorted(rows, key=lambda r: (r[0], r[1]["name"]))


def linked(user: User, text: str, limit: int = MAX_SEEDS) -> list[dict[str, Any]]:
    """Objects named in the text that this person may see, in order of first mention."""
    g = graph()
    return [o for o in (get(user, oid) for oid in g.linker.link(text)) if o][:limit]


def mentioning_documents(user: User, oids: Iterable[str]) -> set[str]:
    """Ids of documents this person may read that mention any of the objects."""
    g = graph()
    return {l["doc_id"] for oid in oids for l in g.inn.get(oid, ())
            if l["type"] == "mentions" and can_read(user, l)}


def seeds_from_documents(user: User, doc_ids: list[str], limit: int = MAX_SEEDS) -> list[dict[str, Any]]:
    """Entity linking switched off: seed from what the retrieved documents mention instead (ablation "seed by
    retrieval only"). Objects named by more of them come first; records before people and teams, which documents
    mention in passing."""
    g = graph()
    score: dict[str, list[int]] = {}
    for rank, did in enumerate(dict.fromkeys(doc_ids)):
        for l in g.out.get(f"doc:{did}", ()):
            if l["type"] == "mentions" and can_read(user, l):
                s = score.setdefault(l["dst"], [0, rank])
                s[0] += 1
    ranked = sorted(score, key=lambda oid: (g.objects.get(oid, {}).get("type") in NO_EXPAND, -score[oid][0],
                                            score[oid][1]))
    return [o for o in (get(user, oid) for oid in ranked) if o][:limit]


def _fmt(pd: PropertyDef | None, v: Any) -> str:
    if pd is not None and pd.type == "money":
        try:
            return f"${float(v):,.0f}"
        except (TypeError, ValueError):
            pass
    return str(v)


def facts(user: User, seeds: list[dict[str, Any]], *, hops: int = 1, property_acl: bool = True,
          link_acl: bool = True) -> list[tuple[dict[str, Any], str]]:
    """Plain-text records for the model, one per object: its visible properties and links. ``hops`` = 2 also
    describes the neighbours of each seed (people and teams are listed, not expanded). An object is only ever
    described if the person may see it; the ACL switches only change what its record lists."""
    sch = schema()
    seen: set[str] = set()
    blocks: list[tuple[dict[str, Any], str]] = []
    queue = [(o, 0) for o in seeds if visible(user, o)]
    while queue and len(blocks) < MAX_BLOCKS:
        obj, depth = queue.pop(0)
        if obj["id"] in seen:
            continue
        seen.add(obj["id"])
        ot = sch.object_types[obj["type"]]
        props = properties(user, obj, property_acl=property_acl)
        fields = "; ".join(f"{ot.properties[n].display if n in ot.properties else n}: {_fmt(ot.properties.get(n), v)}"
                           for n, v in props.items() if n != "name" and v not in (None, ""))
        lines = [f"{ot.label}: {obj['name']}" + (f" — {fields}" if fields else "")]
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for label, other, _ in neighbors(user, obj["id"], link_acl=link_acl):
            grouped[label].append(other)
        for label, others in sorted(grouped.items()):
            names = ", ".join(f"{o['name']} ({sch.object_types[o['type']].label})" for o in others[:MAX_LIST])
            more = f" and {len(others) - MAX_LIST} more" if len(others) > MAX_LIST else ""
            lines.append(f"  {label}: {names}{more}")
            if depth + 1 < hops:
                queue.extend((o, depth + 1) for o in others[:EXPAND_PER_LABEL]
                             if o["type"] not in NO_EXPAND and visible(user, o))
        blocks.append((obj, "\n".join(lines)))
    return blocks


# ---------------------------------------------------------------- assistant hooks

def sources(user: User, question: str, cfg: dict[str, Any], hits: list[dict[str, Any]] | None = None
            ) -> list[dict[str, Any]]:
    """Ontology facts as assistant sources (one per object record). They are tokenized with the rest of the payload
    unless the research-only ``tokenize_facts`` switch is off (see ``payload``)."""
    from . import usage

    o = settings(cfg)
    if o["entity_linking"]:
        seeds = linked(user, question)
    else:
        seeds = seeds_from_documents(user, [h.get("doc_id", "") for h in hits or [] if h.get("doc_id")])
    if not seeds:
        return []
    blocks = facts(user, seeds, hops=int(o["hops"]), property_acl=bool(o["property_acl"]),
                   link_acl=bool(o["link_acl"]))
    usage.bump("graph_lookups", len(blocks))
    sch = schema()
    return [{"title": f"Ontology: {sch.object_types[obj['type']].label} {obj['name']}", "text": text, "ontology": True}
            for obj, text in blocks]


def payload(t_user: str, srcs: list[dict[str, Any]], vault, cfg: dict[str, Any]) -> str:
    """The tokenized user message, unless the research-only ``tokenize_facts`` switch is off: then the facts are put
    back raw (the "utility cost of privacy" ablation). The outbound guard still re-scans the payload afterwards, so
    the run also needs ``invariants.outbound_guard`` off in its cfg_override, which no patch can do."""
    if settings(cfg)["tokenize_facts"]:
        return t_user
    for s in srcs:
        if s.get("ontology"):
            t_user = t_user.replace(vault.tokenize(s["text"]), s["text"])
    return t_user


def boost(user: User, query: str, hits: list[dict[str, Any]], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Rank documents that mention an entity named in the query higher: each moves up ``BOOST_RANKS`` places.
    Rank-based, because dense, hybrid and reranked scores are on different scales. Needs entity linking."""
    o = settings(cfg)
    if not (o["mention_boost"] and o["entity_linking"] and hits):
        return hits
    docs = mentioning_documents(user, [x["id"] for x in linked(user, query)])
    if not docs:
        return hits
    order = sorted(range(len(hits)), key=lambda i: i - (BOOST_RANKS if hits[i].get("doc_id") in docs else 0))
    return [hits[i] for i in order]


def pii_registry():
    """The privacy registries from the directory and the ontology: people and CRM contacts, customers, projects."""
    from .pii import Names

    d = db()
    people = [u["name"] for u in d.users.find({}, {"name": 1})]
    people += [o["name"] for o in d.objects.find({"type": "Contact"}, {"name": 1})]
    customers = [o["name"] for o in d.objects.find({"type": "Customer"}, {"name": 1})]
    projects = [o["name"] for o in d.objects.find({"type": "Project"}, {"name": 1})]
    return Names(people, customers=customers, projects=projects)


# ---------------------------------------------------------------- governed actions

def _may_run(user: User, action: ActionType, obj: dict[str, Any] | None) -> bool:
    if obj is None or obj["type"] != action.object_type or not visible(user, obj):
        return False
    if user.role not in action.roles or (action.departments and user.department not in action.departments):
        return False
    if action.requires_link and user.role not in action.bypass_roles:
        return any(l["type"] in action.requires_link and l["dst"] == user.id for l in graph().out.get(obj["id"], ()))
    return True


def actions_for(user: User, oid: str) -> list[dict[str, Any]]:
    """The actions this person may run on the object (empty for a restricted or unknown object)."""
    obj = get(user, oid)
    return [{"name": a.name, "label": a.label,
             "parameters": [{"name": p.name, "label": p.display, "type": p.type, "options": list(p.options),
                             "required": p.required} for p in a.parameters.values()]}
            for a in schema().action_types.values() if _may_run(user, a, obj)]


def run_action(user: User, name: str, oid: str, params: dict[str, Any]) -> dict[str, Any]:
    """Check, apply, log and audit one action. Refused identically for a restricted object and an unknown one."""
    from . import audit

    action = schema().action_types.get(name)
    obj = graph().objects.get(oid)
    if action is None or not _may_run(user, action, obj):
        raise PermissionError(f"You can't run '{name}' on this object.")
    clean = _validate(user, action, params)
    with _lock:
        result = _apply(name, oid, clean, {})
        seq = db().ontology_edits.count_documents({}) + 1
        db().ontology_edits.insert_one({"seq": seq, "ts": dt.datetime.now(dt.timezone.utc), "user_id": user.id,
                                        "action": name, "object": oid, "params": clean, "result": result})
    invalidate()
    audit.record("ontology_action", user.id, action=name, object=oid, fields=sorted(clean))
    return result


def _validate(user: User, action: ActionType, params: dict[str, Any]) -> dict[str, str]:
    unknown = set(params) - set(action.parameters)
    if unknown:
        raise OntologyError(f"unknown parameter(s): {', '.join(sorted(unknown))}")
    clean: dict[str, str] = {}
    for p in action.parameters.values():
        v = str(params.get(p.name) or "").strip()
        if p.required and not v:
            raise OntologyError(f"'{p.display}' is required")
        if not v:
            continue
        if len(v) > 2000:
            raise OntologyError(f"'{p.display}' is too long")
        if p.options and v not in p.options:
            raise OntologyError(f"'{p.display}' must be one of: {', '.join(p.options)}")
        if p.type == "person":
            person = _resolve_person(user, v)
            if person is None:
                raise OntologyError(f"no employee called '{v}'")
            v = person["id"]
        clean[p.name] = v
    return clean


def _resolve_person(user: User, ref: str) -> dict[str, Any] | None:
    o = get(user, ref)
    if o is not None and o["type"] == "Person":
        return o
    matches = [p for p in graph().objects.values()
               if p["type"] == "Person" and p["name"].lower() == ref.lower() and visible(user, p)]
    return matches[0] if len(matches) == 1 else None


def _apply(name: str, oid: str, params: dict[str, str], replay: dict[str, Any]) -> dict[str, Any]:
    """The effect of one action on the stored records. ``replay`` is the logged result when re-applying."""
    d = db()
    obj = d.objects.find_one({"id": oid}, {"_id": 0})
    if obj is None:
        raise OntologyError(f"no object '{oid}'")
    props = dict(obj.get("props") or {})
    if name == "update_customer_health":
        before = {"health": props.get("health"), "health_note": props.get("health_note")}
        props.update(health=params["health"], health_note=params["note"])
        d.objects.update_one({"id": oid}, {"$set": {"props": props}})
        return {"changed": {"health": [before["health"], params["health"]],
                            "health_note": [before["health_note"], params["note"]]}}
    if name == "update_deal_stage":
        before = props.get("stage")
        props["stage"] = params["stage"]
        if params.get("next_step"):
            props["next_step"] = params["next_step"]
        d.objects.update_one({"id": oid}, {"$set": {"props": props}})
        return {"changed": {"stage": [before, params["stage"]]}}
    if name == "add_incident_action_item":
        new_id = replay.get("created_id") or f"ai-{uuid.uuid4().hex[:8]}"
        new = {"name": params["name"], "status": "Open", **({"due": params["due"]} if params.get("due") else {})}
        d.objects.insert_one({"id": new_id, "type": "ActionItem", "name": new.pop("name"), "aliases": [],
                              "category": obj.get("category", "engineering"), "props": new})
        d.links.insert_many([{"src": new_id, "type": "action_for", "dst": oid},
                             {"src": new_id, "type": "assigned_to", "dst": params["assignee"]}])
        return {"created_id": new_id}
    raise OntologyError(f"no handler for action '{name}'")


def replay() -> int:
    """Re-apply the edit log over freshly seeded records, in order. Edits whose object is gone are skipped."""
    n = 0
    for e in db().ontology_edits.find({}, {"_id": 0}).sort("seq", 1):
        try:
            _apply(e["action"], e["object"], e["params"], e.get("result") or {})
            n += 1
        except (OntologyError, KeyError):
            continue
    invalidate()
    return n


def history(user: User, oid: str) -> list[dict[str, Any]]:
    """Edits to an object, newest first; empty for a restricted or unknown object."""
    if get(user, oid) is None:
        return []
    return [{"ts": e["ts"], "user_id": e["user_id"], "action": e["action"], "result": e["result"]}
            for e in db().ontology_edits.find({"object": oid}, {"_id": 0}).sort("seq", -1)]
