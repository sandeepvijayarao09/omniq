"""Build the Halcyon Labs warehouse: the relational databases a structured-data engine (Auto Ontology) compiles.

The same fictional records that feed Omniq's ontology (data/seed/org.yaml, data/seed/ontology.yaml) are laid out the
way source systems would hold them: a directory, a CRM, an ITSM tool, planning, finance and a board area, one
Postgres schema each, with primary and foreign keys so join paths can be inferred.

Writes
  var/warehouse/halcyon.postgres.sql   DDL + data, loaded by scripts/auto_ontology.sh
  var/warehouse/catalog.json           every table with its columns and access category (data/warehouse/acl.yaml)
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from omniq import structured  # noqa: E402

OUT = ROOT / "var" / "warehouse"


def _list(v: Any) -> list[Any]:
    return [] if v is None else v if isinstance(v, list) else [v]


def tables() -> list[dict[str, Any]]:
    org = yaml.safe_load((ROOT / "data/seed/org.yaml").read_text())
    ont = yaml.safe_load((ROOT / "data/seed/ontology.yaml").read_text())
    T: list[dict[str, Any]] = []

    def table(name, columns, rows, comment):
        T.append({"name": name, "columns": columns, "rows": rows, "comment": comment})

    # columns: (name, sql type, "pk" | "fk:<schema.table>" | None, comment)
    table("directory.teams", [("team_id", "text", "pk", None), ("name", "text", None, "Team name")],
          [(k, v) for k, v in org["teams"].items()], "Teams in the company")
    table("directory.employees",
          [("employee_id", "text", "pk", None), ("name", "text", None, None), ("email", "text", None, None),
           ("title", "text", None, "Job title"), ("department", "text", None, None),
           ("team_id", "text", "fk:directory.teams", None)],
          [(u["id"], u["name"], u["email"], u.get("title"), u.get("department"), u.get("team")) for u in org["users"]],
          "Every employee, from the HR directory")
    table("directory.policies",
          [("policy_id", "text", "pk", None), ("name", "text", None, None), ("summary", "text", None, None),
           ("owner_team_id", "text", "fk:directory.teams", None)],
          [(p["id"], p["name"], p.get("summary"), p["links"].get("policy_owner")) for p in ont["Policy"]],
          "Company policies")

    table("crm.customers",
          [("customer_id", "text", "pk", None), ("name", "text", None, None), ("plan", "text", None, "Subscription plan"),
           ("arr", "numeric", None, "Annual recurring revenue in USD"),
           ("health", "text", None, "Account health: Healthy, Watch, At risk, Prospect"),
           ("segment", "text", None, "Industry segment"), ("renewal_date", "date", None, "Next renewal date"),
           ("health_note", "text", None, None),
           ("account_owner_id", "text", "fk:directory.employees", "Sales account owner"),
           ("csm_id", "text", "fk:directory.employees", "Customer success manager")],
          [(c["id"], c["name"], c.get("plan"), c.get("arr"), c.get("health"), c.get("segment"), c.get("renewal"),
            c.get("health_note"), c["links"].get("account_owner"), c["links"].get("csm")) for c in ont["Customer"]],
          "Customer accounts from the CRM")
    table("crm.contacts",
          [("contact_id", "text", "pk", None), ("name", "text", None, None), ("title", "text", None, None),
           ("email", "text", None, None), ("phone", "text", None, None),
           ("customer_id", "text", "fk:crm.customers", "Company the contact works at"),
           ("is_champion", "boolean", None, "Internal champion for us at their company")],
          [(c["id"], c["name"], c.get("title"), c.get("email"), c.get("phone"), c["links"].get("works_at"),
            c["links"].get("champion_of") == c["links"].get("works_at")) for c in ont["Contact"]],
          "People at customer companies")
    table("crm.deals",
          [("deal_id", "text", "pk", None), ("name", "text", None, None), ("amount", "numeric", None, "Deal value in USD"),
           ("stage", "text", None, "Pipeline stage"), ("close_quarter", "text", None, "Expected close quarter"),
           ("next_step", "text", None, None), ("customer_id", "text", "fk:crm.customers", None),
           ("owner_id", "text", "fk:directory.employees", "Deal owner")],
          [(d["id"], d["name"], d.get("amount"), d.get("stage"), d.get("close_quarter"), d.get("next_step"),
            d["links"].get("deal_for"), d["links"].get("deal_owner")) for d in ont["Deal"]],
          "Open sales opportunities")

    owner_team = {s: t["id"] for t in ont["Team"] for s in _list(t["links"].get("owns_system"))}
    table("itsm.systems",
          [("system_id", "text", "pk", None), ("name", "text", None, None), ("tier", "text", None, "Criticality tier"),
           ("description", "text", None, None), ("owner_team_id", "text", "fk:directory.teams", "Owning team")],
          [(s["id"], s["name"], s.get("tier"), s.get("description"), owner_team.get(s["id"])) for s in ont["System"]],
          "Production systems and services")
    table("itsm.system_dependencies",
          [("system_id", "text", "fk:itsm.systems", None), ("depends_on_system_id", "text", "fk:itsm.systems", None)],
          [(s["id"], d) for s in ont["System"] for d in _list(s["links"].get("depends_on"))],
          "Which system depends on which")
    table("itsm.incidents",
          [("incident_id", "text", "pk", None), ("name", "text", None, None), ("severity", "text", None, None),
           ("started_on", "date", None, None), ("duration", "text", None, None), ("summary", "text", None, None),
           ("commander_id", "text", "fk:directory.employees", "Incident commander")],
          [(i["id"], i["name"], i.get("severity"), i.get("date"), i.get("duration"), i.get("summary"),
            i["links"].get("commanded_by")) for i in ont["Incident"]],
          "Production incidents")
    table("itsm.incident_customers",
          [("incident_id", "text", "fk:itsm.incidents", None), ("customer_id", "text", "fk:crm.customers", None)],
          [(i["id"], c) for i in ont["Incident"] for c in _list(i["links"].get("impacted"))],
          "Customers impacted by each incident")
    table("itsm.incident_systems",
          [("incident_id", "text", "fk:itsm.incidents", None), ("system_id", "text", "fk:itsm.systems", None)],
          [(i["id"], s) for i in ont["Incident"] for s in _list(i["links"].get("affected"))],
          "Systems affected by each incident")
    table("itsm.action_items",
          [("action_item_id", "text", "pk", None), ("name", "text", None, None), ("status", "text", None, None),
           ("due", "text", None, None), ("incident_id", "text", "fk:itsm.incidents", None),
           ("assignee_id", "text", "fk:directory.employees", None)],
          [(a["id"], a["name"], a.get("status"), str(a["due"]) if a.get("due") else None, a["links"].get("action_for"),
            a["links"].get("assigned_to")) for a in ont["ActionItem"]],
          "Follow-up actions from incidents")
    table("itsm.decisions",
          [("decision_id", "text", "pk", None), ("name", "text", None, None), ("status", "text", None, None),
           ("summary", "text", None, None), ("author_id", "text", "fk:directory.employees", None)],
          [(d["id"], d["name"], d.get("status"), d.get("summary"), d["links"].get("authored_by")) for d in ont["Decision"]],
          "Architecture decision records")

    open_projects = [p for p in ont["Project"] if p.get("category") != "board"]
    board_projects = [p for p in ont["Project"] if p.get("category") == "board"]
    table("planning.projects",
          [("project_id", "text", "pk", None), ("name", "text", None, None), ("status", "text", None, None),
           ("budget", "numeric", None, "Budget in USD"), ("summary", "text", None, None),
           ("team_id", "text", "fk:directory.teams", "Delivering team")],
          [(p["id"], p["name"], p.get("status"), p.get("budget"), p.get("summary"), p["links"].get("project_team"))
           for p in open_projects],
          "Engineering and product projects")
    table("planning.project_customers",
          [("project_id", "text", "fk:planning.projects", None), ("customer_id", "text", "fk:crm.customers", None)],
          [(p["id"], c) for p in open_projects for c in _list(p["links"].get("project_customer"))],
          "Customers each project is for")
    table("planning.objectives",
          [("objective_id", "text", "pk", None), ("name", "text", None, None), ("key_results", "text", None, None),
           ("owner_id", "text", "fk:directory.employees", None)],
          [(o["id"], o["name"], o.get("key_results"), o["links"].get("objective_owner")) for o in ont["Objective"]],
          "Company objectives (OKRs)")

    table("finance.vendors",
          [("vendor_id", "text", "pk", None), ("name", "text", None, None),
           ("annual_value", "numeric", None, "Annual spend in USD"), ("renewal", "text", None, None),
           ("owner_id", "text", "fk:directory.employees", None)],
          [(v["id"], v["name"], v.get("annual_value"), v.get("renewal"), v["links"].get("vendor_owner")) for v in ont["Vendor"]],
          "Suppliers and what we spend with them")
    table("finance.contracts",
          [("contract_id", "text", "pk", None), ("name", "text", None, None),
           ("annual_value", "numeric", None, "Annual contract value in USD"), ("renewal_terms", "text", None, None),
           ("key_terms", "text", None, None), ("customer_id", "text", "fk:crm.customers", None)],
          [(c["id"], c["name"], c.get("annual_value"), c.get("renewal"), c.get("key_terms"), c["links"].get("contract_with"))
           for c in ont["Contract"]],
          "Customer contracts")
    table("finance.incident_service_credits",
          [("incident_id", "text", "fk:itsm.incidents", None), ("amount", "numeric", None, "Service credits owed in USD")],
          [(i["id"], i["service_credits"]) for i in ont["Incident"] if i.get("service_credits") is not None],
          "Service credits owed because of incidents")

    table("board.strategic_projects",
          [("project_id", "text", "pk", None), ("name", "text", None, None), ("status", "text", None, None),
           ("budget", "numeric", None, "Budget in USD"), ("summary", "text", None, None),
           ("customer_id", "text", "fk:crm.customers", None)],
          [(p["id"], p["name"], p.get("status"), p.get("budget"), p.get("summary"),
            (_list(p["links"].get("project_customer")) or [None])[0]) for p in board_projects],
          "Board-only strategic projects")
    table("board.strategic_project_owners",
          [("project_id", "text", "fk:board.strategic_projects", None), ("owner_id", "text", "fk:directory.employees", None)],
          [(p["id"], o) for p in board_projects for o in _list(p["links"].get("project_owner"))],
          "Executives who own each strategic project")
    return T


def _lit(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return f"'{v.isoformat()}'"
    return "'" + str(v).replace("'", "''") + "'"


def postgres_sql(T: list[dict[str, Any]]) -> str:
    out = ["-- Halcyon Labs warehouse. Generated by scripts/build_warehouse.py; every value is fictional.",
           "SET client_min_messages = warning;"]
    for schema in sorted({t["name"].split(".")[0] for t in T}):
        out.append(f"CREATE SCHEMA IF NOT EXISTS {schema};")
    for t in T:  # tables first, keys after, so creation order doesn't matter
        cols = ",\n  ".join(f"{c} {typ}" + (" PRIMARY KEY" if k == "pk" else "") for c, typ, k, _ in t["columns"])
        out.append(f"CREATE TABLE {t['name']} (\n  {cols}\n);")
        out.append(f"COMMENT ON TABLE {t['name']} IS {_lit(t['comment'])};")
        for c, _, _, note in t["columns"]:
            if note:
                out.append(f"COMMENT ON COLUMN {t['name']}.{c} IS {_lit(note)};")
        if t["rows"]:
            vals = ",\n  ".join("(" + ", ".join(_lit(v) for v in r) + ")" for r in t["rows"])
            out.append(f"INSERT INTO {t['name']} VALUES\n  {vals};")
    for t in T:
        for c, _, k, _ in t["columns"]:
            if k and k.startswith("fk:"):
                ref = k[3:]
                ref_pk = next(col for col, _, kk, _ in next(x for x in T if x["name"] == ref)["columns"] if kk == "pk")
                out.append(f"ALTER TABLE {t['name']} ADD FOREIGN KEY ({c}) REFERENCES {ref} ({ref_pk});")
    return "\n".join(out) + "\n"


def catalog(T: list[dict[str, Any]]) -> dict[str, Any]:
    acl = structured.load_acl()
    return {"database": "halcyon", "tables": [
        {"name": t["name"], "category": structured.table_category(t["name"], acl), "rows": len(t["rows"]),
         "columns": [c for c, *_ in t["columns"]], "description": t["comment"]} for t in T]}


def main() -> None:
    T = tables()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "halcyon.postgres.sql").write_text(postgres_sql(T))
    (OUT / "catalog.json").write_text(json.dumps(catalog(T), indent=2, default=str))
    print(f"{len(T)} tables, {sum(len(t['rows']) for t in T)} rows → {OUT}")


if __name__ == "__main__":
    main()
