"""Privacy v2: the 14 categories, org registries, alias tokens, the outbound guard and the per-conversation vault."""

import pytest

from omniq import db as dbmod
from omniq import memory
from omniq.docstore import Database
from omniq.pii import ALL_CATEGORIES, Category, Names, Tokenizer, Vault

NAMES = Names(["Maya Chen", "Leo Martins", "Rachel Torres"], customers=["Globex Financial"],
              projects=["Project Bluefin"])


@pytest.mark.parametrize("text,category", [
    ("mail maya@example.com now", Category.EMAIL),
    ("call +1 (415) 555-0142 today", Category.PHONE),
    ("SSN 123-45-6789 on file", Category.NATIONAL_ID),
    ("passport no. X12345678", Category.NATIONAL_ID),
    ("card 4111 1111 1111 1111", Category.CREDIT_CARD),
    ("IBAN GB82 WEST 1234 5698 7654 32", Category.BANK_ACCOUNT),
    ("routing number 021000021", Category.BANK_ACCOUNT),
    ("budget of $4.48M", Category.MONEY),
    ("host 10.20.4.17 is down", Category.IP_ADDRESS),
    ("MRN: 00412-77 admitted", Category.HEALTH),
    ("diagnosis F41.1 noted", Category.HEALTH),
    ("Globex Financial renewed", Category.CUSTOMER),
    ("Project Bluefin diligence", Category.PROJECT),
    ("key sk-ant-api03-abcdefghijklmnopqrstuv", Category.SECRET),
    ("password: hunter2-long", Category.SECRET),
    ("lives at 1440 Alder Street, Apt 2B", Category.ADDRESS),
    ("DOB: 1999-03-14", Category.DATE_OF_BIRTH),
    ("Maya Chen wrote it", Category.PERSON),
])
def test_each_category_is_detected(text, category):
    assert [s.category for s in NAMES.tokenizer.find(text)] == [category]


def test_fourteen_categories():
    assert len(Category) == len(ALL_CATEGORIES) == 14


def test_full_names_parts_and_codenames_share_one_token():
    v = Vault(NAMES)
    out = v.tokenize("Maya Chen emailed maya.chen@corp.example; later maya chen called. Chen agreed.")
    assert out == "[PERSON_1] emailed [EMAIL_1]; later [PERSON_1] called. [PERSON_1] agreed."
    assert v.detokenize(out) == "Maya Chen emailed maya.chen@corp.example; later Maya Chen called. Maya Chen agreed."
    assert v.tokenize("Bluefin is need-to-know; Project Bluefin closes in Nov.") == \
        "[PROJECT_1] is need-to-know; [PROJECT_1] closes in Nov."


def test_validators_and_no_retokenizing():
    t = Tokenizer()
    assert t.find("order 1234 5678 9012 3456") == []          # fails Luhn
    assert t.find("ref GB00 WEST 1234 5698 7654 32") == []    # fails IBAN mod-97
    assert t.find("the maya calendar") == []                   # lowercase alone is not a name
    v = Vault(NAMES)
    once = v.tokenize("Maya Chen paid $500 to Globex Financial")
    assert v.tokenize(once) == once and v.leaks(once) == []


def test_guard_uses_the_same_detectors():
    v = Vault(NAMES)
    payload = v.tokenize("Rachel Torres at Globex Financial, ARR $612,000, host 10.0.0.7")
    assert v.leaks(payload) == []
    assert set(v.leaks("Rachel Torres at Globex Financial, $612,000")) == {"Rachel Torres", "Globex Financial",
                                                                            "$612,000"}


def test_summary_counts_this_request_only():
    v = Vault(NAMES)
    v.tokenize("Maya Chen and Leo Martins, $5")
    assert v.summary() == {"MONEY": 1, "PERSON": 2}
    again = Vault.from_json(v.to_json(), NAMES)
    again.tokenize("Leo Martins again")
    assert again.summary() == {"PERSON": 1}


def test_vault_is_stable_across_turns():
    v1 = Vault(NAMES)
    t1 = v1.tokenize("Maya Chen owns Globex Financial")
    v2 = Vault.from_json(v1.to_json(), NAMES)
    t2 = v2.tokenize("Leo Martins and Maya Chen; Globex Financial again")
    assert t1 == "[PERSON_1] owns [CUSTOMER_1]"
    assert t2 == "[PERSON_2] and [PERSON_1]; [CUSTOMER_1] again"
    assert v2.detokenize("[PERSON_1] and [PERSON_2]") == "Maya Chen and Leo Martins"


def test_conversation_vault_round_trips_through_the_store():
    prev = dbmod._current
    dbmod.use(Database(":memory:"))
    try:
        v = memory.conversation_vault("c1", "u1", NAMES)
        assert v.tokenize("Maya Chen") == "[PERSON_1]"
        memory.save_vault("c1", "u1", v)
        memory.remember_turn("c1", "u1", "", "q", "a")  # the turn log and the vault share the document
        w = memory.conversation_vault("c1", "u1", NAMES)
        assert w.tokenize("Leo Martins, Maya Chen") == "[PERSON_2], [PERSON_1]"
        assert memory.conversation_vault("c1", "someone-else", NAMES).tokenize("Leo Martins") == "[PERSON_1]"
        assert memory.conversation_vault("", "u1", NAMES).tokenize("Leo Martins") == "[PERSON_1]"
    finally:
        dbmod._current = prev


def test_categories_can_be_narrowed_for_research_runs():
    v = Vault(NAMES, frozenset({Category.EMAIL}))
    assert v.tokenize("$500 to a@b.co from Maya Chen") == "$500 to [EMAIL_1] from Maya Chen"
