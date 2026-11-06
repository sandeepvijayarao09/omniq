"""Ontology v2 on the real Halcyon graph (no embeddings, no model): schema checks, object / property / link ACL,
entity linking, facts, every ablation switch, the assistant hook (facts are tokenized) and governed actions."""

import copy
from pathlib import Path

import pytest
import yaml

from omniq import db as dbmod
from omniq import harness, ontology
from omniq.docstore import Database
from omniq.pii import Names, Vault
from omniq.policy import User
from omniq.seed import _frontmatter, load_users

SEED = Path(__file__).resolve().parent.parent / "data" / "seed"

MAYA = User(id="emp-1001", name="Maya Chen", role="pro", department="engineering", team="platform",
            title="Senior Software Engineer")                                   # developer: general, engineering, sales
PRIYA = User(id="emp-1003", name="Priya Raman", role="manager", department="engineering", team="platform",
             title="Engineering Manager, Platform", manages=("platform",))
MARCUS = User(id="emp-1009", name="Marcus Whitfield", role="executive", department="executive", team="leadership",
              title="Chief Technology Officer")                                 # cto: + finance, board
ANJALI = User(id="emp-1091", name="Anjali Kumar", role="pro", department="finance", team="finance",
              title="FP&A Analyst")                                             # legacy grants: + finance, no board
LAUREN = User(id="emp-1074", name="Lauren Fitzgerald", role="pro", department="customer_success",
              team="customer-success", title="Senior Customer Success Manager")  # Globex's CSM
SANJAY = User(id="emp-1075", name="Sanjay Rao", role="pro", department="customer_success",
              team="customer-success", title="Customer Success Manager")         # a CSM, but not Globex's
ANDRE = User(id="emp-1073", name="Andre Baptiste", role="manager", department="customer_success",
             team="customer-success", title="Head of Customer Success", manages=("customer-success",))


def _docs() -> list[dict]:
    out = []
    for p in sorted((SEED / "docs").glob("*.md")):
        fm, body = _frontmatter(p.read_text())
        out.append({"doc_id": p.stem, "title": fm.get("title", p.stem), "text": body,
                    "category": fm.get("category", "general"), "team": fm.get("team", ""), "owner": fm.get("owner", "")})
    return out


@pytest.fixture(scope="module")
def seed_graph():
    users, teams = load_users(SEED)
    objects, links = ontology.build_graph(users, teams, yaml.safe_load((SEED / "ontology.yaml").read_text()))
    return users, objects, links + ontology.mention_links(_docs(), objects)


def _load(seed_graph) -> Database:
    users, objects, links = copy.deepcopy(seed_graph)
    d = dbmod.use(Database(":memory:"))
    d.users.insert_many([dict(u) for u in users])
    d.objects.insert_many(objects)
    d.links.insert_many(links)
    ontology.invalidate()
    return d


@pytest.fixture(scope="module")
def store(seed_graph):
    """Shared by the read-only tests (loading the graph is the slow part)."""
    prev = dbmod._current
    d = _load(seed_graph)
    yield d
    dbmod._current = prev
    ontology.invalidate()


@pytest.fixture
def fresh(store, seed_graph):
    """A private copy for tests that change ontology records."""
    d = _load(seed_graph)
    yield d
    dbmod.use(store)
    ontology.invalidate()


def cfg(**onto) -> dict:
    c = copy.deepcopy(harness.DEFAULT_HARNESS)
    c["retrieval"]["graph_context"] = True
    c["ontology"].update(onto)
    return c


def text_of(srcs: list[dict]) -> str:
    return "\n".join(s["text"] for s in srcs)


# ---------------------------------------------------------------- schema

def test_seed_records_satisfy_the_typed_schema():
    users, teams = load_users(SEED)
    objects, links = ontology.build_graph(users, teams, yaml.safe_load((SEED / "ontology.yaml").read_text()))
    by_id = {o["id"]: o for o in objects}
    assert by_id["project-bluefin"]["category"] == "board"          # per-object override
    assert by_id["project-nimbus"]["category"] == "engineering"     # the type's default
    assert {"src": "emp-1003", "type": "leads", "dst": "platform"} in links
    sch = ontology.schema()
    assert sch.object_types["Incident"].properties["service_credits"].category == "finance"
    assert sch.link_types["csm"].inverse == "accounts managed"


def test_schema_violations_are_refused():
    person = {"id": "p", "type": "Person", "name": "P", "category": "general", "props": {}}
    team = {"id": "t", "type": "Team", "name": "T", "category": "general", "props": {}}
    with pytest.raises(ontology.OntologyError, match="unknown object type"):
        ontology.validate([{"id": "x", "type": "Spaceship", "name": "X", "props": {}}], [])
    with pytest.raises(ontology.OntologyError, match="unknown properties"):
        ontology.validate([{**person, "props": {"salary": "1"}}], [])
    with pytest.raises(ontology.OntologyError, match="unknown link type"):
        ontology.validate([person, team], [{"src": "p", "type": "befriends", "dst": "t"}])
    with pytest.raises(ontology.OntologyError, match="cannot join"):
        ontology.validate([person, team], [{"src": "t", "type": "member_of", "dst": "p"}])
    # a link whose other end was never synced is dropped, not an error
    assert ontology.validate([person], [{"src": "p", "type": "member_of", "dst": "t"}]) == []


# ---------------------------------------------------------------- object ACL; restricted == nonexistent

def test_object_acl_and_per_object_category(store):
    assert ontology.get(MAYA, "project-bluefin") is None
    assert ontology.get(MARCUS, "project-bluefin")["name"] == "Project Bluefin"
    assert ontology.get(MAYA, "project-nimbus") is not None
    assert ontology.get(MAYA, "globex-msa") is None and ontology.get(ANJALI, "globex-msa") is not None


def test_restricted_and_nonexistent_look_the_same(store):
    c = cfg()
    real, fake = "project-bluefin", "project-zephyr"
    assert ontology.get(MAYA, real) == ontology.get(MAYA, fake) is None
    assert ontology.sources(MAYA, "What is Project Bluefin's budget?", c) == \
        ontology.sources(MAYA, "What is Project Zephyr's budget?", c) == []
    assert ontology.actions_for(MAYA, real) == ontology.actions_for(MAYA, fake) == []
    assert ontology.history(MAYA, real) == ontology.history(MAYA, fake) == []
    errors = []
    for oid in (real, fake):
        with pytest.raises(PermissionError) as e:
            ontology.run_action(MAYA, "update_deal_stage", oid, {"stage": "Commit"})
        errors.append(str(e.value))
    assert errors[0] == errors[1]


# ---------------------------------------------------------------- property ACL

def test_property_stricter_than_its_object(store):
    inc = ontology.get(MAYA, "inc-2026-08-14")
    assert "service_credits" not in ontology.properties(MAYA, inc)
    assert "severity" in ontology.properties(MAYA, inc)
    assert ontology.properties(ANJALI, inc)["service_credits"] == "42000"
    assert ontology.properties(MARCUS, inc)["service_credits"] == "42000"
    nimbus = ontology.get(MAYA, "project-nimbus")
    assert "budget" not in ontology.properties(MAYA, nimbus) and "budget" in ontology.properties(ANJALI, nimbus)


def test_facts_respect_property_acl(store):
    q = "How long was the August outage?"
    maya, marcus = text_of(ontology.sources(MAYA, q, cfg())), text_of(ontology.sources(MARCUS, q, cfg()))
    assert "2h 41m" in maya and "Service credits" not in maya and "42,000" not in maya
    assert "Service credits: $42,000" in marcus


# ---------------------------------------------------------------- link ACL (and mentions)

def test_link_visible_only_if_both_ends_are(store):
    names = lambda u: {o["name"] for _, o, _ in ontology.neighbors(u, "globex-financial")}  # noqa: E731
    assert "Project Driftwood" not in names(MAYA) and "Globex Financial MSA" not in names(MAYA)
    assert "Project Nimbus" in names(MAYA) and "Lauren Fitzgerald" in names(MAYA)
    assert {"Project Driftwood", "Globex Financial MSA"} <= names(MARCUS)
    facts = text_of(ontology.sources(MAYA, "Tell me about Globex Financial", cfg()))
    assert "Driftwood" not in facts and "MSA" not in facts and "Project Nimbus" in facts


def test_mentions_only_from_documents_the_person_may_read(store):
    maya = ontology.mentioning_documents(MAYA, ["globex-financial"])
    marcus = ontology.mentioning_documents(MARCUS, ["globex-financial"])
    assert "sales-crm-top-accounts" in maya and "eng-incident-postmortem" in maya
    assert not {"board-q3-notes", "board-driftwood-eu"} & maya
    assert {"board-q3-notes", "board-driftwood-eu"} <= marcus
    # the MSA summary names the contract (finance), so only finance readers see that it's mentioned there
    assert "legal-globex-msa" in ontology.mentioning_documents(ANJALI, ["globex-msa"])
    link = next(l for l in store.links.find({"type": "mentions", "src": "doc:board-q3-notes"}))
    assert link["category"] == "board" and not ontology.link_visible(MAYA, link)


# ---------------------------------------------------------------- entity linking

def test_entity_linker(store):
    link = ontology.graph().linker.link
    assert link("Who is the CSM for globex financial?") == ["globex-financial"]          # case-insensitive name
    assert link("Did ADR-007 fix the august outage?") == ["adr-007", "inc-2026-08-14"]  # aliases, in order
    assert link("What did Priya say?") == ["emp-1003"]                                   # unique first name
    assert link("what did priya say?") == []                                              # name parts are capitalized
    assert link("How big is the Platform team?") == ["platform"]
    assert link("Our platform is fast") == []                                             # a bare team name is ambiguous
    assert link("Who runs Data & ML?") == ["data-ml"]
    assert link("self-hosted redis-cluster") == []                                        # no partial-token matches
    # documents are linked with the same linker, and mention links carry their document's access fields
    m = store.links.find_one({"type": "mentions", "src": "doc:eng-incident-postmortem", "dst": "inc-2026-08-14"})
    assert m and m["category"] == "engineering"


def test_linking_skips_what_the_person_cant_see(store):
    assert [o["id"] for o in ontology.linked(MAYA, "Bluefin and Nimbus")] == ["project-nimbus"]
    assert [o["id"] for o in ontology.linked(MARCUS, "Bluefin and Nimbus")] == ["project-bluefin", "project-nimbus"]


# ---------------------------------------------------------------- ablation switches

def test_switch_entity_linking_off_seeds_from_retrieved_documents(store):
    q = "anything at all"
    hits = [{"doc_id": "eng-incident-postmortem"}]
    assert ontology.sources(MAYA, q, cfg(entity_linking=True), hits) == []
    srcs = ontology.sources(MAYA, q, cfg(entity_linking=False), hits)
    assert any(s["title"] == "Ontology: Incident August 14 ingest outage" for s in srcs)
    # a document the person may not read seeds nothing (its mention links are invisible to them)
    assert ontology.sources(MAYA, q, cfg(entity_linking=False), [{"doc_id": "board-bluefin-diligence"}]) == []


def test_switch_hops(store):
    q = "What happened in the August outage?"
    one = ontology.sources(MAYA, q, cfg(hops=1))
    two = ontology.sources(MAYA, q, cfg(hops=2))
    assert [s["title"] for s in one] == ["Ontology: Incident August 14 ingest outage"]
    titles = {s["title"] for s in two}
    assert "Ontology: System Ingest gateway" in titles and "Ontology: Customer Globex Financial" in titles
    assert not any(t.startswith("Ontology: Person") for t in titles)                    # people are listed, not expanded
    assert "Project Driftwood" not in text_of(two)                                       # still ACL'd on hop 2


def test_switch_property_acl_off_is_the_o1_ablation(store):
    q = "How long was the August outage?"
    assert "Service credits: $42,000" in text_of(ontology.sources(MAYA, q, cfg(property_acl=False)))
    assert "Service credits" not in text_of(ontology.sources(MAYA, q, cfg(property_acl=True)))


def test_switch_link_acl_off_lists_hidden_neighbours_but_never_describes_them(store):
    srcs = ontology.sources(MAYA, "Tell me about Globex Financial", cfg(link_acl=False, hops=2))
    facts = text_of(srcs)
    assert "Project Driftwood" in facts and "Globex Financial MSA" in facts               # names leak: that's O1
    assert not any("Driftwood" in s["title"] or "MSA" in s["title"] for s in srcs)        # no record for them


def test_switch_mention_boost(store):
    docs = ontology.mentioning_documents(MAYA, ["globex-financial"])
    plain = [d for d in ("general-pto-policy", "general-benefits", "general-hybrid-work") if d not in docs]
    hits = [{"doc_id": d} for d in plain] + [{"doc_id": "sales-crm-top-accounts"}]
    q = "Globex Financial renewal"
    assert ontology.boost(MAYA, q, hits, cfg(mention_boost=False)) == hits
    boosted = ontology.boost(MAYA, q, hits, cfg(mention_boost=True))
    assert [h["doc_id"] for h in boosted].index("sales-crm-top-accounts") < 3
    assert ontology.boost(MAYA, q, hits, cfg(mention_boost=True, entity_linking=False)) == hits
    assert ontology.boost(MAYA, "the weather", hits, cfg(mention_boost=True)) == hits


def test_switch_tokenize_facts(store):
    srcs = ontology.sources(MAYA, "Who is the CSM for Globex Financial?", cfg())
    vault = Vault(ontology.pii_registry())
    user_msg = "\n".join(f'<source title="{s["title"]}">\n{s["text"]}\n</source>' for s in srcs)
    t_user = vault.tokenize(user_msg)
    assert "Lauren Fitzgerald" in user_msg and "Lauren" not in t_user and "Globex" not in t_user
    assert "[CUSTOMER_1]" in t_user and "[MONEY_" in t_user and vault.leaks(t_user) == []
    assert ontology.payload(t_user, srcs, vault, cfg(tokenize_facts=True)) == t_user
    raw = ontology.payload(t_user, srcs, vault, cfg(tokenize_facts=False))
    assert "Lauren Fitzgerald" in raw and vault.leaks(raw)  # the outbound guard would refuse this payload


def test_research_only_switches_cannot_be_patched():
    base = copy.deepcopy(harness.DEFAULT_HARNESS)
    for k in ("property_acl", "link_acl", "tokenize_facts"):
        assert any("research-only" in p for p in harness.validate_patch(base, {"ontology": {k: False}}))
        assert harness.validate_patch(base, {"ontology": {k: True}})  # not even a no-op
    assert harness.validate_patch(base, {"ontology": {"hops": 2, "mention_boost": True, "entity_linking": False}}) == []
    assert harness.validate_patch(base, {"ontology": {"hops": 3}})
    assert harness.validate_patch(base, {"ontology": {"max_facts": 99}})
    from omniq.governance import check_types

    assert check_types({"ontology": {"hops": "2"}}) and check_types({"ontology": {"mention_boost": 1}})
    # even if a patch slipped past the validator, apply_patch keeps the base's (strict) value
    new = harness.apply_patch(base, {"ontology": {"hops": 2, "property_acl": False}})
    assert new["ontology"]["property_acl"] is True and new["ontology"]["hops"] == 2
    # a stored version without the block reads as the strict defaults
    assert ontology.settings({})["property_acl"] is True and ontology.settings({"ontology": {"hops": 2}})["link_acl"]


# ---------------------------------------------------------------- the assistant hook: facts are tokenized

@pytest.fixture
def assistant_env(store, monkeypatch):
    from omniq import assistant, embed

    monkeypatch.setattr(embed, "backend", lambda: "hash")
    for d in _docs():
        if d["doc_id"] in ("sales-crm-top-accounts", "general-pto-policy"):
            store.chunks.insert_one({"doc_id": d["doc_id"], "title": d["title"], "chunk": 0, "text": d["text"][:1200],
                                     "category": d["category"], "team": "", "owner": "",
                                     "embedding": embed._hash_embed(d["text"][:1200])})
    calls = []
    monkeypatch.setattr(assistant.llm, "complete", lambda s, u, **kw: (calls.append((s, u)) or "ok", {"model": "stub"}))
    assistant.name_registry.cache_clear()
    yield assistant, calls
    assistant.name_registry.cache_clear()


def test_facts_reach_the_model_tokenized(assistant_env):
    assistant, calls = assistant_env
    out = assistant.ask("emp-1001", "Who is the CSM for Globex Financial?", cfg_override=cfg(), source="eval")
    assert "Ontology: Customer Globex Financial" in out["sources"]
    payload = calls[0][0] + calls[0][1]
    assert "customer success manager: [PERSON_" in payload
    for raw in ("Globex", "Lauren Fitzgerald", "Fitzgerald", "$612,000", "Connor Walsh"):
        assert raw not in payload
    assert not out["blocked"]


def test_untokenized_facts_are_still_stopped_by_the_guard(assistant_env):
    assistant, calls = assistant_env
    c = cfg(tokenize_facts=False)
    out = assistant.ask("emp-1001", "Who is the CSM for Globex Financial?", cfg_override=c, source="eval")
    assert out["blocked"] and calls == []                    # guard on: the raw facts never leave
    c["invariants"]["outbound_guard"] = False                # a research run must also switch the guard off
    out = assistant.ask("emp-1001", "Who is the CSM for Globex Financial?", cfg_override=c, source="eval")
    assert "Lauren Fitzgerald" in calls[-1][1] and "Globex Financial" not in out["model_view"]["user"].split("<source")[0]


# ---------------------------------------------------------------- governed actions

def test_action_relationship_checks(store):
    names = lambda u: {a["name"] for a in ontology.actions_for(u, "globex-financial")}  # noqa: E731
    assert names(LAUREN) == {"update_customer_health"}         # Globex's CSM
    assert names(SANJAY) == set()                              # a CSM, but not on this account
    assert names(ANDRE) == {"update_customer_health"}          # managers bypass the relationship check
    assert names(MAYA) == set()                                # wrong department
    with pytest.raises(PermissionError):
        ontology.run_action(SANJAY, "update_customer_health", "globex-financial", {"health": "Healthy", "note": "x"})


def test_action_applies_logs_audits_and_survives_a_reseed(fresh):
    store = fresh
    res = ontology.run_action(LAUREN, "update_customer_health", "globex-financial",
                              {"health": "Watch", "note": "Reliability reviews going well."})
    assert res["changed"]["health"] == ["At risk", "Watch"]
    assert ontology.get(LAUREN, "globex-financial")["props"]["health"] == "Watch"
    with pytest.raises(ontology.OntologyError, match="must be one of"):
        ontology.run_action(LAUREN, "update_customer_health", "globex-financial", {"health": "Great", "note": "x"})
    made = ontology.run_action(PRIYA, "add_incident_action_item", "inc-2026-08-14",
                               {"name": "Chaos test for session failover", "assignee": "Maya Chen"})
    new_id = made["created_id"]
    assert ontology.get(MAYA, new_id)["category"] == "engineering"
    assert any(o["id"] == "emp-1001" for _, o, _ in ontology.neighbors(MAYA, new_id))
    assert [h["action"] for h in ontology.history(LAUREN, "globex-financial")] == ["update_customer_health"]
    assert store.audit.find_one({"kind": "ontology_action", "data.object": "globex-financial"})
    # re-seed the source records, then replay the edit log over them
    users, teams = load_users(SEED)
    objects, links = ontology.build_graph(users, teams, yaml.safe_load((SEED / "ontology.yaml").read_text()))
    store.objects.delete_many({})
    store.links.delete_many({})
    store.objects.insert_many(objects)
    store.links.insert_many(links)
    ontology.invalidate()
    assert ontology.get(LAUREN, "globex-financial")["props"]["health"] == "At risk"
    assert ontology.replay() == 2
    assert ontology.get(LAUREN, "globex-financial")["props"]["health"] == "Watch"
    assert ontology.get(MAYA, new_id) is not None


def test_pii_registry_covers_customers_and_projects(store):
    reg = ontology.pii_registry()
    assert isinstance(reg, Names)
    assert "Globex Financial" in reg.customers and "Project Bluefin" in reg.projects
    assert "Rachel Torres" in reg.full and "Maya Chen" in reg.full
