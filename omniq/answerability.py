"""Answerability: how much of a question the person's own sources actually cover, before the model answers.

Auto Ontology grades database coverage before writing SQL; this does the same for documents and the ontology.
The question's salient terms (content words, identifiers, numbers, and the ontology entities it names) are matched
against the sources this person retrieved and the ontology objects they may read. Below the harness threshold
(`retrieval.answerability_min`, 0 = off) the model is told which terms are uncovered and must say it doesn't know or
ask a clarifying question instead of guessing.

Only permitted sources count, so a question about something restricted gets exactly the same "not covered" result
as a question about something that doesn't exist: the grade can't be used to probe for hidden data.
"""

from __future__ import annotations

import re
from typing import Any

from .lexical import tokenize

# Question scaffolding and words that carry no subject on their own.
STOP = frozenset("""
a about above after again against all also am an and any are as at be because been before being below between both
but by can could did do does doing done down during each few for from further had has have having he her here hers
him his how i if in into is it its just me more most my no nor not now of off on once only or other our ours out over
own same she should so some such than that the their them then there these they this those through to too under until
up very was we were what when where which while who whom whose why will with would you your yours
tell show give list find explain describe summarize summarise please know need want let get make anything something
much many long often total number amount currently current today latest recent last next first new old us company team
soonest earliest highest lowest largest smallest biggest top bottom best worst most least average sum count per each every
""".split())


def _stem(w: str) -> str:
    for suf in ("ings", "ing", "ies", "es", "ed", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def salient_terms(question: str) -> list[str]:
    """Content words, identifiers and numbers, in order, without duplicates."""
    out: list[str] = []
    for t in tokenize(question):
        if t in STOP or (len(t) < 3 and not any(ch.isdigit() for ch in t)):
            continue
        if t not in out:
            out.append(t)
    return out


def grade(question: str, sources: list[dict[str, Any]], entities: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Coverage of the question's salient terms by these (already permitted) sources and entities.

    Returns {coverage: 0..1, terms, uncovered}. An empty question has coverage 1.
    """
    terms = salient_terms(question)
    if not terms:
        return {"coverage": 1.0, "terms": [], "uncovered": []}
    corpus = " ".join(s.get("text", "") + " " + s.get("title", "") for s in sources)
    for e in entities or []:
        corpus += " " + " ".join([e.get("name", ""), *e.get("aliases", [])])
    have = {_stem(w) for w in tokenize(corpus)}
    for w in tokenize(corpus):  # "at risk" in a source covers "at-risk" in a question, and vice versa
        have.update(_stem(x) for x in re.split(r"[-.]", w) if x)

    def covered(term: str) -> bool:
        if _stem(term) in have:
            return True
        parts = [x for x in re.split(r"[-.]", term) if x and x not in STOP]
        return bool(parts) and all(_stem(x) in have for x in parts)

    uncovered = [t for t in terms if not covered(t)]
    return {"coverage": round(1 - len(uncovered) / len(terms), 3), "terms": terms, "uncovered": uncovered}


def instruction(result: dict[str, Any]) -> str:
    """What the model is told when coverage is below the threshold."""
    missing = ", ".join(f'"{t}"' for t in result["uncovered"][:8])
    return ("Answerability check: the sources this employee can access do not mention " + missing + ". "
            "If the answer depends on these, say you couldn't find it in the information they can access, or ask one "
            "clarifying question. Do not guess or answer from general knowledge.")
