"""One assistant turn, end to end, driven entirely by the role's active harness.

question → harness → (rewrite) → ACL-filtered $vectorSearch (+rerank, +graph, +lessons, +memories)
→ PII tokenization → outbound guard → Claude → de-tokenize → episode + audit + Raindrop
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from functools import lru_cache
from typing import Any

from . import audit, embed, harness, llm, retrieve, telemetry
from .db import db
from .pii import Names, Vault
from .policy import User, describe

BASE_SYSTEM = (
    "You are Omniq, the internal AI assistant for Halcyon Labs. Answer the employee's question "
    "using only the <source> passages provided; they are already filtered to what this employee may see. "
    "Values like [PERSON_1] or [EMAIL_2] are privacy tokens: use them verbatim, never guess what they hide."
)


@lru_cache(maxsize=1)
def name_registry() -> Names:
    names = [u["name"] for u in db().users.find({}, {"name": 1})]
    names += [o["name"] for o in db().objects.find({"type": "Contact"}, {"name": 1})]
    return Names(names)


def get_user(user_id: str) -> User:
    doc = db().users.find_one({"id": user_id})
    if not doc:
        raise KeyError(f"unknown user {user_id}")
    return User.from_doc(doc)


def system_prompt(cfg: dict[str, Any]) -> str:
    parts = [BASE_SYSTEM]
    g = cfg["guardrails"]
    if g.get("abstain_when_unsupported"):
        parts.append("If the sources do not contain the answer, say you couldn't find it in the documents "
                     "this employee can access. Never answer from general knowledge or speculate.")
    if g.get("cite_sources"):
        parts.append("End with 'Sources:' and the titles of the passages you used.")
    if cfg["rules"]:
        parts.append("Rules learned for this role:\n" + "\n".join(f"- {r}" for r in cfg["rules"]))
    return "\n\n".join(parts)


def _rewrite(question: str, cfg: dict[str, Any]) -> str:
    text, _ = llm.complete("Rewrite the employee question as a concise search query listing the key entities "
                           "and terms. Output only the query.", question, max_tokens=80, temperature=0.0)
    return text.strip() or question


def ask(user_id: str, question: str, *, cfg_override: dict[str, Any] | None = None,
        harness_version: int | None = None, convo_id: str = "", source: str = "chat") -> dict[str, Any]:
    t0 = time.time()
    user = get_user(user_id)
    h = harness.active(user.role)
    cfg = cfg_override or h["config"]
    version = harness_version or h["version"]
    vault = Vault(name_registry())

    tq = vault.tokenize(question)
    query = _rewrite(tq, cfg) if cfg["retrieval"].get("query_rewrite") and llm.available() else question
    query = vault.detokenize(query)  # retrieval runs inside our boundary on raw text

    hits = retrieve.search_docs(user, query, cfg)
    sources = [{"title": x["title"], "text": x["text"], "doc_id": x.get("doc_id", "")} for x in hits]
    if cfg["retrieval"].get("graph_context") or "ontology_lookup" in cfg["tools"]:
        sources += retrieve.graph_context(user, question)
    extras: list[str] = []
    if cfg["retrieval"].get("use_lessons"):
        extras += retrieve.recall_lessons(user.role, question)
    if cfg["context"].get("include_memories") or "memory_recall" in cfg["tools"]:
        extras += [m["text"] for m in db().memories.find({"user_id": user.id}).limit(10)]

    src_xml = "\n".join(f'<source title="{s["title"]}">\n{s["text"]}\n</source>' for s in sources) or "(no sources)"
    user_msg = (f"Employee: {user.name}, {user.title} (role {user.role}; may read: {describe(user)})\n"
                + (("Context notes:\n" + "\n".join(f"- {e}" for e in extras) + "\n") if extras else "")
                + f"{src_xml}\n\nQuestion: {question}")
    system = system_prompt(cfg)
    t_system, t_user = vault.tokenize(system), vault.tokenize(user_msg)

    leaked = vault.leaks(t_system + t_user)
    if leaked and cfg["invariants"]["outbound_guard"]:
        answer_t, meta = "Blocked: sensitive data could not be fully tokenized for this request.", {"model": "blocked"}
    else:
        answer_t, meta = llm.complete(t_system, t_user, max_tokens=int(cfg["model"]["max_tokens"]),
                                      temperature=float(cfg["model"]["temperature"]))
    answer = vault.detokenize(answer_t)

    event_id = uuid.uuid4().hex
    latency = round(time.time() - t0, 2)
    episode = {"_id": event_id, "ts": dt.datetime.now(dt.timezone.utc), "user_id": user.id, "role": user.role,
               "harness_version": version, "source": source, "question_t": tq, "answer_t": answer_t,
               "sources": [s["title"] for s in sources], "model": meta.get("model"),
               "tokens": meta.get("input_tokens", 0) + meta.get("output_tokens", 0), "latency": latency,
               "pii": vault.summary(), "blocked": bool(leaked)}
    if source == "chat":
        db().episodes.insert_one(episode)
        audit.record("ask", user.id, episode=event_id, harness_version=version, question=tq,
                     sources=episode["sources"], model=meta.get("model"), pii=vault.summary())
    telemetry.track_answer(event_id, user.id, tq, answer_t, meta.get("model", ""), convo_id or event_id,
                           {"role": user.role, "harness_version": version, "source": source,
                            "n_sources": len(sources), "latency": latency})
    return {"answer": answer, "event_id": event_id, "harness_version": version, "role": user.role,
            "sources": [s["title"] for s in sources], "model": meta.get("model"), "latency": latency,
            "tokens": episode["tokens"], "pii": vault.summary(), "blocked": bool(leaked),
            "model_view": {"system": t_system, "user": t_user, "answer": answer_t},
            "_context": src_xml}


def feedback(event_id: str, user_id: str, good: bool, comment: str = "") -> None:
    ep = db().episodes.find_one({"_id": event_id})
    if not ep:
        raise KeyError(event_id)
    vault = Vault(name_registry())
    c_t = vault.tokenize(comment)
    db().feedback.insert_one({"event_id": event_id, "user_id": user_id, "role": ep["role"], "good": good,
                              "comment_t": c_t, "harness_version": ep["harness_version"],
                              "question_t": ep["question_t"], "ts": dt.datetime.now(dt.timezone.utc)})
    if not good:
        db().episodes.update_one({"_id": event_id}, {"$set": {
            "failed": True, "failure": f"employee thumbs-down: {c_t}" if c_t else "employee thumbs-down",
            "question": ep["question_t"], "embedding": embed.embed_one(ep["question_t"], "document")}})
    telemetry.signal(event_id, "thumbs_down" if not good else "thumbs_up", negative=not good, comment=c_t)
    audit.record("feedback", user_id, episode=event_id, good=good)


def remember(user_id: str, text: str) -> None:
    vault = Vault(name_registry())
    db().memories.insert_one({"user_id": user_id, "text": text, "ts": dt.datetime.now(dt.timezone.utc)})
    audit.record("memory", user_id, text=vault.tokenize(text))
