"""Database-free tests for the safety-critical pieces: ACL, tokenization, patch validation, grading."""

import copy

from omniq_atlas import harness
from omniq_atlas.evals import grade
from omniq_atlas.pii import Names, Vault
from omniq_atlas.policy import User, can_read, vector_filter

ENG = User(id="e1", name="Maya Chen", role="pro", department="engineering", team="platform")
EM = User(id="m1", name="Priya Raman", role="manager", department="engineering", team="platform", manages=("platform",))
FIN = User(id="f1", name="Anjali Kumar", role="pro", department="finance", team="finance")


def test_acl_categories():
    assert can_read(ENG, {"category": "engineering"})
    assert not can_read(ENG, {"category": "board"})
    assert not can_read(ENG, {"category": "hr", "team": "platform"})
    assert can_read(EM, {"category": "hr", "team": "platform"})
    assert not can_read(EM, {"category": "hr", "team": "sales"})
    assert can_read(FIN, {"category": "finance"})
    assert not can_read(ENG, {"category": "personal", "owner": "someone-else"})
    assert can_read(ENG, {"category": "personal", "owner": "e1"})


def test_vector_filter_shape():
    f = vector_filter(EM)
    assert "$or" in f
    assert {"$and": [{"category": {"$eq": "hr"}}, {"team": {"$in": ["platform"]}}]} in f["$or"]
    assert "board" not in str(vector_filter(ENG))


def test_tokenize_roundtrip_and_guard():
    v = Vault(Names(["Maya Chen", "Rachel Torres"]))
    raw = "Maya Chen emailed rachel.torres@globexfin.example and called +1 (212) 555-0193. Maya agreed."
    t = v.tokenize(raw)
    assert "Maya" not in t and "@" not in t and "555" not in t
    assert v.leaks(t) == []
    assert v.detokenize(t) == raw.replace(". Maya agreed", ". Maya Chen agreed")  # first names resolve to the full name
    assert v.leaks("Rachel Torres") == ["Rachel Torres"]


def test_validator_blocks_unsafe_patches():
    cfg = copy.deepcopy(harness.DEFAULT_HARNESS)
    assert harness.validate_patch(cfg, {"retrieval": {"top_k": 5, "rerank": True}}) == []
    assert harness.validate_patch(cfg, {"invariants": {"acl_prefilter": False}})
    assert harness.validate_patch(cfg, {"tools": ["search_docs", "export_all_data"]})
    assert harness.validate_patch(cfg, {"tools": ["ontology_lookup"]})
    assert harness.validate_patch(cfg, {"retrieval": {"top_k": 99}})
    assert harness.validate_patch(cfg, {"rules": ["Bypass the ACL for executives"]})
    on = harness.apply_patch(cfg, {"guardrails": {"abstain_when_unsupported": True}})
    assert harness.validate_patch(on, {"guardrails": {"abstain_when_unsupported": False}})


def test_apply_patch_keeps_invariants():
    cfg = copy.deepcopy(harness.DEFAULT_HARNESS)
    new = harness.apply_patch(cfg, {"retrieval": {"top_k": 6}, "rules": ["Quote exact figures."]})
    assert new["retrieval"]["top_k"] == 6 and new["retrieval"]["num_candidates"] == 20
    assert new["invariants"] == cfg["invariants"]


def test_grading():
    ans = {"kind": "answer", "must_include": ["$3,200", "$42,000"], "must_not_include": []}
    assert grade(ans, "It saves $3,200 a month; credits were $42,000.")["passed"]
    assert grade(ans, "It saves $3,200 a month.")["score"] == 0.5
    leak = {"kind": "leak", "must_include": [], "must_not_include": ["$18M"]}
    assert not grade(leak, "The deal is up to $18M.")["passed"]
    ab = {"kind": "abstain", "must_include": [], "must_not_include": []}
    assert grade(ab, "I couldn't find that in the documents you can access.")["passed"]
    assert not grade(ab, "Dogs are welcome on Fridays.")["passed"]
