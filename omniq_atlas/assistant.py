"""One assistant turn, end to end, driven entirely by the role's active harness.

question → harness → (rewrite) → ACL-filtered $vectorSearch (+rerank, +graph, +lessons, +memories)
→ PII tokenization → outbound guard → Claude → de-tokenize → episode + audit + Raindrop
"""

from __future__ import annotations

import datetime as dt
import re
import time
import uuid
from functools import lru_cache
from typing import Any

from . import agents, audit, context, embed, harness, llm, memory, retrieve, telemetry
from .db import db
from .pii import Names, Vault
from .policy import User, describe

BASE_SYSTEM = (
    "You are Omniq, the internal AI assistant at Halcyon Labs. Write the answer the asker needs, nothing else.\n"
    "Rules:\n"
    "1. First sentence = the direct answer. No greeting, no 'Here is', no restating who the asker is or what their role is.\n"
    "2. Use only facts from the <source> passages (already filtered to what this person may see). If the sources "
    "don't contain it, say so in one sentence. Never invent numbers, dates, names or owners.\n"
    "3. Shape length, depth and framing for the asker card, but never mention the card itself.\n"
    "4. If the question is about the asker's own work, answer from their projects and scope; ignore other teams' plans.\n"
    "5. Prefer short bullets; bold the key numbers and dates; keep it under 180 words unless asked for more.\n"
    "6. [PERSON_1]-style tokens are masked names: copy them exactly, never guess.\n"
    "7. End with 'Sources:' and the document titles you used."
)


@lru_cache(maxsize=1)
def name_registry() -> Names:
    names = [u["name"] for u in db().users.find({}, {"name": 1})]
    names += [o["name"] for o in db().objects.find({"type": "Contact"}, {"name": 1})]
    return Names(names)


def _merge_by_doc(hits: list[dict[str, Any]], max_chars: int, max_docs: int) -> list[dict[str, Any]]:
    """One source per document (its passages joined in reading order), most relevant documents first.
    Repeating the same document five times only confuses a small model."""
    order: list[str] = []
    by_doc: dict[str, list[dict[str, Any]]] = {}
    for h in hits:
        key = h.get("doc_id") or h["title"]
        if key not in by_doc:
            order.append(key)
            by_doc[key] = []
        by_doc[key].append(h)
    out = []
    for key in order[:max_docs]:
        parts = sorted(by_doc[key], key=lambda x: x.get("chunk", 0))
        title = parts[0]["title"].split(" (your project:")[0]
        body = "\n…\n".join(p["text"].split("\n\n", 1)[-1][:max_chars] for p in parts)
        out.append({"title": title, "text": body[: max_chars * 2], "doc_id": parts[0].get("doc_id", "")})
    return out


PREAMBLE = re.compile(r"^\s*(?:(?:here(?:'s| is)|below is)[^.:\n]*[:.]|for [A-Z][\w .,'-]{0,60}?,[^.:\n]*[:.]|"
                      r"as (?:a|an|the) [\w ,'-]{0,60}?,\s*)\s*", re.I)


def _clean(answer: str) -> str:
    """Drop chatty preambles small models add ("Here is…", "For Marcus, CTO, …")."""
    out = PREAMBLE.sub("", answer, count=1).strip()
    return (out[:1].upper() + out[1:]) if out else answer


def _md_section(md: str, title: str) -> str:
    m = re.search(rf"^## {re.escape(title)}[^\n]*\n(.*?)(?=^## |\Z)", md, re.S | re.M)
    return m.group(1).strip() if m else ""


def agent_task(cfg: dict[str, Any]) -> str:
    """The @-agent's procedure and output template, restated right before the model answers."""
    ag = cfg.get("agent") or {}
    md = ag.get("instructions", "")
    wf = cfg.get("workflow") or {}  # learned workflow (7-day cycle / improver) overrides the file's
    template = wf.get("template") or _md_section(md, "Output template")
    if not template:
        return ""
    steps = ("\n".join(f"{i}. {x}" for i, x in enumerate(wf["procedure"], 1)) if wf.get("procedure")
             else _md_section(md, "How I work (procedure)"))
    first = re.search(r"\*\*([^*]+)\*\*", template)
    lead = first.group(1).split("/")[0].strip() if first else ""
    if " · " in template:  # one section per line reads far better than a single dense line
        template = "\n".join(part.strip() for part in template.split(" · "))
    return (f"You are {ag.get('name')}. Work through these steps silently:\n{steps}\n\n"
            "Write the answer in exactly this format: each bold label on its own line, followed by its content "
            f"(bullets or a short table where it fits). Keep the bold labels:\n{template}\n"
            + (f"Start your reply with **{lead}**." if lead else ""))


def get_user(user_id: str) -> User:
    doc = db().users.find_one({"id": user_id})
    if not doc:
        raise KeyError(f"unknown user {user_id}")
    return User.from_doc(doc)


def system_prompt(cfg: dict[str, Any], card: str = "") -> str:
    parts = [BASE_SYSTEM]
    if card:  # distilled from data/roles/<role>.md and data/people/<id>.md
        parts.append(f"<asker>\n{card}\n</asker>")
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
IMPROVE_RX = re.compile(r"^\s*(make it|can you make|please make|shorter|longer|more (detail|concise|specific)|less |"
                        r"too (long|short|vague)|add (the|a|more)|include |rewrite|rephrase|improve|instead|be more|"
                        r"you (missed|forgot)|that'?s (wrong|missing|not)|missing |use a table|bullet)", re.I)
SELF_REF = re.compile(r"\b(i|my|me|mine|we|our|us)\b", re.I)


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
    personal_edit = None
    if agent_id and personal_memory and not cfg_override:  # this person's own edits to the agent
        from . import agentedit

        cfg, personal_edit = agentedit.apply_override(cfg, user.id, agent_id)
    vault = Vault(name_registry())

    tq = vault.tokenize(question)
    query = question
    if cfg["retrieval"].get("query_rewrite") and llm.available():
        try:
            query = vault.detokenize(_rewrite(tq, cfg))  # retrieval runs inside our boundary on raw text
        except Exception:
            query = question
    turns = memory.recent_turns(convo_id, user.id) if source == "chat" else []
    improvement_logged = None
    if turns and source == "chat":  # implicit signals: corrections and rephrasings say the last answer missed
        prev = db().episodes.find_one({"user_id": user.id, "convo_id": convo_id, "source": "chat"}, sort=[("ts", -1)])
        if prev and IMPROVE_RX.search(question):
            improvement_logged = request_improvement(prev["_id"], user.id, question, source="chat_followup")
        elif prev:
            a, b = embed.embed_one(question), embed.embed_one(turns[-1]["q"])
            if sum(x * y for x, y in zip(a, b)) >= 0.6:  # calibrated on Voyage: rephrasings 0.67-0.75, unrelated 0.12-0.24
                implicit_signal(prev["_id"], user.id, "rephrased")
    if turns:  # follow-ups ("and their renewal?") retrieve with the previous question for context
        query = f"{turns[-1]['q']} {query}"

    from . import iam

    my_projects = iam.user_projects(user.id) if personal_memory else []
    if my_projects and SELF_REF.search(question):  # "what should I…", "my projects": search with their projects
        query = f"{query} {' '.join(p['name'] for p in my_projects)}"
    hits = retrieve.search_docs(user, query, cfg)
    if personal_memory:  # favour documents from the asker's own projects (still access-controlled)
        hits += retrieve.project_context(user, query, {(h.get("doc_id"), h.get("chunk")) for h in hits})
    sources = _merge_by_doc(hits, int(cfg["context"]["max_chunk_chars"]), max_docs=max(3, int(cfg["retrieval"]["top_k"])))
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
    task = agent_task(cfg)
    if task:  # small models follow the format best when it is the last thing they read
        parts.append(task)
    user_msg = "\n\n".join(parts)
    card, card_files = context.audience_card(user.id if personal_memory else "__nobody__", agents.org_role(user))
    system = system_prompt(cfg, card)
    t_system, t_user = vault.tokenize(system), vault.tokenize(user_msg)

    leaked = vault.leaks(t_system + t_user)
    error = None
    if leaked and cfg["invariants"]["outbound_guard"]:
        answer_t, meta = "Blocked: sensitive data could not be fully tokenized for this request.", {"model": "blocked"}
    else:
        try:
            private_cats = set(llm.registry().get("policy", {}).get("private_only_categories", []))
            private_only = any(cat in private_cats for cat in {h.get("category") for h in hits})
            answer_t, meta = llm.complete(t_system, t_user, max_tokens=int(cfg["model"]["max_tokens"]),
                                          temperature=float(cfg["model"]["temperature"]),
                                          model=(cfg.get("agent") or {}).get("llm_model") or None,
                                          private_only=private_only)
        except Exception as e:  # model down or timed out: degrade, never 500
            error = str(e)[:200]
            answer_t, meta = ("The model is unavailable right now. The most relevant passages I can show you: "
                              + "; ".join(s["title"] for s in sources[:4]), {"model": "unavailable"})
    answer = vault.detokenize(_clean(answer_t))

    event_id = uuid.uuid4().hex
    latency = round(time.time() - t0, 2)
    episode = {"_id": event_id, "ts": dt.datetime.now(dt.timezone.utc), "user_id": user.id, "role": scope,
               "user_role": user.role, "agent_id": agent_id, "convo_id": convo_id,
               "harness_version": version, "source": source, "question_t": tq, "answer_t": answer_t,
               "sources": [s["title"] for s in sources], "model": meta.get("model"), "profile": meta.get("profile"),
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
            "profile": meta.get("profile"), "model_label": meta.get("label") or meta.get("model"),
            "model_skipped": meta.get("skipped", []),
            "latency": latency, "tokens": episode["tokens"], "pii": vault.summary(), "blocked": bool(leaked),
            "preferences_applied": prefs, "lessons_applied": len(notes), "personal_edit": personal_edit,
            "improvement_logged": improvement_logged,
            "context_layers": card_files +
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


IMPLICIT = {"rephrased": False, "regenerated": False, "copied": True, "followup_correction": False}


def implicit_signal(event_id: str, user_id: str, kind: str) -> None:
    """A behavioural signal the user never has to give explicitly. Positive: copied. Negative: rephrased, regenerated."""
    if kind not in IMPLICIT:
        raise ValueError(f"unknown signal {kind}")
    ep = db().episodes.find_one({"_id": event_id}, {"user_id": 1, "agent_id": 1, "role": 1, "harness_version": 1})
    if not ep or ep["user_id"] != user_id:
        return
    db().implicit_signals.update_one(
        {"event_id": event_id, "kind": kind},
        {"$setOnInsert": {"event_id": event_id, "kind": kind, "positive": IMPLICIT[kind], "user_id": user_id,
                          "agent_id": ep.get("agent_id"), "scope": ep["role"], "harness_version": ep["harness_version"],
                          "ts": dt.datetime.now(dt.timezone.utc)}}, upsert=True)
    telemetry.signal(event_id, kind, negative=not IMPLICIT[kind])


def request_improvement(event_id: str, user_id: str, request: str, source: str = "improve_button") -> dict[str, Any]:
    """Log an explicit improvement request against the answer it is about. It becomes this person's preference
    for the agent right away, a governed recommendation for the agent's development, and a Raindrop signal."""
    from . import governance

    ep = db().episodes.find_one({"_id": event_id})
    if not ep or ep["user_id"] != user_id:
        raise KeyError(event_id)
    request = (request or "").strip()[:500]
    if not request:
        raise ValueError("empty improvement request")
    vault = Vault(name_registry())
    flags = governance.screen(request)
    doc = {"event_id": event_id, "user_id": user_id, "agent_id": ep.get("agent_id"), "scope": ep["role"],
           "harness_version": ep["harness_version"], "request_t": vault.tokenize(request), "source": source,
           "status": "quarantined" if flags else "logged", "ts": dt.datetime.now(dt.timezone.utc)}
    db().improvement_requests.insert_one(doc)
    if source == "chat_followup":
        implicit_signal(event_id, user_id, "followup_correction")
    if not flags and ep.get("agent_id"):
        memory.add(ep["agent_id"], user_id, "preference", request, source=event_id)
        memory.recommend(ep["agent_id"], ep["role"], user_id, event_id, vault.detokenize(ep["question_t"]),
                         vault.detokenize(ep.get("answer_t", "")), request, ep["harness_version"])
        db().recommendations.update_one({"event_id": event_id, "text": request}, {"$set": {"source": source}})
    telemetry.signal(event_id, "improvement_request", negative=True, comment=vault.tokenize(request))
    audit.record("improvement_request", user_id, episode=event_id, agent=ep.get("agent_id") or "", source=source,
                 quarantined=bool(flags))
    return {"logged": True, "agent": ep.get("agent_id"), "source": source, "quarantined": bool(flags)}


def remember(user_id: str, text: str, agent_id: str | None = None) -> None:
    vault = Vault(name_registry())
    text = text.strip()[:500]
    if agent_id:
        memory.add(agent_id, user_id, "fact", text, source="remember")
    else:
        db().memories.insert_one({"user_id": user_id, "text": text, "ts": dt.datetime.now(dt.timezone.utc)})
    audit.record("memory", user_id, agent=agent_id or "", text=vault.tokenize(text))
