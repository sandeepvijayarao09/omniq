"""Aggregation pipelines for the local document store (see docstore.py).

Stages: $vectorSearch (exact cosine, pre-filtered), $graphLookup (breadth-first),
$match, $project, $addFields/$set, $unset, $sort, $limit, $skip, $group, $unwind,
$count, $lookup, $sortByCount, $replaceRoot, $facet.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from .docstore import (
    _MISSING, OperationFailure, _cmp_key, _eq, _set_path, _sort_spec, _unset_path, cosine, evaluate, get_path,
    matches, project, sort_docs,
)

if TYPE_CHECKING:
    from .docstore import Collection


def run_pipeline(coll: "Collection", pipeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    docs: list[dict[str, Any]] | None = None
    for i, stage in enumerate(pipeline):
        (name, spec), = stage.items()
        if name == "$vectorSearch":
            if i != 0:
                raise OperationFailure("$vectorSearch must be the first stage")
            docs = _vector_search(coll, spec)
            continue
        if docs is None:
            if name == "$match":  # push the first $match down to the collection scan
                docs = [copy.deepcopy(d) for d in coll._select(spec)]
                continue
            docs = [copy.deepcopy(d) for d in coll._select({})]
        docs = _STAGES[name](coll, docs, spec) if name in _STAGES else _unsupported(name)
    if docs is None:
        docs = [copy.deepcopy(d) for d in coll._select({})]
    for d in docs:
        d.pop("__meta__", None)
    return docs


def _unsupported(name: str) -> list[dict[str, Any]]:
    raise OperationFailure(f"unsupported aggregation stage {name}")


def _vector_search(coll: "Collection", spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Exact nearest neighbours. The filter is applied *before* ranking (a true pre-filter)."""
    path, q = spec["path"], spec["queryVector"]
    pool = coll._select(spec.get("filter") or {})
    scored = []
    for d in pool:
        v = get_path(d, path)
        if isinstance(v, list) and v and len(v) == len(q):
            # Same scale as the hosted cosine score: (1 + cos) / 2, in [0, 1].
            scored.append(((1.0 + cosine(q, v)) / 2.0, d))
    scored.sort(key=lambda x: -x[0])
    out = []
    for s, d in scored[: int(spec.get("limit", 10))]:
        c = copy.deepcopy(d)
        c["__meta__"] = {"vectorSearchScore": s, "score": s}
        out.append(c)
    return out


def _match(coll: "Collection", docs: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [d for d in docs if matches(d, spec)]


def _project(coll: "Collection", docs: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for d in docs:
        p = project(d, spec)
        if "__meta__" in d:
            p["__meta__"] = d["__meta__"]
        out.append(p)
    return out


def _add_fields(coll: "Collection", docs: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    for d in docs:
        for k, expr in spec.items():
            _set_path(d, k, evaluate(expr, d))
    return docs


def _unset(coll: "Collection", docs: list[dict[str, Any]], spec: Any) -> list[dict[str, Any]]:
    for d in docs:
        for k in ([spec] if isinstance(spec, str) else spec):
            _unset_path(d, k)
    return docs


def _sort(coll: "Collection", docs: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    resolved = []
    for field, direction in _sort_spec(spec):
        resolved.append((field, -1 if (isinstance(direction, dict) or direction < 0) else 1))
    return sort_docs(docs, resolved)


def _limit(coll: "Collection", docs: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    return docs[: int(n)]


def _skip(coll: "Collection", docs: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    return docs[int(n):]


def _accumulate(op: str, values: list[Any]) -> Any:
    vals = [v for v in values if v is not None and v is not _MISSING]
    if op == "$sum":
        return sum(v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool))
    if op == "$avg":
        nums = [v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        return sum(nums) / len(nums) if nums else None
    if op == "$min":
        return min(vals, key=_cmp_key) if vals else None
    if op == "$max":
        return max(vals, key=_cmp_key) if vals else None
    if op == "$first":
        return values[0] if values else None
    if op == "$last":
        return values[-1] if values else None
    if op == "$push":
        return list(values)
    if op == "$addToSet":
        out: list[Any] = []
        for v in values:
            if not any(_eq(v, o) for o in out):
                out.append(v)
        return out
    if op == "$count":
        return len(values)
    raise OperationFailure(f"unsupported accumulator {op}")


def _group(coll: "Collection", docs: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[str, tuple[Any, list[dict[str, Any]]]] = {}
    order: list[str] = []
    for d in docs:
        key = evaluate(spec["_id"], d)
        k = repr(_cmp_key(key)) if not isinstance(key, dict) else repr(sorted((a, _cmp_key(b)) for a, b in key.items()))
        if k not in groups:
            groups[k] = (key, [])
            order.append(k)
        groups[k][1].append(d)
    out = []
    for k in order:
        key, members = groups[k]
        row: dict[str, Any] = {"_id": key}
        for field, acc in spec.items():
            if field == "_id":
                continue
            (op, expr), = acc.items()
            if op == "$count":
                row[field] = len(members)
            else:
                row[field] = _accumulate(op, [evaluate(expr, m) for m in members])
        out.append(row)
    return out


def _unwind(coll: "Collection", docs: list[dict[str, Any]], spec: Any) -> list[dict[str, Any]]:
    path = spec if isinstance(spec, str) else spec["path"]
    keep_empty = isinstance(spec, dict) and spec.get("preserveNullAndEmptyArrays", False)
    field = path.lstrip("$")
    out = []
    for d in docs:
        v = get_path(d, field)
        if isinstance(v, list) and v:
            for item in v:
                c = copy.deepcopy(d)
                _set_path(c, field, item)
                out.append(c)
        elif isinstance(v, list) or v is _MISSING or v is None:
            if keep_empty:
                out.append(d)
        else:
            out.append(d)
    return out


def _count(coll: "Collection", docs: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [{name: len(docs)}] if docs else []


def _lookup(coll: "Collection", docs: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    other = coll.database[spec["from"]]
    for d in docs:
        local = get_path(d, spec["localField"])
        locals_ = local if isinstance(local, list) else [local]
        d[spec["as"]] = [copy.deepcopy(o) for o in other._select({}) if any(
            _eq(get_path(o, spec["foreignField"]), lv) for lv in locals_)]
    return docs


def _sort_by_count(coll: "Collection", docs: list[dict[str, Any]], expr: Any) -> list[dict[str, Any]]:
    rows = _group(coll, docs, {"_id": expr, "count": {"$sum": 1}})
    return sorted(rows, key=lambda r: -r["count"])


def _replace_root(coll: "Collection", docs: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [evaluate(spec["newRoot"], d) for d in docs]


def _facet(coll: "Collection", docs: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    out: dict[str, Any] = {}
    for name, sub in spec.items():
        rows = copy.deepcopy(docs)
        for stage in sub:
            (sname, sspec), = stage.items()
            rows = _STAGES[sname](coll, rows, sspec)
        out[name] = rows
    return [out]


def _graph_lookup(coll: "Collection", docs: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Breadth-first traversal: start values -> docs whose connectToField matches -> their connectFromField."""
    other = coll.database[spec["from"]]
    pool = other._select(spec.get("restrictSearchWithMatch") or {})
    to_f, from_f = spec["connectToField"], spec["connectFromField"]
    max_depth = int(spec.get("maxDepth", 1_000_000))
    depth_field = spec.get("depthField")
    index: dict[str, list[dict[str, Any]]] = {}
    for o in pool:
        v = get_path(o, to_f)
        for x in (v if isinstance(v, list) else [v]):
            index.setdefault(repr(_cmp_key(x)), []).append(o)
    for d in docs:
        start = evaluate(spec["startWith"], d)
        frontier = start if isinstance(start, list) else [start]
        seen: set[str] = set()
        found: list[dict[str, Any]] = []
        depth = 0
        while frontier and depth <= max_depth:
            nxt = []
            for val in frontier:
                for o in index.get(repr(_cmp_key(val)), []):
                    key = repr(_cmp_key(o["_id"]))
                    if key in seen:
                        continue
                    seen.add(key)
                    c = copy.deepcopy(o)
                    if depth_field:
                        c[depth_field] = depth
                    found.append(c)
                    nv = get_path(o, from_f)
                    nxt.extend(nv if isinstance(nv, list) else [nv])
            frontier = nxt
            depth += 1
        d[spec["as"]] = found
    return docs


_STAGES = {
    "$match": _match, "$project": _project, "$addFields": _add_fields, "$set": _add_fields, "$unset": _unset,
    "$sort": _sort, "$limit": _limit, "$skip": _skip, "$group": _group, "$unwind": _unwind, "$count": _count,
    "$lookup": _lookup, "$sortByCount": _sort_by_count, "$replaceRoot": _replace_root, "$facet": _facet,
    "$graphLookup": _graph_lookup,
}
