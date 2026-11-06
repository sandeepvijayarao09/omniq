"""Answerability: coverage of a question by the person's permitted sources."""

from omniq_atlas import answerability, harness

SOURCES = [{"title": "Incident postmortem", "text": "The August 14 ingest outage lasted 2h 41m. Rollback took 12 minutes."},
           {"title": "Q3 OKRs", "text": "Reliability: 99.95% ingest availability monthly."}]


def test_salient_terms_drop_question_scaffolding():
    assert answerability.salient_terms("What was the total ARR of our at-risk accounts last quarter?") == \
        ["arr", "at-risk", "accounts", "quarter"]
    assert answerability.salient_terms("Tell me about ADR-007") == ["adr-007"]


def test_covered_question_grades_high_and_missing_terms_are_named():
    g = answerability.grade("How long did the August ingest outage last?", SOURCES)
    assert g["coverage"] == 1.0 and g["uncovered"] == []
    g = answerability.grade("What was return on advertising spend for the outage?", SOURCES)
    assert g["coverage"] < 0.5 and "advertising" in g["uncovered"]
    assert "advertising" in answerability.instruction(g)


def test_hyphenated_terms_match_spaced_text():
    g = answerability.grade("Which accounts are at-risk?", [{"title": "", "text": "Corvid Insurance: health At risk"}])
    assert "at-risk" not in g["uncovered"]


def test_entities_the_person_may_read_count_as_coverage():
    q = "Who owns Project Nimbus?"
    assert answerability.grade(q, SOURCES)["coverage"] < 1
    ent = {"name": "Project Nimbus", "aliases": ["nimbus"]}
    assert "nimbus" not in answerability.grade(q, SOURCES, [ent])["uncovered"]


def test_restricted_and_nonexistent_look_the_same():
    # The caller passes only permitted sources, so a hidden document and a missing one grade identically.
    a = answerability.grade("What is Project Bluefin's valuation?", SOURCES)
    b = answerability.grade("What is Project Zephyr's valuation?", SOURCES)
    assert a["coverage"] == b["coverage"]


def test_threshold_is_a_bounded_harness_knob():
    cfg = {"guardrails": {}}
    assert harness.validate_patch(cfg, {"retrieval": {"answerability_min": 0.6}}) == []
    assert harness.validate_patch(cfg, {"retrieval": {"answerability_min": 0.99}})
