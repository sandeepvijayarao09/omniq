"""Generate the role and employee context layers for the 10-person team.

  data/roles/<org_role>.md   what the role is responsible for and how it wants answers framed
  data/people/<emp-id>.md    one employee's persona: style, focus, current work, preferences

Both are sent to the model on every turn (role, then person, then the @-agent's own file), so the same question
gets a different answer for a CTO than for a developer. Persona details are seeded-random per person (seed = id),
so regenerating is deterministic. Like agent files, these must not contain board, HR or finance facts.

Run:  .venv/bin/python scripts/build_people.py
"""

from __future__ import annotations

import random
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

ROLES = {
    "cto": ("CTO", 3, [
        "Owns technical strategy, architecture direction and engineering spend.",
        "Answers to the CEO and the board for reliability, security and delivery.",
        "Decides build vs buy, platform bets and org design for engineering.",
    ], "Lead with the business impact and the decision needed. Three bullets max, then risks. No implementation detail unless asked."),
    "director": ("Director", 2, [
        "Runs a function (engineering or product) through managers and leads.",
        "Owns delivery against the roadmap, team health, hiring and cross-team trade-offs.",
        "Escalates to the CTO; unblocks the people who report to them.",
    ], "Frame answers as status, risks and the trade-off to decide. Name owners and dates."),
    "product_manager": ("Product Manager", 1, [
        "Owns the problem, the requirements and the success metrics for a product area.",
        "Works with engineering on scope and with customers and sales on what matters.",
        "Writes PRDs, sequences the roadmap and runs launches.",
    ], "Frame answers around users, customer impact, scope and metrics. Call out assumptions."),
    "developer": ("Developer", 1, [
        "Designs, builds, reviews and operates services on the Pulse platform.",
        "Holds the pager on rotation and follows the deploy and incident runbooks.",
        "Cares about correctness, the exact commands and numbers, and the relevant ADR.",
    ], "Be concrete: exact numbers, commands, file or system names, and the ADR or runbook section."),
}

# The 10-person team. reports_to is the line inside this team's view of the org.
TEAM = [
    ("emp-1009", "cto", None, "Platform architecture and the reliability story for enterprise customers"),
    ("emp-1012", "director", "emp-1009", "Engineering delivery across Platform, Ingest, Frontend and SRE"),
    ("emp-1047", "director", "emp-1009", "Product strategy and the Q4 roadmap"),
    ("emp-1048", "product_manager", "emp-1047", "Platform product: SSO/SAML and enterprise readiness"),
    ("emp-1049", "product_manager", "emp-1047", "Ingest product: quotas, pricing tiers and data volume"),
    ("emp-1051", "product_manager", "emp-1047", "AI product: Project Nimbus incident summaries"),
    ("emp-1001", "developer", "emp-1012", "Auth and sessions; landing ADR-007"),
    ("emp-1019", "developer", "emp-1012", "Ingest gateway and the Kafka pipeline"),
    ("emp-1025", "developer", "emp-1012", "Web app performance and the Tide design system"),
    ("emp-1040", "developer", "emp-1012", "SRE: alerting, on-call health and the out-of-band alerting path"),
]

STYLES = {
    "cto": ["I read on my phone between meetings; keep it to one screen.", "Give me the number first, then the story.",
            "Tell me what you'd do if you were me.", "Flag anything the board would ask about."],
    "director": ["I want owners and dates on everything.", "Show me what's at risk before what's on track.",
                 "Tables beat paragraphs for status.", "Tell me which trade-off you'd make and why."],
    "product_manager": ["Start from the customer problem.", "Separate facts from assumptions clearly.",
                        "Give me something I can paste into a PRD or a launch doc.", "Include the metric we'd use to know it worked."],
    "developer": ["Skip the preamble; give me the exact command or threshold.", "Link the decision to the ADR or runbook section.",
                  "Assume I know the codebase; explain only what's non-obvious.", "Code-style formatting for commands and config keys."],
}
QUIRKS = ["I prefer bullet points over prose.", "Put a one-line TL;DR at the top.", "Keep it under 150 words.",
          "Sanity-check my assumptions if they look off.", "Tell me what changed since last week.",
          "Say 'I don't know' rather than guess.", "Use ISO dates."]
TONES = ["direct", "warm but brief", "dry and precise", "curious and detailed", "pragmatic"]
WEEK = {
    "cto": ["have the exec staff meeting on Monday", "am preparing the board technology update", "am reviewing the H1 platform budget"],
    "director": ["have quarterly planning on Monday", "am running calibration with my managers", "am reviewing Q4 delivery risks"],
    "product_manager": ["have customer interviews on Wednesday", "am writing a PRD due Friday", "am preparing a launch review"],
    "developer": ["am on call this week", "am heads-down on a deadline", "have a design review on Thursday", "am pairing on a migration"],
}


def role_file(role: str) -> str:
    title, rank, duties, framing = ROLES[role]
    meta = {"org_role": role, "title": title, "rank": rank}
    lines = [f"# Role: {title}", "", "## What this role is responsible for", *[f"- {d}" for d in duties], "",
             "## How to frame answers for this role", framing, "",
             *access_section(role)]
    return "---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n\n" + "\n".join(lines)


PROJECTS = yaml.safe_load((ROOT / "data" / "projects.yaml").read_text())
IAM = yaml.safe_load((ROOT / "data" / "iam" / "policies.yaml").read_text())


def projects_section(eid: str, role: str, names: dict[str, str]) -> list[str]:
    mine = [p for p in PROJECTS if eid in p["members"]]
    lines = ["## My projects and scope of work"]
    if not mine:
        return lines + ["- No project assignments right now.", ""]
    for p in mine:
        m = p["members"][eid]
        owner = "I own this" if p["owner"] == eid else f"owner: {names.get(p['owner'], p['owner'])}"
        lines += [f"- **{p['name']}** ({p['status']}; {owner})",
                  f"  - My role: {m['role']}", f"  - My scope: {m['scope']}",
                  f"  - Milestones: {'; '.join(p['milestones'])}"]
    if role in ("director", "cto"):
        others = [p["name"] for p in PROJECTS if eid not in p["members"]]
        if others:
            lines.append(f"- I also have visibility into: {', '.join(others)}.")
    return lines + [""]


def access_section(role: str) -> list[str]:
    chain, seen = [], set()

    def visit(r):
        if r in seen:
            return
        seen.add(r)
        chain.append(r)
        for x in IAM["roles"][r].get("inherits", []):
            visit(x)

    visit(role)
    sids = [f"{r}.{st['sid']}" for r in chain for st in IAM["roles"][r]["statements"] if st["effect"] == "Allow"]
    return ["## What my role may access (IAM)", f"Role chain: {' → '.join(chain)}. Allow statements: {', '.join(sids)}.",
            "Anything not allowed is denied; answers must respect this.", ""]


def person_file(emp: dict, role: str, reports_to: str | None, focus: str, names: dict[str, str]) -> str:
    rnd = random.Random(emp["id"])
    styles = rnd.sample(STYLES[role], 2)
    quirks = rnd.sample(QUIRKS, 2)
    meta = {"id": emp["id"], "name": emp["name"], "title": emp["title"], "org_role": role,
            "projects": [p["id"] for p in PROJECTS if emp["id"] in p["members"]],
            "team": emp.get("team", ""), "reports_to": reports_to,
            "reports_to_name": names.get(reports_to) if reports_to else None,
            "tone": rnd.choice(TONES)}
    first = emp["name"].split()[0]
    lines = [f"# {emp['name']}, {emp['title']}", "",
             "## About me",
             f"I'm {first}, {emp['title']} at Halcyon Labs" + (f", reporting to {names[reports_to]}." if reports_to else "."),
             f"My focus right now: {focus}.", f"This week I {rnd.choice(WEEK[role])}.", "",
             "## How I like answers", *[f"- {s}" for s in styles], *[f"- {q}" for q in quirks],
             f"- Tone: {meta['tone']}.", "",
             *projects_section(emp["id"], role, names),
             *access_section(role),
             "## Use this to tailor every answer",
             f"Write for {first} specifically. Relate the answer to my projects and scope above when relevant. Never invent "
             "facts about them; the sources still decide what is true.", ""]
    return "---\n" + yaml.safe_dump(meta, sort_keys=False) + "---\n\n" + "\n".join(lines)


def main() -> None:
    users = {u["id"]: u for u in yaml.safe_load((ROOT / "data" / "seed" / "org.yaml").read_text())["users"]}
    names = {i: users[i]["name"] for i, *_ in TEAM}
    (ROOT / "data" / "roles").mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "people").mkdir(parents=True, exist_ok=True)
    for role in ROLES:
        (ROOT / "data" / "roles" / f"{role}.md").write_text(role_file(role))
    for eid, role, rep, focus in TEAM:
        (ROOT / "data" / "people" / f"{eid}.md").write_text(person_file(users[eid], role, rep, focus, names))
    print(f"wrote {len(ROLES)} role files and {len(TEAM)} people files")


if __name__ == "__main__":
    main()
