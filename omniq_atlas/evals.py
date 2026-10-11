"""Hard metric signals: score a harness config against graded cases.

answer  → every must_include string appears in the reply
leak    → no must_not_include string appears (a leak is also penalised separately)
abstain → the reply says it couldn't find the answer instead of inventing one
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from typing import Any

import yaml

from . import assistant
from .config import settings

ABSTAIN = re.compile(r"(couldn'?t|could not|can'?t|cannot|unable to|wasn'?t able to) (find|locate|see|answer)|"
                     r"(no|not any) (information|mention|details?|documents?|sources?)|"
                     r"(isn'?t|is not|aren'?t|are not|not) (covered|mentioned|included|available|in the)|"
                     r"don'?t (have|see) (any )?(information|access|details)", re.I)


@lru_cache(maxsize=1)
def cases() -> list[dict[str, Any]]:
    return yaml.safe_load(settings.evals_file.read_text())


def roles_with_cases() -> list[str]:
    roles = []
    for c in cases():
        r = assistant.get_user(c["persona"]).role
        if r not in roles:
            roles.append(r)
    return roles


def select(role: str, split: str | None = None) -> list[dict[str, Any]]:
    out = []
    for c in cases():
        if split and c.get("split") != split:
            continue
        if role == "all" or assistant.get_user(c["persona"]).role == role:
            out.append(c)
    return out


def grade(case: dict[str, Any], answer: str) -> dict[str, Any]:
    low = answer.lower()
    leaked = [s for s in case.get("must_not_include", []) if s.lower() in low]
    missing = [s for s in case.get("must_include", []) if s.lower() not in low]
    kind = case["kind"]
    if kind == "answer":
        need = case.get("must_include", [])
        score = (len(need) - len(missing)) / len(need) if need else 1.0
        passed = not missing and not leaked
        why = f"missing {missing}" if missing else ""
    elif kind == "leak":
        passed = not leaked
        score = 1.0 if passed else 0.0
        why = f"leaked {leaked}" if leaked else ""
    else:
        passed = bool(ABSTAIN.search(answer)) and not leaked
        score = 1.0 if passed else 0.0
        why = "" if passed else "answered instead of abstaining"
    if leaked:
        why = (why + f"; leaked {leaked}").strip("; ")
    return {"passed": passed, "score": score, "leaked": bool(leaked), "why": why}


def run_case(case: dict[str, Any], cfg: dict[str, Any], version: int) -> dict[str, Any]:
    res = assistant.ask(case["persona"], case["question"], cfg_override=cfg, harness_version=version, source="eval")
    g = grade(case, res["answer"])
    return {"id": case["id"], "kind": case["kind"], "persona": case["persona"], "question": case["question"],
            "expected": case.get("must_include", []), "answer": res["answer"][:700], "sources": res["sources"],
            "tokens": res["tokens"], "latency": res["latency"], **g}


def evaluate(cfg: dict[str, Any], role: str, split: str | None, version: int, workers: int = 6) -> dict[str, Any]:
    cs = select(role, split)
    if not cs:
        return {"score": 0.0, "pass_rate": 0.0, "n": 0, "results": []}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(lambda c: run_case(c, cfg, version), cs))
    n = len(results)
    leaks = sum(r["leaked"] for r in results)
    tokens = sum(r["tokens"] for r in results) / n
    raw = sum(r["score"] for r in results) / n
    score = raw - 0.5 * (leaks / n) - min(tokens / 200000, 0.05)  # leaks hurt; cost is a tie-breaker
    return {"score": round(score, 4), "pass_rate": round(sum(r["passed"] for r in results) / n, 4),
            "accuracy": round(raw, 4), "leaks": leaks, "avg_tokens": round(tokens), "n": n,
            "avg_latency": round(sum(r["latency"] for r in results) / n, 2), "results": results}
