"""Structured data through an external engine: table access, answerability, fail-closed behaviour, the HTTP client."""

import json

import httpx
import pytest

from omniq import harness, structured
from omniq.policy import User

DEV = User(id="emp-1001", name="Maya", role="pro", department="engineering", team="platform", title="Engineer")
CTO = User(id="emp-1009", name="Marcus", role="executive", department="executive", title="Chief Technology Officer")
FIN = User(id="emp-1091", name="Anjali", role="pro", department="finance", title="FP&A Analyst")  # not in the engineering org
TABLES = frozenset({"crm.customers", "crm.deals", "itsm.incidents", "itsm.systems", "finance.vendors",
                    "finance.incident_service_credits", "board.strategic_projects", "directory.employees",
                    "planning.projects"})


@pytest.fixture(autouse=True)
def _catalog(monkeypatch):
    # Hermetic: never read var/warehouse/catalog.json.
    monkeypatch.setattr(structured, "known_tables", lambda: TABLES)
    monkeypatch.setattr(structured, "catalog", lambda: CATALOG)


def test_sql_tables_finds_base_tables_not_ctes():
    sql = """WITH risky AS (SELECT customer_id, arr FROM crm.customers WHERE health = 'At risk')
             SELECT e.name, SUM(r.arr) FROM risky r JOIN directory.employees e ON e.employee_id = r.customer_id
             WHERE r.customer_id IN (SELECT customer_id FROM crm.deals) GROUP BY e.name"""
    assert structured.sql_tables(sql) == ["crm.customers", "crm.deals", "directory.employees"]
    assert structured.sql_tables("SELECT name FROM customers") == ["customers"]
    assert structured.sql_tables("SELEC nonsense FROM (") is None


def test_table_categories_follow_acl_file():
    assert structured.table_category("crm.customers") == "sales"
    assert structured.table_category("itsm.incidents") == "engineering"
    assert structured.table_category("finance.incident_service_credits") == "finance"
    assert structured.table_category("planning.objectives") == "general"   # table override beats schema
    assert structured.table_category("board.strategic_projects") == "board"


def test_authorize_needs_every_table_and_fails_closed():
    assert structured.authorize(DEV, "SELECT SUM(arr) FROM crm.customers")["ok"]
    assert structured.authorize(DEV, "SELECT name FROM customers")["ok"]  # unqualified, resolved via the catalog
    # Incident is engineering but its service credits are finance-only: the join is denied as a whole.
    g = structured.authorize(DEV, "SELECT i.name, c.amount FROM itsm.incidents i "
                                  "JOIN finance.incident_service_credits c USING (incident_id)")
    assert not g["ok"] and g["denied"] == ["finance.incident_service_credits"]
    assert not structured.authorize(DEV, "SELECT * FROM board.strategic_projects")["ok"]
    assert not structured.authorize(DEV, "SELECT * FROM hr.salaries")["ok"]          # not in the catalog
    assert not structured.authorize(DEV, "DROP TABLE (")["ok"]                         # unparseable
    assert structured.authorize(CTO, "SELECT * FROM board.strategic_projects")["ok"]


def test_only_the_persons_access_decides_not_the_agents_focus():
    # Focus ranks documents but doesn't restrict them; tables follow the same rule.
    assert structured.authorize(CTO, "SELECT SUM(amount) FROM finance.incident_service_credits")["ok"]
    assert not structured.authorize(DEV, "SELECT * FROM board.strategic_projects")["ok"]


class FakeEngine:
    name = "fake"

    def __init__(self, coverage=0.9, sql="SELECT name, arr FROM crm.customers WHERE health = 'At risk'",
                 rows=({"name": "Globex Financial", "arr": 612000},), up=True):
        self._cov, self._sql, self._rows, self._up = coverage, sql, list(rows), up
        self.asked = 0

    def available(self):
        return self._up

    def coverage(self, question, user_id):
        return structured.Coverage(self._cov, [] if self._cov >= 0.5 else ["ad spend"])

    def ask(self, question, user_id):
        self.asked += 1
        return structured.Result("Globex Financial is at risk.", self._sql, self._rows, len(self._rows), ["sql"])


# "always" so these tests reach the later stages with lookup-style questions; routing has its own tests.
CFG = {"retrieval": {"structured_min_coverage": 0.5, "structured_route": "always"}, "tools": ["search_docs", "structured_query"]}


def test_query_ok_becomes_a_cited_source():
    rec = structured.query(DEV, "Which accounts are at risk?", CFG, FakeEngine())
    assert rec["status"] == "ok" and rec["tables"] == ["crm.customers"] and rec["categories"] == ["sales"]
    src = structured.as_source(rec)
    assert "612,000" in src["text"] and "SQL:" in src["text"] and src["category"] == "sales"


def test_low_coverage_means_abstain_without_running_sql():
    eng = FakeEngine(coverage=0.2)
    rec = structured.query(DEV, "What was return on ad spend?", CFG, eng)
    assert rec["status"] == "unanswerable" and eng.asked == 0
    assert "do not estimate" in structured.as_source(rec)["text"]


def test_denied_tables_withhold_rows_and_reveal_nothing():
    eng = FakeEngine(sql="SELECT name, budget FROM board.strategic_projects", rows=({"name": "Project Bluefin", "budget": 18000000},))
    # The developer's own tables cover the question (planning.projects), but the engine's SQL reads a board table.
    rec = structured.query(DEV, "What's the total budget of our projects?", CFG, eng)
    assert rec["status"] == "withheld" and "rows" not in rec and "answer" not in rec
    text = structured.as_source(rec)["text"]
    assert "18000000" not in text and "Bluefin" not in text
    # The refusal reads the same whether or not restricted data exists.
    assert text == structured.as_source({"status": "withheld"})["text"]


def test_engine_down_or_off_degrades_quietly():
    assert structured.query(DEV, "q", CFG, FakeEngine(up=False))["status"] == "unavailable"
    assert structured.as_source({"status": "unavailable"}) is None


def test_structured_tool_is_governed_by_the_allow_list():
    cfg = {"agent": {"allowed_tools": ["search_docs"]}, "guardrails": {}}
    assert any("not in allow-list" in p for p in harness.validate_patch(cfg, {"tools": ["search_docs", "structured_query"]}))
    cfg["agent"]["allowed_tools"] = ["search_docs", "structured_query"]
    assert harness.validate_patch(cfg, {"tools": ["search_docs", "structured_query"]}) == []
    assert harness.validate_patch(cfg, {"retrieval": {"structured_min_coverage": 0.05}})  # outside limits


def _mock_ao(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/health":
        return httpx.Response(200, json={"ok": True})
    assert request.headers["x-api-key"] == "tok-maya"
    if request.url.path == "/api/question-entity-coverage":
        return httpx.Response(200, json={"data": {"coverage": 0.83, "uncovered_entities": []}})
    if request.url.path == "/api/chat/completions":
        events = [{"type": "step", "label": "Resolved terms"},
                  {"type": "result", "answer": {"response": "Total ARR at risk is $730,000.",
                                                "sql_code": "SELECT SUM(arr) AS arr FROM crm.customers WHERE health = 'At risk'",
                                                # the exact shape Auto Ontology v1.2.0 sends
                                                "sql_response_from_db": ["[{\"arr\": 730000}]"]}}]
        body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    return httpx.Response(404)


def test_auto_ontology_client_speaks_its_public_api():
    ao = structured.AutoOntology(url="http://ao.test", tokens={"emp-1001": "tok-maya"}, shared_token="",
                                 transport=httpx.MockTransport(_mock_ao))
    assert ao.available()
    assert ao.coverage("total ARR at risk", "emp-1001").coverage == 0.83
    res = ao.ask("total ARR at risk", "emp-1001")
    assert res.rows == [{"arr": 730000}] and res.steps == ["Resolved terms"] and "SUM(arr)" in res.sql
    with pytest.raises(structured.EngineError):
        ao.token_for("emp-9999")  # no per-user token and no shared fallback: refuse rather than guess


def test_what_the_person_sees_cannot_tell_withheld_from_unanswerable():
    from omniq.assistant import _data_view

    withheld = {"status": "withheld", "sql": "SELECT budget FROM board.strategic_projects", "tables": ["board.strategic_projects"],
                "denied": ["board.strategic_projects"], "reason": "needs data you may not see (board)", "coverage": 0.9}
    unanswerable = {"status": "unanswerable", "coverage": 0.1, "uncovered": ["ad spend"]}
    assert _data_view(withheld) == _data_view(unanswerable) == {"status": "not_answerable"}
    ok = _data_view({"status": "ok", "sql": "SELECT 1 FROM crm.customers", "tables": ["crm.customers"], "row_count": 1,
                     "rows": [{"a": 1}], "answer": "x"})
    assert ok == {"status": "ok", "sql": "SELECT 1 FROM crm.customers", "tables": ["crm.customers"], "row_count": 1}


def test_rows_parse_every_shape_the_engine_uses():
    assert structured._rows(["[{\"sum\": 730000.0}]"]) == [{"sum": 730000.0}]
    assert structured._rows([{"a": 1}, "[{\"a\": 2}]"]) == [{"a": 1}, {"a": 2}]
    assert structured._rows({"columns": ["a"], "rows": [[1], [2]]}) == [{"a": 1}, {"a": 2}]
    assert structured._rows("not json") == [] and structured._rows(None) == []


CATALOG = [
    {"name": "crm.customers", "category": "sales", "columns": ["customer_id", "arr", "health", "renewal_date"],
     "description": "Customer accounts from the CRM", "column_notes": {"arr": "Annual recurring revenue in USD"},
     "values": {"health": ["At risk", "Healthy", "Watch"]}},
    {"name": "planning.projects", "category": "engineering", "columns": ["project_id", "name", "budget"],
     "description": "Engineering and product projects", "column_notes": {"budget": "Budget in USD"}, "values": {}},
    {"name": "board.strategic_projects", "category": "board", "columns": ["project_id", "budget"],
     "description": "Board-only strategic projects", "column_notes": {}, "values": {}},
]


def test_catalog_answerability_rejects_what_the_data_never_mentions(monkeypatch):
    monkeypatch.setattr(structured, "catalog", lambda: CATALOG)
    assert structured.catalog_coverage(DEV, "What is the total revenue of at-risk customers?")["coverage"] == 1.0
    g = structured.catalog_coverage(DEV, "What was our return on advertising spend last quarter?")
    assert g["coverage"] < 0.6 and "advertising" in g["uncovered"]


def test_catalog_answerability_only_sees_permitted_tables(monkeypatch):
    monkeypatch.setattr(structured, "catalog", lambda: CATALOG)
    q = "What is the budget of strategic projects?"
    assert structured.catalog_coverage(CTO, q)["coverage"] == 1.0
    assert structured.catalog_coverage(DEV, q)["coverage"] < 1.0   # graded as if the board table didn't exist


def test_question_outside_the_catalog_never_reaches_the_engine(monkeypatch):
    monkeypatch.setattr(structured, "catalog", lambda: CATALOG)
    eng = FakeEngine()
    rec = structured.query(DEV, "What was our return on advertising spend last quarter?", CFG, eng)
    assert rec["status"] == "unanswerable" and eng.asked == 0


def test_routing_sends_only_computations_to_the_database():
    for q in ["What is the total ARR of at-risk customers?", "How many deals are in Commit?",
              "Which customer renews soonest?", "Top 3 vendors by spend", "ARR by plan"]:
        assert structured.needs_computation(q), q
    for q in ["Who is the CSM for Globex Financial?", "What is Globex Financial's ARR?", "Which team owns Redis?"]:
        assert not structured.needs_computation(q), q
    eng = FakeEngine()
    rec = structured.query(DEV, "Who is the CSM for Globex Financial?", {"retrieval": {}}, eng)
    assert rec["status"] == "skipped" and eng.asked == 0


def test_people_outside_the_engineering_org_use_their_document_access_for_tables():
    # The O3 run denied a finance analyst the database entirely (IAM covers only the engineering org).
    assert structured.may_query(FIN)
    assert structured.authorize(FIN, "SELECT SUM(amount) FROM finance.incident_service_credits")["ok"]
    assert not structured.authorize(FIN, "SELECT * FROM board.strategic_projects")["ok"]
