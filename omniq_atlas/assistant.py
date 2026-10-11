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

from . import agents, audit, context, embed, harness, llm, memory, retrieve, telemetry
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


def system_prompt(cfg: dict[str, Any], persona_layers: list[tuple[str, str]] | None = None) -> str:
    parts = [BASE_SYSTEM]
    for label, body in persona_layers or []:  # role context, then the employee's own persona
        parts.append(f"<context file=\"{label}\">\n{body}\n</context>")
    if cfg.get("agent"):  # the @-agent's context file, verbatim
        parts.append(f"<context file=\"{cfg['agent'].get('context_file') or 'agent'}\">\n"
                     f"{cfg['agent']['instructions'].strip()}\n</context>")
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


MAX_QUESTION_CHARS = 2000


class Forbidden(PermissionError):
    pass


def scope_for(user: User, agent_id: str | None) -> tuple[str, dict[str, Any] | None]:
    if not agent_id:
        return user.role, None
    tpl = agents.get(agent_id)
    if tpl is None:
        raise KeyError(f"unknown agent {agent_id}")
    if not agents.eligible(user, tpl):
        raise Forbidden(f"{user.name} is not in the audience for {tpl['name']}")
    return harness.agent_scope(agent_id), tpl


def ask(user_id: str, question: str, *, agent_id: str | None = None, cfg_override: dict[str, Any] | None = None,
        harness_version: int | None = None, convo_id: str = "", source: str = "chat",
        personal_memory: bool = True) -> dict[str, Any]:
    t0 = time.time()
    question = (question or "").strip()[:MAX_QUESTION_CHARS]
    if not question:
        raise ValueError("empty question")
    user = get_user(user_id)
    scope, tpl = scope_for(user, agent_id)
    h = harness.active(scope)
    cfg = cfg_override or h["config"]
    version = harness_version or h["version"]
    vault = Vault(name_registry())

    tq = vault.tokenize(question)
    query = question
    if cfg["retrieval"].get("query_rewrite") and llm.available():
        try:
            query = vault.detokenize(_rewrite(tq, cfg))  # retrieval runs inside our boundary on raw text
        except Exception:
            query = question
    turns = memory.recent_turns(convo_id, user.id) if source == "chat" else []
    if turns:  # follow-ups ("and their renewal?") retrieve with the previous question for context
        query = f"{turns[-1]['q']} {query}"

    hits = retrieve.search_docs(user, query, cfg)
    sources = [{"title": x["title"], "text": x["text"], "doc_id": x.get("doc_id", "")} for x in hits]
    if cfg["retrieval"].get("graph_context") or "ontology_lookup" in cfg["tools"]:
        sources += retrieve.graph_context(user, question)

    # ---- memory layer: this user's preferences + agent-wide lessons (agents), role lessons, facts
    prefs: list[str] = []
    notes: list[str] = []
    mems = memory.recall(agent_id, user.id if personal_memory else "__nobody__", question) if agent_id else []
    if not cfg["retrieval"].get("use_lessons"):  # agent-wide lessons are opt-in per harness version
        mems = [m for m in mems if m["kind"] != "lesson"]
    for m in mems:
        (prefs if m["kind"] == "preference" else notes).append(m["text"])
    if cfg["retrieval"].get("use_lessons"):
        notes += retrieve.recall_lessons(scope, question)
    if cfg["context"].get("include_memories") or "memory_recall" in cfg["tools"]:
        notes += [m["text"] for m in db().memories.find({"user_id": user.id}).limit(10)]

    src_xml = "\n".join(f'<source title="{s["title"]}">\n{s["text"]}\n</source>' for s in sources) or "(no sources)"
    parts = [f"Employee: {user.name}, {user.title} (role {user.role}; may read: {describe(user)})"]
    if prefs:
        parts.append("This employee's standing preferences for how you answer (follow them unless they conflict "
                     "with the rules above):\n" + "\n".join(f"- {p}" for p in prefs))
    if notes:
        parts.append("Context notes:\n" + "\n".join(f"- {n}" for n in notes))
    if turns:
        parts.append("Earlier in this conversation:\n" + "\n".join(f"Q: {t['q']}\nA: {t['a'][:400]}" for t in turns))
    parts += [src_xml, f"Question: {question}"]
    user_msg = "\n\n".join(parts)
    persona_layers = context.layers(user.id, agents.org_role(user)) if personal_memory else \
        ([] if not agents.org_role(user) else context.layers("__nobody__", agents.org_role(user)))
    system = system_prompt(cfg, persona_layers)
    t_system, t_user = vault.tokenize(system), vault.tokenize(user_msg)

    leaked = vault.leaks(t_system + t_user)
    error = None
    if leaked and cfg["invariants"]["outbound_guard"]:
        answer_t, meta = "Blocked: sensitive data could not be fully tokenized for this request.", {"model": "blocked"}
    else:
        try:
            answer_t, meta = llm.complete(t_system, t_user, max_tokens=int(cfg["model"]["max_tokens"]),
                                          temperature=float(cfg["model"]["temperature"]),
                                          model=(cfg.get("agent") or {}).get("llm_model") or None)
        except Exception as e:  # model down or timed out: degrade, never 500
            error = str(e)[:200]
            answer_t, meta = ("The model is unavailable right now. The most relevant passages I can show you: "
                              + "; ".join(s["title"] for s in sources[:4]), {"model": "unavailable"})
    answer = vault.detokenize(answer_t)

    event_id = uuid.uuid4().hex
    latency = round(time.time() - t0, 2)
    episode = {"_id": event_id, "ts": dt.datetime.now(dt.timezone.utc), "user_id": user.id, "role": scope,
               "user_role": user.role, "agent_id": agent_id, "convo_id": convo_id,
               "harness_version": version, "source": source, "question_t": tq, "answer_t": answer_t,
               "sources": [s["title"] for s in sources], "model": meta.get("model"),
               "tokens": meta.get("input_tokens", 0) + meta.get("output_tokens", 0), "latency": latency,
               "pii": vault.summary(), "blocked": bool(leaked), "memories_applied": len(mems), "error": error}
    if source == "chat":
        db().episodes.insert_one(episode)
        memory.remember_turn(convo_id, user.id, agent_id or "", question, answer)
        audit.record("ask", user.id, episode=event_id, scope=scope, harness_version=version, question=tq,
                     sources=episode["sources"], model=meta.get("model"), pii=vault.summary())
    if source == "chat":  # only real conversations go to Raindrop (eval traffic would drown the signal and quota)
        telemetry.track_answer(event_id, user.id, tq, answer_t, meta.get("model", ""), convo_id or event_id,
                           {"scope": scope, "agent": agent_id or "", "harness_version": version, "source": source,
                            "n_sources": len(sources), "latency": latency, "memories_applied": len(mems)})
    return {"answer": answer, "event_id": event_id, "harness_version": version, "scope": scope, "role": user.role,
            "agent": (tpl or {}).get("name"), "sources": [s["title"] for s in sources], "model": meta.get("model"),
            "latency": latency, "tokens": episode["tokens"], "pii": vault.summary(), "blocked": bool(leaked),
            "preferences_applied": prefs, "lessons_applied": len(notes),
            "context_layers": [label for label, _ in persona_layers] +
                              ([cfg["agent"].get("context_file")] if cfg.get("agent") else []),
            "model_view": {"system": t_system, "user": t_user, "answer": answer_t},
            "_context": src_xml}


def feedback(event_id: str, user_id: str, good: bool, comment: str = "", recommendation: str = "") -> dict[str, Any]:
    """Thumbs up/down. A recommendation ("next time, ...") becomes this user's preference for the agent
    immediately, and is queued for the agent's improvement loop to consider for everyone."""
    ep = db().episodes.find_one({"_id": event_id})
    if not ep or ep["user_id"] != user_id:
        raise KeyError(event_id)
    vault = Vault(name_registry())
    c_t = vault.tokenize(comment)
    rec = recommendation.strip()[:500]
    db().feedback.insert_one({"event_id": event_id, "user_id": user_id, "role": ep["role"], "good": good,
                              "comment_t": c_t, "recommendation_t": vault.tokenize(rec),
                              "harness_version": ep["harness_version"], "question_t": ep["question_t"],
                              "ts": dt.datetime.now(dt.timezone.utc)})
    if not good:
        db().episodes.update_one({"_id": event_id}, {"$set": {
            "failed": True, "failure": f"employee thumbs-down: {c_t or vault.tokenize(rec)}",
            "question": ep["question_t"], "embedding": embed.embed_one(ep["question_t"], "document")}})
    out = {"queued": False, "preference": False}
    if rec and ep.get("agent_id"):
        from . import governance

        flags = governance.screen(rec)
        if flags or not governance.recommendation_allowed(user_id, ep["agent_id"]):
            audit.record("recommendation_quarantined", user_id, episode=event_id, reasons=flags or ["daily limit"])
            telemetry.signal(event_id, "recommendation_quarantined", negative=True, comment=vault.tokenize(rec))
            return {"queued": False, "preference": False, "quarantined": True,
                    "reasons": flags or ["daily recommendation limit reached"]}
        q = vault.detokenize(ep["question_t"])
        memory.add(ep["agent_id"], user_id, "preference", rec, source=event_id)
        memory.recommend(ep["agent_id"], ep["role"], user_id, event_id, q, vault.detokenize(ep["answer_t"]), rec,
                         ep["harness_version"])
        out = {"queued": True, "preference": True}
    telemetry.signal(event_id, "thumbs_down" if not good else "thumbs_up", negative=not good,
                     comment=c_t or vault.tokenize(rec))
    if rec:
        telemetry.signal(event_id, "recommendation", negative=True, comment=vault.tokenize(rec))
    audit.record("feedback", user_id, episode=event_id, good=good, recommendation=bool(rec))
    return out


def remember(user_id: str, text: str, agent_id: str | None = None) -> None:
    vault = Vault(name_registry())
    text = text.strip()[:500]
    if agent_id:
        memory.add(agent_id, user_id, "fact", text, source="remember")
    else:
        db().memories.insert_one({"user_id": user_id, "text": text, "ts": dt.datetime.now(dt.timezone.utc)})
    audit.record("memory", user_id, agent=agent_id or "", text=vault.tokenize(text))
