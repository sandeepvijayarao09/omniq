"""A small, local, embedded document store: the persistence layer for Omniq.

Replaces the hosted database with something any researcher can run with no
account or service: collections of JSON documents, persisted write-through to
one SQLite file, with the query, update and aggregation features this codebase
uses (a subset of the familiar document-database API, so call sites read
naturally). Retrieval-specific stages run locally:

- ``$vectorSearch``  exact (brute-force) cosine search with a pre-filter, so
  access control is applied *before* ranking, never after;
- ``$graphLookup``   breadth-first traversal over a links collection.

Exact search is deliberate for research use: results are deterministic and
there is no approximate-index recall to account for. Collections here are
small (thousands of chunks), so it is also fast enough.

Concurrency: one process, a re-entrant lock per database. Every write is
committed to SQLite immediately; reads are served from memory.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import math
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Iterator

ASCENDING = 1
DESCENDING = -1


class DuplicateKeyError(Exception):
    pass


class OperationFailure(Exception):
    pass


class ReturnDocument:
    BEFORE = False
    AFTER = True


# ---- JSON with dates ------------------------------------------------------------------


def _encode(obj: Any) -> Any:
    if isinstance(obj, dt.datetime):
        if obj.tzinfo is None:
            obj = obj.replace(tzinfo=dt.timezone.utc)
        return {"$date": obj.isoformat()}
    if isinstance(obj, dt.date):
        return {"$day": obj.isoformat()}
    if isinstance(obj, dict):
        return {k: _encode(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_encode(v) for v in obj]
    return obj


def _decode(obj: Any) -> Any:
    if isinstance(obj, dict):
        if len(obj) == 1 and "$date" in obj:
            return dt.datetime.fromisoformat(obj["$date"])
        if len(obj) == 1 and "$day" in obj:
            return dt.date.fromisoformat(obj["$day"])
        return {k: _decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decode(v) for v in obj]
    return obj


def _dumps(doc: dict[str, Any]) -> str:
    return json.dumps(_encode(doc), separators=(",", ":"), default=str)


# ---- paths -----------------------------------------------------------------------------

_MISSING = object()


def get_path(doc: Any, path: str) -> Any:
    """Value at a dotted path; arrays along the way fan out (returns a list then)."""
    cur: Any = doc
    parts = path.split(".")
    for i, part in enumerate(parts):
        if isinstance(cur, dict):
            if part not in cur:
                return _MISSING
            cur = cur[part]
        elif isinstance(cur, list):
            if part.isdigit():
                idx = int(part)
                if idx >= len(cur):
                    return _MISSING
                cur = cur[idx]
            else:
                rest = ".".join(parts[i:])
                vals = [get_path(x, rest) for x in cur if isinstance(x, dict)]
                return [v for v in vals if v is not _MISSING]
        else:
            return _MISSING
    return cur


def _set_path(doc: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur: Any = doc
    for part in parts[:-1]:
        if isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)]
            continue
        if part not in cur or not isinstance(cur[part], (dict, list)):
            cur[part] = {}
        cur = cur[part]
    last = parts[-1]
    if isinstance(cur, list) and last.isdigit():
        idx = int(last)
        while len(cur) <= idx:
            cur.append(None)
        cur[idx] = value
    else:
        cur[last] = value


def _unset_path(doc: dict[str, Any], path: str) -> None:
    parts = path.split(".")
    cur: Any = doc
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return
        cur = cur[part]
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)


# ---- comparisons -----------------------------------------------------------------------

_TYPE_ORDER = {type(None): 0, int: 1, float: 1, bool: 1, str: 2, dict: 3, list: 4, dt.date: 5, dt.datetime: 5}


def _cmp_key(v: Any) -> tuple[int, Any]:
    if v is _MISSING or v is None:
        return (0, 0)
    if isinstance(v, bool):
        return (1, int(v))
    if isinstance(v, (int, float)):
        return (1, v)
    if isinstance(v, str):
        return (2, v)
    if isinstance(v, dt.datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=dt.timezone.utc)
        return (5, v.timestamp())
    if isinstance(v, dt.date):
        return (5, dt.datetime(v.year, v.month, v.day, tzinfo=dt.timezone.utc).timestamp())
    return (3, json.dumps(_encode(v), sort_keys=True, default=str))


def _compare(a: Any, b: Any) -> int:
    ka, kb = _cmp_key(a), _cmp_key(b)
    return (ka > kb) - (ka < kb)


def _eq(a: Any, b: Any) -> bool:
    if a is _MISSING:
        return b is None
    if isinstance(a, (dt.date, dt.datetime)) or isinstance(b, (dt.date, dt.datetime)):
        return _compare(a, b) == 0
    return a == b


# ---- query matching -----------------------------------------------------------------------


def _candidates(v: Any) -> list[Any]:
    """A field value plus, for arrays, each element (array fields match any element)."""
    if isinstance(v, list):
        return [v, *v]
    return [v]


def _match_op(value: Any, op: str, arg: Any, doc: dict[str, Any] | None = None) -> bool:
    if op == "$eq":
        return any(_eq(c, arg) for c in _candidates(value))
    if op == "$ne":
        return not any(_eq(c, arg) for c in _candidates(value))
    if op in ("$gt", "$gte", "$lt", "$lte"):
        if value is _MISSING or value is None:
            return False
        for c in _candidates(value):
            if c is None or isinstance(c, list):
                continue
            r = _compare(c, arg)
            if (op == "$gt" and r > 0) or (op == "$gte" and r >= 0) or (op == "$lt" and r < 0) or (op == "$lte" and r <= 0):
                return True
        return False
    if op == "$in":
        return any(_eq(c, a) for c in _candidates(value) for a in arg)
    if op == "$nin":
        return not any(_eq(c, a) for c in _candidates(value) for a in arg)
    if op == "$exists":
        return (value is not _MISSING) == bool(arg)
    if op == "$regex":
        if not isinstance(value, str):
            return any(isinstance(c, str) and re.search(arg, c) for c in _candidates(value))
        return re.search(arg, value) is not None
    if op == "$size":
        return isinstance(value, list) and len(value) == arg
    if op == "$all":
        return isinstance(value, list) and all(any(_eq(x, a) for x in value) for a in arg)
    if op == "$elemMatch":
        return isinstance(value, list) and any(
            (matches(x, arg) if isinstance(x, dict) else _match_value(x, arg)) for x in value
        )
    if op == "$not":
        return not _match_value(value, arg)
    raise OperationFailure(f"unsupported query operator {op}")


def _match_value(value: Any, cond: Any) -> bool:
    if isinstance(cond, dict) and cond and all(k.startswith("$") for k in cond):
        opts = cond.get("$options", "")
        for op, arg in cond.items():
            if op == "$options":
                continue
            if op == "$regex" and opts:
                flags = (re.I if "i" in opts else 0) | (re.M if "m" in opts else 0) | (re.S if "s" in opts else 0)
                arg = re.compile(arg, flags)
            if not _match_op(value, op, arg):
                return False
        return True
    if isinstance(cond, re.Pattern):
        return any(isinstance(c, str) and cond.search(c) for c in _candidates(value))
    return _match_op(value, "$eq", cond)


def matches(doc: dict[str, Any], query: dict[str, Any] | None) -> bool:
    if not query:
        return True
    for key, cond in query.items():
        if key == "$and":
            if not all(matches(doc, q) for q in cond):
                return False
        elif key == "$or":
            if not any(matches(doc, q) for q in cond):
                return False
        elif key == "$nor":
            if any(matches(doc, q) for q in cond):
                return False
        elif key == "$expr":
            if not _truthy(evaluate(cond, doc)):
                return False
        else:
            if not _match_value(get_path(doc, key), cond):
                return False
    return True


# ---- aggregation expressions ------------------------------------------------------------


def _truthy(v: Any) -> bool:
    if v is _MISSING or v is None or v is False:
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v != 0
    return True


def evaluate(expr: Any, doc: dict[str, Any], vars_: dict[str, Any] | None = None) -> Any:
    """Aggregation expressions: "$field", literals, and the operators this codebase uses."""
    if isinstance(expr, str) and expr.startswith("$$"):
        name, _, rest = expr[2:].partition(".")
        base = (vars_ or {}).get(name, _MISSING)
        return get_path(base, rest) if rest else base
    if isinstance(expr, str) and expr.startswith("$"):
        v = get_path(doc, expr[1:])
        return None if v is _MISSING else v
    if isinstance(expr, list):
        return [evaluate(e, doc, vars_) for e in expr]
    if not isinstance(expr, dict):
        return expr
    if len(expr) == 1:
        (op, arg), = expr.items()
        if op.startswith("$"):
            return _apply_op(op, arg, doc, vars_)
    return {k: evaluate(v, doc, vars_) for k, v in expr.items()}


def _apply_op(op: str, arg: Any, doc: dict[str, Any], vars_: dict[str, Any] | None) -> Any:
    ev = lambda e: evaluate(e, doc, vars_)  # noqa: E731
    args = arg if isinstance(arg, list) else [arg]
    if op == "$literal":
        return arg
    if op == "$meta":
        return doc.get("__meta__", {}).get(arg)
    if op in ("$eq", "$ne", "$gt", "$gte", "$lt", "$lte"):
        a, b = ev(args[0]), ev(args[1])
        r = _compare(a, b)
        return {"$eq": r == 0, "$ne": r != 0, "$gt": r > 0, "$gte": r >= 0, "$lt": r < 0, "$lte": r <= 0}[op]
    if op == "$and":
        return all(_truthy(ev(a)) for a in args)
    if op == "$or":
        return any(_truthy(ev(a)) for a in args)
    if op == "$not":
        return not _truthy(ev(args[0]))
    if op == "$cond":
        if isinstance(arg, dict):
            c, t, f = arg["if"], arg["then"], arg["else"]
        else:
            c, t, f = arg
        return ev(t) if _truthy(ev(c)) else ev(f)
    if op == "$ifNull":
        for a in args:
            v = ev(a)
            if v is not None:
                return v
        return None
    if op == "$in":
        return ev(args[0]) in (ev(args[1]) or [])
    if op == "$add":
        vals = [ev(a) for a in args]
        return sum(v for v in vals if isinstance(v, (int, float)))
    if op == "$subtract":
        a, b = ev(args[0]), ev(args[1])
        if isinstance(a, dt.datetime) and isinstance(b, dt.datetime):
            return (a - b).total_seconds() * 1000
        return (a or 0) - (b or 0)
    if op == "$multiply":
        out = 1.0
        for a in args:
            out *= ev(a) or 0
        return out
    if op == "$divide":
        a, b = ev(args[0]), ev(args[1])
        return (a or 0) / b if b else None
    if op == "$size":
        v = ev(args[0])
        return len(v) if isinstance(v, list) else 0
    if op == "$concat":
        vals = [ev(a) for a in args]
        return None if any(v is None for v in vals) else "".join(str(v) for v in vals)
    if op == "$toString":
        v = ev(args[0])
        return None if v is None else str(v)
    if op == "$round":
        v = ev(args[0])
        places = ev(args[1]) if len(args) > 1 else 0
        return None if v is None else round(v, places)
    if op == "$first":
        v = ev(args[0])
        return v[0] if isinstance(v, list) and v else None
    if op == "$filter":
        items = ev(arg["input"]) or []
        name = arg.get("as", "this")
        return [x for x in items if _truthy(evaluate(arg["cond"], doc, {**(vars_ or {}), name: x}))]
    if op == "$map":
        items = ev(arg["input"]) or []
        name = arg.get("as", "this")
        return [evaluate(arg["in"], doc, {**(vars_ or {}), name: x}) for x in items]
    if op == "$dateToString":
        v = ev(arg["date"])
        if not isinstance(v, (dt.date, dt.datetime)):
            return None
        fmt = arg.get("format", "%Y-%m-%dT%H:%M:%S.%LZ").replace("%L", "000")
        return v.strftime(fmt)
    raise OperationFailure(f"unsupported expression operator {op}")


# ---- updates -------------------------------------------------------------------------------


def _apply_update(doc: dict[str, Any], update: dict[str, Any], *, inserting: bool) -> None:
    if not any(k.startswith("$") for k in update):  # a replacement document
        keep = doc.get("_id")
        doc.clear()
        doc.update(copy.deepcopy(update))
        if keep is not None:
            doc["_id"] = keep
        return
    for op, fields in update.items():
        if op == "$setOnInsert":
            if inserting:
                for k, v in fields.items():
                    _set_path(doc, k, copy.deepcopy(v))
        elif op == "$set":
            for k, v in fields.items():
                _set_path(doc, k, copy.deepcopy(v))
        elif op == "$unset":
            for k in fields:
                _unset_path(doc, k)
        elif op == "$inc":
            for k, v in fields.items():
                cur = get_path(doc, k)
                _set_path(doc, k, (0 if cur in (_MISSING, None) else cur) + v)
        elif op in ("$min", "$max"):
            for k, v in fields.items():
                cur = get_path(doc, k)
                if cur in (_MISSING, None) or (op == "$min" and _compare(v, cur) < 0) or (op == "$max" and _compare(v, cur) > 0):
                    _set_path(doc, k, v)
        elif op in ("$push", "$addToSet"):
            for k, v in fields.items():
                cur = get_path(doc, k)
                arr = list(cur) if isinstance(cur, list) else []
                if isinstance(v, dict) and "$each" in v:
                    items = list(v["$each"])
                    sl = v.get("$slice")
                else:
                    items, sl = [v], None
                for it in items:
                    if op == "$addToSet" and any(_eq(x, it) for x in arr):
                        continue
                    arr.append(copy.deepcopy(it))
                if sl is not None:
                    arr = arr[sl:] if sl < 0 else arr[:sl]
                _set_path(doc, k, arr)
        elif op == "$pull":
            for k, cond in fields.items():
                cur = get_path(doc, k)
                if isinstance(cur, list):
                    keep = [x for x in cur if not (matches(x, cond) if isinstance(x, dict) and isinstance(cond, dict)
                                                   else _match_value(x, cond))]
                    _set_path(doc, k, keep)
        elif op == "$currentDate":
            for k in fields:
                _set_path(doc, k, dt.datetime.now(dt.timezone.utc))
        else:
            raise OperationFailure(f"unsupported update operator {op}")


def _upsert_seed(query: dict[str, Any]) -> dict[str, Any]:
    """The equality parts of a filter become the new document's fields on upsert."""
    seed: dict[str, Any] = {}
    for k, v in (query or {}).items():
        if k.startswith("$"):
            if k == "$and":
                for q in v:
                    seed.update(_upsert_seed(q))
            continue
        if isinstance(v, dict) and any(x.startswith("$") for x in v):
            if "$eq" in v:
                _set_path(seed, k, v["$eq"])
            continue
        _set_path(seed, k, copy.deepcopy(v))
    return seed


# ---- projection & sorting -----------------------------------------------------------------


def project(doc: dict[str, Any], projection: dict[str, Any] | list[str] | None) -> dict[str, Any]:
    if not projection:
        return doc
    if isinstance(projection, list):
        projection = {k: 1 for k in projection}
    include_id = projection.get("_id", 1)
    fields = {k: v for k, v in projection.items() if k != "_id"}
    if not fields:
        out = dict(doc)
        if not include_id:
            out.pop("_id", None)
        return out
    slices = {k: v["$slice"] for k, v in fields.items() if isinstance(v, dict) and set(v) == {"$slice"}}
    fields = {k: (1 if k in slices else v) for k, v in fields.items()}
    computed = {k: v for k, v in fields.items() if not isinstance(v, (int, bool))}
    flags = {k: bool(v) for k, v in fields.items() if isinstance(v, (int, bool))}
    if flags and all(not v for v in flags.values()):  # exclusion (computed fields may be added alongside)
        out = copy.deepcopy(doc)
        out.pop("__meta__", None)
        for k in flags:
            _unset_path(out, k)
        for k, expr in computed.items():
            _set_path(out, k, evaluate(expr, doc))
        if not include_id:
            out.pop("_id", None)
        return out
    out: dict[str, Any] = {}
    if include_id and "_id" in doc:
        out["_id"] = doc["_id"]
    for k, inc in flags.items():
        if inc:
            v = get_path(doc, k)
            if v is not _MISSING:
                _set_path(out, k, copy.deepcopy(v))
    for k, expr in computed.items():
        _set_path(out, k, evaluate(expr, doc))
    for k, sl in slices.items():  # {"field": {"$slice": n}} or {"$slice": [skip, n]}
        v = get_path(out, k)
        if isinstance(v, list):
            if isinstance(sl, list):
                v = v[sl[0]: sl[0] + sl[1]]
            else:
                v = v[sl:] if sl < 0 else v[:sl]
            _set_path(out, k, v)
    return out


def _sort_spec(key: Any, direction: int | None = None) -> list[tuple[str, int]]:
    if isinstance(key, str):
        return [(key, direction or ASCENDING)]
    if isinstance(key, dict):
        return list(key.items())
    return [(k, d) for k, d in key]


def sort_docs(docs: list[dict[str, Any]], spec: list[tuple[str, int]]) -> list[dict[str, Any]]:
    for field, direction in reversed(spec):
        def key(d: dict[str, Any], field: str = field) -> tuple[int, Any]:
            if field == "score" and "__meta__" in d and get_path(d, "score") is _MISSING:
                return _cmp_key(d["__meta__"].get("vectorSearchScore"))
            return _cmp_key(get_path(d, field))
        docs = sorted(docs, key=key, reverse=direction == DESCENDING)
    return docs


# ---- results & cursor ----------------------------------------------------------------------


class InsertOneResult:
    def __init__(self, inserted_id: Any):
        self.inserted_id = inserted_id
        self.acknowledged = True


class InsertManyResult:
    def __init__(self, ids: list[Any]):
        self.inserted_ids = ids
        self.acknowledged = True


class UpdateResult:
    def __init__(self, matched: int, modified: int, upserted_id: Any = None):
        self.matched_count = matched
        self.modified_count = modified
        self.upserted_id = upserted_id
        self.acknowledged = True


class DeleteResult:
    def __init__(self, n: int):
        self.deleted_count = n
        self.acknowledged = True


class Cursor:
    def __init__(self, coll: "Collection", query: dict[str, Any] | None, projection: Any):
        self._coll, self._query, self._projection = coll, query, projection
        self._sort: list[tuple[str, int]] = []
        self._skip = 0
        self._limit = 0
        self._iter: Iterator[dict[str, Any]] | None = None

    def sort(self, key: Any, direction: int | None = None) -> "Cursor":
        self._sort = _sort_spec(key, direction)
        return self

    def skip(self, n: int) -> "Cursor":
        self._skip = int(n)
        return self

    def limit(self, n: int) -> "Cursor":
        self._limit = int(n)
        return self

    def _run(self) -> list[dict[str, Any]]:
        docs = self._coll._select(self._query)
        if self._sort:
            docs = sort_docs(docs, self._sort)
        docs = docs[self._skip:]
        if self._limit:
            docs = docs[: self._limit]
        return [project(copy.deepcopy(d), self._projection) for d in docs]

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self._run())

    def __next__(self) -> dict[str, Any]:
        if self._iter is None:
            self._iter = iter(self._run())
        return next(self._iter)

    def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        out = self._run()
        return out[:length] if length else out


# ---- collections ------------------------------------------------------------------------------


class Collection:
    def __init__(self, database: "Database", name: str):
        self.database = database
        self.name = name
        self._docs: dict[str, dict[str, Any]] = {}
        self._unique: list[tuple[str, ...]] = [("_id",)]
        self._ttl: dict[str, int] = {}
        self._indexes: dict[str, dict[str, Any]] = {"_id_": {"name": "_id_", "key": {"_id": 1}}}
        self._loaded = False

    # -- storage
    def _load(self) -> None:
        if self._loaded:
            return
        for (doc_id, raw) in self.database._sql.execute("SELECT id, doc FROM docs WHERE coll = ?", (self.name,)):
            self._docs[doc_id] = _decode(json.loads(raw))
        for (raw,) in self.database._sql.execute("SELECT spec FROM indexes WHERE coll = ?", (self.name,)):
            self._register_index(json.loads(raw))
        self._loaded = True

    def _register_index(self, ix: dict[str, Any]) -> None:
        fields = tuple(ix["key"])
        if ix.get("unique") and fields not in self._unique:
            self._unique.append(fields)
        if ix.get("expireAfterSeconds") is not None:
            self._ttl[fields[0]] = int(ix["expireAfterSeconds"])
        self._indexes[ix["name"]] = ix

    def _key(self, _id: Any) -> str:
        return json.dumps(_encode(_id), sort_keys=True, default=str)

    def _persist(self, doc: dict[str, Any]) -> None:
        self.database._sql.execute(
            "INSERT OR REPLACE INTO docs (coll, id, doc) VALUES (?, ?, ?)",
            (self.name, self._key(doc["_id"]), _dumps(doc)),
        )
        self.database._changed(self.name, doc)

    def _remove(self, doc: dict[str, Any]) -> None:
        self.database._sql.execute("DELETE FROM docs WHERE coll = ? AND id = ?", (self.name, self._key(doc["_id"])))

    def _expire(self) -> None:
        if not self._ttl:
            return
        now = dt.datetime.now(dt.timezone.utc)
        for field, seconds in self._ttl.items():
            for k, d in list(self._docs.items()):
                v = d.get(field)
                if isinstance(v, dt.datetime) and (v if v.tzinfo else v.replace(tzinfo=dt.timezone.utc)) + dt.timedelta(seconds=seconds) <= now:
                    self._remove(d)
                    del self._docs[k]

    def _select(self, query: dict[str, Any] | None) -> list[dict[str, Any]]:
        with self.database._lock:
            self._load()
            self._expire()
            if query and set(query) == {"_id"} and not isinstance(query["_id"], dict):
                d = self._docs.get(self._key(query["_id"]))
                return [d] if d is not None else []
            return [d for d in self._docs.values() if matches(d, query)]

    def _check_unique(self, doc: dict[str, Any], exclude: str | None = None) -> None:
        for fields in self._unique:
            vals = tuple(json.dumps(_encode(get_path(doc, f)), default=str) if get_path(doc, f) is not _MISSING else None
                         for f in fields)
            if all(v is None for v in vals):
                continue
            for k, other in self._docs.items():
                if k == exclude:
                    continue
                ovals = tuple(json.dumps(_encode(get_path(other, f)), default=str) if get_path(other, f) is not _MISSING
                              else None for f in fields)
                if ovals == vals:
                    raise DuplicateKeyError(f"E11000 duplicate key in {self.name}: {dict(zip(fields, vals))}")

    # -- writes
    def insert_one(self, document: dict[str, Any]) -> InsertOneResult:
        with self.database._lock:
            self._load()
            self._expire()
            if "_id" not in document:
                document["_id"] = uuid.uuid4().hex[:24]
            doc = copy.deepcopy(document)
            self._check_unique(doc)
            self._docs[self._key(doc["_id"])] = doc
            self._persist(doc)
            return InsertOneResult(doc["_id"])

    def insert_many(self, documents: Iterable[dict[str, Any]], ordered: bool = True) -> InsertManyResult:
        ids = []
        with self.database._lock, self.database._batch():
            for d in documents:
                ids.append(self.insert_one(d).inserted_id)
        return InsertManyResult(ids)

    def _update(self, query: dict[str, Any], update: dict[str, Any] | list[dict[str, Any]], *, many: bool,
                upsert: bool) -> tuple[UpdateResult, dict[str, Any] | None, dict[str, Any] | None]:
        if isinstance(update, list):
            raise OperationFailure("pipeline-style updates are not supported")
        with self.database._lock:
            targets = self._select(query)
            if not many:
                targets = targets[:1]
            if not targets:
                if not upsert:
                    return UpdateResult(0, 0), None, None
                doc = _upsert_seed(query)
                _apply_update(doc, update, inserting=True)
                if "_id" not in doc:
                    doc["_id"] = uuid.uuid4().hex[:24]
                self._check_unique(doc)
                self._docs[self._key(doc["_id"])] = doc
                self._persist(doc)
                return UpdateResult(0, 0, doc["_id"]), None, doc
            modified = 0
            before = after = None
            for doc in targets:
                before = copy.deepcopy(doc)
                new = copy.deepcopy(doc)
                _apply_update(new, update, inserting=False)
                if new != doc:
                    self._check_unique(new, exclude=self._key(doc["_id"]))
                    doc.clear()
                    doc.update(new)
                    self._persist(doc)
                    modified += 1
                after = doc
            return UpdateResult(len(targets), modified), before, after

    def update_one(self, filter: dict[str, Any], update: dict[str, Any], upsert: bool = False, **_: Any) -> UpdateResult:
        return self._update(filter, update, many=False, upsert=upsert)[0]

    def update_many(self, filter: dict[str, Any], update: dict[str, Any], upsert: bool = False, **_: Any) -> UpdateResult:
        return self._update(filter, update, many=True, upsert=upsert)[0]

    def replace_one(self, filter: dict[str, Any], replacement: dict[str, Any], upsert: bool = False) -> UpdateResult:
        rep = {k: v for k, v in replacement.items()}
        return self._update(filter, rep, many=False, upsert=upsert)[0]

    def find_one_and_update(self, filter: dict[str, Any], update: dict[str, Any], *, upsert: bool = False,
                            return_document: bool = ReturnDocument.BEFORE, projection: Any = None,
                            sort: Any = None, **_: Any) -> dict[str, Any] | None:
        with self.database._lock:
            if sort:
                first = sort_docs(self._select(filter), _sort_spec(sort))[:1]
                if first:
                    filter = {"_id": first[0]["_id"]}
            _, before, after = self._update(filter, update, many=False, upsert=upsert)
            out = after if return_document else before
            return project(copy.deepcopy(out), projection) if out is not None else None

    def delete_one(self, filter: dict[str, Any]) -> DeleteResult:
        with self.database._lock:
            hits = self._select(filter)[:1]
            for d in hits:
                self._remove(d)
                del self._docs[self._key(d["_id"])]
            return DeleteResult(len(hits))

    def delete_many(self, filter: dict[str, Any]) -> DeleteResult:
        with self.database._lock, self.database._batch():
            hits = self._select(filter)
            for d in hits:
                self._remove(d)
                del self._docs[self._key(d["_id"])]
            return DeleteResult(len(hits))

    def drop(self) -> None:
        with self.database._lock:
            self.database._sql.execute("DELETE FROM docs WHERE coll = ?", (self.name,))
            self.database._sql.execute("DELETE FROM indexes WHERE coll = ?", (self.name,))
            self._unique, self._ttl = [("_id",)], {}
            self._indexes = {"_id_": {"name": "_id_", "key": {"_id": 1}}}
            self._docs.clear()
            self._loaded = True

    # -- reads
    def find(self, filter: dict[str, Any] | None = None, projection: Any = None, **kw: Any) -> Cursor:
        cur = Cursor(self, filter, projection)
        if kw.get("sort"):
            cur.sort(kw["sort"])
        if kw.get("skip"):
            cur.skip(kw["skip"])
        if kw.get("limit"):
            cur.limit(kw["limit"])
        return cur

    def find_one(self, filter: dict[str, Any] | Any = None, projection: Any = None, **kw: Any) -> dict[str, Any] | None:
        if filter is not None and not isinstance(filter, dict):
            filter = {"_id": filter}
        for d in self.find(filter, projection, **kw).limit(1):
            return d
        return None

    def count_documents(self, filter: dict[str, Any] | None = None, **_: Any) -> int:
        return len(self._select(filter or {}))

    def estimated_document_count(self) -> int:
        return self.count_documents({})

    def distinct(self, key: str, filter: dict[str, Any] | None = None) -> list[Any]:
        out: list[Any] = []
        for d in self._select(filter or {}):
            v = get_path(d, key)
            for x in (v if isinstance(v, list) else [v]):
                if x is not _MISSING and not any(_eq(x, y) for y in out):
                    out.append(x)
        return out

    def aggregate(self, pipeline: list[dict[str, Any]], **_: Any) -> Cursor | "_ListCursor":
        from .docstore_agg import run_pipeline

        with self.database._lock:
            return _ListCursor(run_pipeline(self, pipeline))

    # -- indexes (unique and TTL are enforced; others are no-ops for a small in-memory store)
    def create_index(self, keys: Any, unique: bool = False, expireAfterSeconds: int | None = None, **_: Any) -> str:
        spec = _sort_spec(keys) if not isinstance(keys, str) else [(keys, ASCENDING)]
        name = "_".join(f"{f}_{d}" for f, d in spec)
        ix = {"name": name, "key": dict(spec), **({"unique": True} if unique else {}),
              **({"expireAfterSeconds": int(expireAfterSeconds)} if expireAfterSeconds is not None else {})}
        with self.database._lock:
            self._load()
            if unique:  # refuse to create a unique index the existing data already violates
                fields = tuple(ix["key"])
                seen: set[str] = set()
                for d in self._docs.values():
                    vals = tuple(json.dumps(_encode(get_path(d, f)), default=str) for f in fields)
                    if all(get_path(d, f) is _MISSING for f in fields):
                        continue
                    if json.dumps(vals) in seen:
                        raise DuplicateKeyError(f"cannot create unique index {name}: duplicates in {self.name}")
                    seen.add(json.dumps(vals))
            self._register_index(ix)
            self.database._sql.execute("INSERT OR REPLACE INTO indexes (coll, name, spec) VALUES (?, ?, ?)",
                                       (self.name, name, json.dumps(ix)))
        return name

    def create_search_index(self, *_: Any, **__: Any) -> str:
        return "local"  # exact vector search needs no index

    def list_search_indexes(self, *_: Any) -> list[dict[str, Any]]:
        return [{"name": "local", "queryable": True}]

    def list_indexes(self) -> list[dict[str, Any]]:
        with self.database._lock:
            self._load()
            return list(self._indexes.values())

    def watch(self, pipeline: list[dict[str, Any]] | None = None, **_: Any) -> "ChangeStream":
        return ChangeStream(self.database, self.name, pipeline or [])


class _ListCursor:
    def __init__(self, rows: list[dict[str, Any]]):
        self._rows = rows
        self._it = iter(rows)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self._rows)

    def __next__(self) -> dict[str, Any]:
        return next(self._it)

    def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self._rows[:length] if length else list(self._rows)


class ChangeStream:
    """Polling change stream: yields {operationType, fullDocument, documentKey} after it opens."""

    def __init__(self, database: "Database", coll: str, pipeline: list[dict[str, Any]]):
        self._db, self._coll = database, coll
        self._match = next((s["$match"] for s in pipeline if "$match" in s), None)
        self._seen = database._change_seq
        self._closed = False

    def __enter__(self) -> "ChangeStream":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._closed = True

    @property
    def alive(self) -> bool:
        return not self._closed

    def try_next(self) -> dict[str, Any] | None:
        for seq, coll, doc in self._db._changes_after(self._seen):
            self._seen = seq
            if coll != self._coll:
                continue
            ev = {"operationType": "update", "fullDocument": copy.deepcopy(doc), "documentKey": {"_id": doc["_id"]}}
            if self._match is None or matches(ev, self._match):
                return ev
        return None

    def __iter__(self) -> Iterator[dict[str, Any]]:
        while not self._closed:
            ev = self.try_next()
            if ev is not None:
                yield ev
            else:
                time.sleep(0.25)

    def next(self) -> dict[str, Any]:
        return next(iter(self))


# ---- database ----------------------------------------------------------------------------------


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.name = self.path.stem
        self._sql = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._sql.execute("PRAGMA journal_mode=WAL")
        self._sql.execute("CREATE TABLE IF NOT EXISTS docs (coll TEXT NOT NULL, id TEXT NOT NULL, doc TEXT NOT NULL,"
                          " PRIMARY KEY (coll, id))")
        self._sql.execute("CREATE TABLE IF NOT EXISTS indexes (coll TEXT NOT NULL, name TEXT NOT NULL, spec TEXT NOT NULL,"
                          " PRIMARY KEY (coll, name))")
        self._collections: dict[str, Collection] = {}
        self._depth = 0
        self._change_seq = 0
        self._changes: list[tuple[int, str, dict[str, Any]]] = []

    def _changed(self, coll: str, doc: dict[str, Any]) -> None:
        self._change_seq += 1
        self._changes.append((self._change_seq, coll, copy.deepcopy(doc)))
        if len(self._changes) > 2000:
            del self._changes[:1000]

    def _changes_after(self, seq: int) -> list[tuple[int, str, dict[str, Any]]]:
        with self._lock:
            return [c for c in self._changes if c[0] > seq]

    class _Batch:
        def __init__(self, db: "Database"):
            self.db = db

        def __enter__(self) -> None:
            if self.db._depth == 0:
                self.db._sql.execute("BEGIN")
            self.db._depth += 1

        def __exit__(self, exc_type: Any, *_: Any) -> None:
            self.db._depth -= 1
            if self.db._depth == 0:
                self.db._sql.execute("ROLLBACK" if exc_type else "COMMIT")

    def _batch(self) -> "_Batch":
        return Database._Batch(self)

    def __getitem__(self, name: str) -> Collection:
        with self._lock:
            if name not in self._collections:
                self._collections[name] = Collection(self, name)
            return self._collections[name]

    def __getattr__(self, name: str) -> Collection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def get_collection(self, name: str) -> Collection:
        return self[name]

    def list_collection_names(self) -> list[str]:
        rows = {r[0] for r in self._sql.execute("SELECT DISTINCT coll FROM docs")}
        rows |= {r[0] for r in self._sql.execute("SELECT DISTINCT coll FROM indexes")}
        return sorted(rows | {n for n, c in self._collections.items() if c._docs})

    def create_collection(self, name: str, **_: Any) -> Collection:
        return self[name]

    def drop_collection(self, name: str) -> None:
        self[name].drop()

    def command(self, cmd: str | dict[str, Any], *_: Any, **__: Any) -> dict[str, Any]:
        return {"ok": 1}


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)

