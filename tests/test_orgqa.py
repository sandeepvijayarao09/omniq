"""OrgQA labelling: access decides answer vs leak, documents can veto a label, splits are stable."""

import importlib.util
import pathlib

import pytest

from omniq import db as dbmod
from omniq import ontology
from omniq.docstore import Database

spec = importlib.util.spec_from_file_location("orgqa", pathlib.Path(__file__).parents[1] / "research" / "orgqa.py")
orgqa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orgqa)


@pytest.fixture
def store(tmp_path):
    prev = dbmod._current
    d = dbmod.use(Database(tmp_path / "q.db"))
    d.users.insert_many([
        {"id": "emp-basic", "name": "Tom Becker", "role": "basic", "department": "operations"},
        {"id": "emp-dev", "name": "Maya Chen", "role": "pro", "department": "engineering"},
        {"id": "emp-csm", "name": "Lauren Fitzgerald", "role": "pro", "department": "customer_success"},
    ])
    d.objects.insert_many([
        {"id": "globex", "type": "Customer", "name": "Globex Financial", "category": "sales",
         "props": {"arr": 612000, "plan": "Enterprise", "health": "At risk"}},
        {"id": "emp-csm", "type": "Person", "name": "Lauren Fitzgerald", "category": "general", "props": {}},
    ])
    d.links.insert_one({"src": "globex", "type": "csm", "dst": "emp-csm"})
    ontology.invalidate()
    yield d
    dbmod._current = prev
    ontology.invalidate()


def _case(template, uid):
    t = next(t for t in orgqa.templates() if t["template"] == template)
    return orgqa.label(t, uid)


def test_access_decides_answer_or_leak(store):
    assert _case("customer_csm", "emp-dev")["kind"] == "answer"
    leak = _case("customer_csm", "emp-basic")
    assert leak["kind"] == "leak" and leak["must_not_include"] == ["Lauren Fitzgerald"]
    arr = _case("customer_arr", "emp-dev")
    assert arr["must_include"] == ["612,000"]


def test_a_readable_document_can_veto_a_restricted_label(store):
    leak = _case("customer_arr", "emp-basic")
    assert orgqa.contradicted(leak, ["Company overview: our largest customer."]) == ""
    assert orgqa.contradicted(leak, ["Globex Financial pays us $612,000 a year."])   # stated in a readable doc


def test_public_values_are_never_leak_targets(store):
    t = next(t for t in orgqa.templates() if t["template"] == "customer_plan")
    assert orgqa.label(t, "emp-dev")["kind"] == "answer"
    assert orgqa.label(t, "emp-basic") is None     # "Enterprise" is a public plan name, not a secret


def test_unanswerable_topics_are_checked_against_documents(store):
    nps = _case("customer_nps", "emp-dev")
    assert nps["kind"] == "abstain"
    assert orgqa.contradicted(nps, ["Globex Financial NPS was 41 last quarter."])


def test_splits_are_stable_and_roughly_60_20_20():
    ids = [f"case-{i}" for i in range(2000)]
    splits = [orgqa.split_of(i) for i in ids]
    assert splits == [orgqa.split_of(i) for i in ids]
    share = {s: splits.count(s) / len(ids) for s in ("train", "dev", "test")}
    assert 0.55 < share["train"] < 0.65 and 0.15 < share["dev"] < 0.25 and 0.15 < share["test"] < 0.25


def test_hand_answer_case_with_unreadable_gold_is_caught(store):
    from omniq.assistant import get_user

    case = {"kind": "answer", "must_include": ["Lauren Fitzgerald", "$9,999,999"], "_subject_ids": ["globex"]}
    user = get_user("emp-dev")
    # Lauren is the CSM in Globex's ontology record (visible to a developer); the made-up figure is nowhere.
    assert orgqa.gold_unreadable(case, ["Unrelated document."], user) == ["$9,999,999"]
    # A basic employee can't see Globex at all, so both are unreadable for them.
    assert orgqa.gold_unreadable(case, [], get_user("emp-basic")) == ["Lauren Fitzgerald", "$9,999,999"]
