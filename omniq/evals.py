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
                     r"not in (the )?(provided |available )?sources?|"  # the phrase agent templates ask for
                     r"don'?t (have|see) (any )?(information|access|details)|"
                     r"(do|does|did) ?n[o']?t (contain|include|mention|cover|provide|specify)", re.I)


AGENT_EVALS = settings.evals_file.parent / "agent_evals.yaml"


@lru_cache(maxsize=1)
def cases() -> list[dict[str, Any]]:
    return yaml.safe_load(settings.evals_file.read_text())


@lru_cache(maxsize=1)
def agent_cases() -> list[dict[str, Any]]:
    return yaml.safe_load(AGENT_EVALS.read_text()) if AGENT_EVALS.exists() else []


def roles_with_cases() -> list[str]:
    roles = []
    for c in cases():
        r = assistant.get_user(c["persona"]).role
        if r not in roles:
            roles.append(r)
    return roles


def select(role: str, split: str | None = None) -> list[dict[str, Any]]:
    """Cases for a scope: a role ("pro") or an agent ("agent:deal_coach")."""
    if role.startswith("agent:"):
        return [c for c in agent_cases() if c["agent"] == role[6:] and (not split or c.get("split") == split)]
    out = []
    for c in cases():
        if split and c.get("split") != split:
            continue
        if role == "all" or assistant.get_user(c["persona"]).role == role:
            out.append(c)
    return out


_EMPHASIS = re.compile(r"\*\*|__|`|(?<![\w*])\*(?=\S)|(?<=\S)\*(?![\w*])")


def plain(text: str) -> str:
    """Markdown emphasis removed: "**3** of **40**" says "3 of 40"."""
    return _EMPHASIS.sub("", text)


def grade(case: dict[str, Any], answer: str) -> dict[str, Any]:
    low, flat = answer.lower(), plain(answer).lower()
    # A fact counts if it's there with or without formatting; a leak counts if it's there either way.
    leaked = [s for s in case.get("must_not_include", []) if s.lower() in low or s.lower() in flat]
    missing = [s for s in case.get("must_include", []) if s.lower() not in low and s.lower() not in flat]
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


_SENT = re.compile(r"(?<=[.!?])\s+|\n+|\s+\|\s+")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def fact_reached_model(fact: str, question: str, context: str, user) -> bool:
    """Did the passage that states this fact *as an answer to this question* reach the model?

    Matching the fact string anywhere in the context misfiles failures: a name the question asks about ("who is up
    for promotion?") also appears in the staff directory, so a missed HR note looked like a reasoning miss and the
    improver tried prompt rules instead of retrieval. The evidence is the section (chunk), among those the person
    may read, that contains the fact and shares the most key terms with the question; its question-relevant lines
    must have been in context. Sections, not sentences: a note names the person in a heading and gives the
    recommendation underneath. With no section related to the question, any occurrence counts (old behaviour)."""
    from . import answerability
    from .db import db
    from .policy import can_read

    f = fact.lower()
    stem = answerability._stem
    terms = {stem(w) for w in answerability.salient_terms(question)} - {stem(w) for w in answerability.salient_terms(fact)}
    best, gold = 0, []
    for c in db().chunks.find({"text": {"$regex": re.escape(fact), "$options": "i"}}, {"embedding": 0}):
        if not can_read(user, c):
            continue
        score = len(terms & {stem(w) for w in answerability.salient_terms(c["text"])})
        if score > best:
            best, gold = score, [c["text"]]
        elif score == best:
            gold.append(c["text"])
    if best == 0:  # nothing relates the fact to the question: fall back to "anywhere in context"
        return f in context.lower()
    ctx = _norm(context)
    for text in gold:
        relevant = [ln for ln in _SENT.split(text) if (f in ln.lower() or terms & {stem(w) for w in answerability.salient_terms(ln)})
                    and len(ln.strip()) > 3]
        if relevant and all(_norm(ln) in ctx for ln in relevant):
            return True
    return False


def run_case(case: dict[str, Any], cfg: dict[str, Any], version: int) -> dict[str, Any]:
    res = assistant.ask(case["persona"], case["question"], agent_id=case.get("agent"), cfg_override=cfg,
                        harness_version=version, source="eval", personal_memory=False)
    g = grade(case, res["answer"])
    if not g["passed"] and case["kind"] == "answer":
        ctx = res.get("_context", "")
        user = assistant.get_user(case["persona"])
        absent = [f for f in case.get("must_include", [])
                  if f.lower() not in res["answer"].lower() and not fact_reached_model(f, case["question"], ctx, user)]
        g["attribution"] = "retrieval_miss" if absent else "reasoning_miss"
        g["why"] += f" [{g['attribution']}: {'not in retrieved context: ' + str(absent) if absent else 'fact was in context but not used'}]"
    elif not g["passed"]:
        g["attribution"] = "policy"
    return {"id": case["id"], "kind": case["kind"], "persona": case["persona"], "question": case["question"],
            "expected": case.get("must_include", []), "answer": res["answer"][:700], "sources": res["sources"],
            "tokens": res["tokens"], "latency": res["latency"], **g}


def _merge(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Average repeated runs of one case; it passes only if it passed every time (no lucky promotions)."""
    first = dict(runs[0])
    first["score"] = sum(r["score"] for r in runs) / len(runs)
    first["passed"] = all(r["passed"] for r in runs)
    first["leaked"] = any(r["leaked"] for r in runs)
    first["tokens"] = sum(r["tokens"] for r in runs) / len(runs)
    first["latency"] = sum(r["latency"] for r in runs) / len(runs)
    if not first["passed"]:
        first["why"] = next(r["why"] for r in runs if not r["passed"])
    return first


def evaluate(cfg: dict[str, Any], role: str, split: str | None, version: int, workers: int = 6,
             repeats: int | None = None) -> dict[str, Any]:
    cs = select(role, split)
    if not cs:
        return {"score": 0.0, "pass_rate": 0.0, "n": 0, "results": []}
    repeats = repeats or (2 if len(cs) < 8 else 1)  # small sets are noisy: score every case twice
    jobs = [c for c in cs for _ in range(repeats)]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        flat = list(ex.map(lambda c: run_case(c, cfg, version), jobs))
    results = [_merge(flat[i * repeats:(i + 1) * repeats]) for i in range(len(cs))]
    n = len(results)
    leaks = sum(r["leaked"] for r in results)
    tokens = sum(r["tokens"] for r in results) / n
    raw = sum(r["score"] for r in results) / n
    score = raw - 0.5 * (leaks / n) - min(tokens / 200000, 0.05)  # leaks hurt; cost is a tie-breaker
    return {"score": round(score, 4), "pass_rate": round(sum(r["passed"] for r in results) / n, 4),
            "accuracy": round(raw, 4), "leaks": leaks, "avg_tokens": round(tokens), "n": n,
            "avg_latency": round(sum(r["latency"] for r in results) / n, 2), "results": results}
