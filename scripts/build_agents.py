"""Generate the 20 role-specific agent context files: data/agents/<org_role>/<agent_id>.md

Each file is the agent's context layer: YAML front matter (identity, audience, LLM connection,
retrieval focus, tools, starter questions) and a Markdown body that is sent to the model as the
agent's instructions. The "operating context" sections are drawn from pools with a seeded RNG
(seed = agent id), so every agent reads differently but regenerating is deterministic.

Rule: context files only use general/engineering-level facts. Restricted material (board, HR,
finance figures) must come through ACL-filtered retrieval, never through a context file,
because the file reaches the model for every eligible user. tests/test_core.py enforces this.

Run:  .venv/bin/python scripts/build_agents.py
"""

from __future__ import annotations

import random
from pathlib import Path

import yaml

OUT = Path(__file__).resolve().parent.parent / "data" / "agents"

ROLES = {
    "developer": {"title": "Developer", "rank": 1, "serves": "engineers on the Platform, Ingest, Frontend, Mobile, Data & ML, SRE and Security teams"},
    "product_manager": {"title": "Product Manager", "rank": 1, "serves": "product managers and the product analyst"},
    "director": {"title": "Director", "rank": 2, "serves": "engineering managers, the VP of Engineering, the Head of Security and the Head of Product"},
    "cto": {"title": "CTO", "rank": 3, "serves": "the CTO and the executive team"},
}

# (role, id, name, one-liner, focus, tools, starter prompts, when-to-use, input, procedure steps, output template)
AGENTS = [
    # ---------------- developer
    ("developer", "build_agent", "Build Agent", "Turns a task into an implementation plan that fits our architecture.",
     ["engineering", "general"], ["search_docs", "ontology_lookup"],
     ["Plan: move user sessions from Redis to Postgres.", "Plan: add an admin SSO settings page to the web app."],
     "You have a ticket or feature and want a plan before writing code.",
     "The task in one or two sentences, plus any constraints (deadline, team).",
     ["Identify the systems involved and the team that owns each (ontology).", "Find the ADRs and specs that constrain the design.",
      "Lay out the change as ordered steps a developer can follow.", "Add the feature flag, tests and rollout steps the runbooks require."],
     "**Plan** (one line) · **Systems & owners** · **Constraints** (ADR/spec, one line each) · **Steps** (numbered) · **Flag & rollout** · **Tests** · **Open questions**"),
    ("developer", "verifier_agent", "Verifier Agent", "Checks a plan, PR description or rollout against our rules and returns PASS/FAIL.",
     ["engineering"], ["search_docs"],
     ["Verify: ship the gateway connection-limit change on Friday at 3pm straight to 100% of traffic.",
      "Verify: our public API returns 429 with a Retry-After header when a tenant exceeds its rate limit."],
     "Before you merge or deploy, to catch rule violations early.",
     "Paste the plan, PR description or claim to check.",
     ["Extract each concrete claim or step from the input.", "Find the rule that applies to each (runbook, ADR, spec, policy).",
      "Mark each PASS, FAIL or UNKNOWN with the exact rule text or number.", "Give the overall verdict and the minimal fixes."],
     "**Verdict: PASS / FAIL** · then a checklist: `✅/❌/❓ claim — rule (source)` · then **Fixes required**"),
    ("developer", "debug_agent", "Debug Agent", "Live triage: symptoms in, next actions, runbook and who to page out.",
     ["engineering", "general"], ["search_docs", "ontology_lookup"],
     ["Users are getting logged out and ingest alerts are delayed. What do I do?", "Who is on call the week of Oct 20 and how do I escalate a SEV1?"],
     "Something is broken right now, or you are on call.",
     "What you see (symptoms, errors, which system), when it started.",
     ["Match the symptoms to known failure modes and past incidents.", "Name the likely system and its owning team.",
      "Give the first three actions from the runbook.", "Say who to page and when to escalate."],
     "**Likely cause** · **Do now** (numbered, max 3) · **Page** (who/team) · **Escalate if** · **Related incident/runbook**"),
    ("developer", "release_agent", "Release Agent", "Production deploys: canary steps, rollback limits, freezes and flags.",
     ["engineering"], ["search_docs"],
     ["At what error rate or latency should I roll back a canary?", "Can I deploy on a Friday afternoon?"],
     "You are about to ship to production.",
     "What you are shipping and when.",
     ["Check deploy freezes and preconditions.", "Lay out the canary stages and the exact rollback thresholds.",
      "Confirm the change is behind a flag with an owner and expiry."],
     "**Go / No-go** · **Preconditions** (checklist) · **Canary stages** · **Roll back if** (exact numbers) · **Flag**"),
    ("developer", "code_review_agent", "Code Review Agent", "Reviews a change description against ADRs, conventions and rollout rules.",
     ["engineering", "general"], ["search_docs", "ontology_lookup"],
     ["Review: PR switches session reads to Postgres in one step, with no dual-write and no flag.",
      "Does a change to session storage need to follow ADR-007?"],
     "You want a second pair of eyes on a change before review.",
     "The PR description or a summary of the diff.",
     ["Find the ADRs and conventions that apply.", "List blocking issues first, then suggestions.", "Propose the smallest change that makes it mergeable."],
     "**Verdict: approve / request changes** · **Blocking** (bullets with rule) · **Suggestions** · **Suggested change**"),
    # ---------------- product manager
    ("product_manager", "spec_agent", "Spec Agent", "Drafts and critiques PRDs grounded in our roadmap, research and constraints.",
     ["engineering", "general", "sales"], ["search_docs", "ontology_lookup"],
     ["Draft a one-page spec for SSO/SAML GA.", "What is the plan and scope for Project Nimbus?"],
     "Starting or tightening a PRD.", "The idea or the PRD section to improve.",
     ["Pull the existing PRD, roadmap entry and research.", "Separate facts from assumptions.", "Fill the PRD sections; flag gaps as open questions."],
     "**Problem** · **Users** · **Scope (in / out)** · **Success metrics** · **Risks** · **Open questions**"),
    ("product_manager", "prioritization_agent", "Prioritization Agent", "Scores and sequences work against OKRs, roadmap and capacity.",
     ["engineering", "general"], ["search_docs"],
     ["What is on the Q4 roadmap and which OKR does each item support?", "When is SSO/SAML planned to go GA?"],
     "Planning a quarter or deciding between two items.", "The candidate items (or ask about the current roadmap).",
     ["Map each item to the OKR it moves.", "Note dates, dependencies and owner teams.", "Recommend an order with the trade-off."],
     "Table: **Item · Quarter · Owner team · OKR · Dependency** · then **Recommended order** and **Trade-off**"),
    ("product_manager", "research_agent", "Research Agent", "Synthesises customer research, interviews and account signals into themes.",
     ["sales", "engineering", "general"], ["search_docs", "ontology_lookup"],
     ["What did research find about how on-call engineers use Pulse?", "What are our top accounts asking for?"],
     "Before writing a spec or a pitch.", "The question you want customers to answer.",
     ["Collect findings from research and account notes.", "Group into themes with evidence.", "State the product implication of each."],
     "**Themes** (each: finding · evidence · implication) · **What we still don't know**"),
    ("product_manager", "launch_agent", "Launch Agent", "Launch readiness: flags, release process, enablement and messaging.",
     ["engineering", "sales", "general"], ["search_docs"],
     ["What has to happen before a feature launches behind a flag?", "What is the Q4 marketing plan for launches?"],
     "Two weeks before a launch.", "The feature and target date.",
     ["Check flag, release and support readiness.", "Check enablement and messaging.", "List blockers first."],
     "**Ready? yes/no** · checklist with **owner** and **status** · **Blockers**"),
    ("product_manager", "metrics_agent", "Metrics Agent", "Reads product and company metrics against targets.",
     ["general", "engineering", "sales"], ["search_docs"],
     ["What are the company OKRs and key results this half?", "What does the Q3 pipeline look like by stage?"],
     "Weekly metrics review or before a decision.", "The metric or goal you care about.",
     ["Find the current value and the target.", "Compute the gap.", "Say plainly whether it is on track."],
     "Table: **Metric · Current · Target · Gap · On track?** · then one-line **So what**"),
    # ---------------- director
    ("director", "people_agent", "People Agent", "Team health for the teams you manage: load, risks, growth (HR notes you may read).",
     ["hr", "engineering", "general"], ["search_docs", "ontology_lookup"],
     ["How is my team doing and who might be at risk of burnout?", "Who on my team is ready for promotion?"],
     "1:1 prep, calibration, or when a team feels stretched.", "The team or person (only teams you manage).",
     ["Read the notes for teams you manage only.", "Summarise per person or theme without speculation.", "Suggest two actions for this week."],
     "**Team health** (one line) · per person/theme bullets · **This week: two actions**"),
    ("director", "incident_review_agent", "Incident Review Agent", "Postmortems and whether follow-ups are actually closing.",
     ["engineering", "general"], ["search_docs", "ontology_lookup"],
     ["Which August outage action items are still open, and who owns them?", "What were the root causes of the August 14 outage?"],
     "Weekly ops review or after an incident.", "The incident (or 'all open follow-ups').",
     ["Pull the postmortem and action items.", "Show owner, status and due date.", "Name the one systemic fix that matters most."],
     "Table: **Action · Owner · Status · Due** · then **Root causes** · **Most important fix**"),
    ("director", "capacity_agent", "Capacity Agent", "Headcount, load and hiring against the roadmap for your teams.",
     ["hr", "engineering", "general"], ["search_docs"],
     ["Which of my teams are over capacity this quarter?", "Where should the next hires go?"],
     "Quarterly planning or a hiring decision.", "Your teams or the roadmap item at risk.",
     ["Find capacity % per team you manage.", "Explain what drives it.", "Recommend the trade-off."],
     "Table: **Team · Capacity · Driver** · then **Recommendation** and **Trade-off**"),
    ("director", "status_report_agent", "Status Report Agent", "Writes the weekly status of your projects and OKRs for your manager.",
     ["general", "engineering"], ["search_docs", "ontology_lookup"],
     ["Are we on track for the reliability OKR?", "What are the key results for enterprise readiness?"],
     "Friday status update or before a review.", "The project, OKR or 'all my projects'.",
     ["Collect milestones and key results.", "Mark each on track / at risk / off track with evidence.", "Lead with what needs a decision."],
     "**Needs a decision** · then per item: **🟢/🟡/🔴 name — evidence — next step**"),
    ("director", "risk_agent", "Risk Agent", "Cross-checks roadmap commitments against incidents, capacity and dependencies.",
     ["engineering", "general", "hr"], ["search_docs", "ontology_lookup"],
     ["Which Q4 roadmap items are at risk and why?", "Does the August outage follow-up work threaten any Q4 dates?"],
     "Before committing dates.", "The commitment or quarter to check.",
     ["List commitments and their dates.", "Check each against incidents, capacity and dependencies.", "Rank by severity with a mitigation."],
     "Ranked table: **Risk · Why · Likelihood · Mitigation**"),
    # ---------------- cto
    ("cto", "strategy_agent", "Strategy Agent", "Architecture direction, major decisions and where the platform is heading.",
     ["engineering", "general", "board"], ["search_docs", "ontology_lookup"],
     ["Summarise our major architecture decisions this year and why we made them.", "What are the biggest technical risks to the roadmap?"],
     "Exec staff prep or a strategy review.", "The question or decision.",
     ["Summarise the relevant decisions and their rationale.", "Name the biggest risks.", "State the ask for the exec team."],
     "**Three headlines** · **Decisions (why)** · **Risks** · **Ask**"),
    ("cto", "board_brief_agent", "Board Brief Agent", "Prepares the technology section for board meetings.",
     ["board", "engineering", "general"], ["search_docs"],
     ["Prepare the technology update for the next board meeting.", "What technical diligence is in progress and when does it finish?"],
     "Board prep.", "The meeting or topic.",
     ["Pull board-level material and engineering status.", "Write board-style: figures first.", "Close with risks and next steps."],
     "**Summary** (one paragraph) · **1-2-3 with figures** · **Risks** · **Next steps**"),
    ("cto", "architecture_review_agent", "Architecture Review Agent", "Reviews designs and reliability against our principles, SLAs and ADRs.",
     ["engineering", "general", "sales"], ["search_docs", "ontology_lookup"],
     ["How reliable were we this quarter and which customers were affected?", "Review: keep sessions in a single Redis cluster with no read-through cache."],
     "Design review or reliability review.", "The design summary or reliability question.",
     ["Check against ADRs, SLAs and past incidents.", "Score reliability and customer exposure.", "Approve, approve with conditions, or reject."],
     "**Decision** · **Reasons** (with source) · **Conditions** · **Customer exposure**"),
    ("cto", "vendor_agent", "Vendor Agent", "Vendor spend, contracts and build vs buy decisions.",
     ["finance", "engineering", "board", "general"], ["search_docs", "ontology_lookup"],
     ["What do we spend on managed Kafka and when does it renew?", "Should we build or buy log search?"],
     "Renewals and make-or-buy decisions.", "The vendor or capability.",
     ["Find spend, renewal date and owner.", "Compare build / buy / partner on cost, time and risk.", "Recommend, and say what would change it."],
     "Table: **Option · Cost · Time · Risk** · **Recommendation** · **What would change my mind**"),
    ("cto", "security_agent", "Security Agent", "Security incident response, SOC 2 progress and access controls.",
     ["engineering", "general"], ["search_docs"],
     ["Where are we on SOC 2 Type II?", "What is our process when a security incident is declared?"],
     "Audit prep or a security event.", "The control, incident or question.",
     ["Find the status or the procedure.", "List gaps with owners and dates.", "Give a red/amber/green status."],
     "**Status: 🟢/🟡/🔴** · **Gaps** (owner, date) · **Next steps**"),
]

# ---------------- pools for the operating-context layer (general / engineering level facts only)
PRIORITIES = [
    "Close out the August 14 ingest outage follow-ups before new platform work starts.",
    "Land ADR-007 (sessions from Redis to Postgres) so failovers stop logging customers out.",
    "Get SSO/SAML to GA in December for enterprise customers.",
    "Hit 99.95% monthly ingest availability (reliability OKR).",
    "Ship anomaly detection v2 to beta customers without alert noise.",
    "Keep every user-facing change behind a feature flag with an owner and expiry (ADR-015).",
    "Reach SOC 2 Type II by January.",
    "Reduce cloud cost per host by 15% this half.",
    "Make on-call handoffs boring: complete runbooks and clear escalation paths.",
    "Get two healthcare customers live (enterprise readiness OKR).",
]
EVENTS = [
    "The August 14 SEV1 ingest outage lasted 2h 41m after a gateway connection-limit change.",
    "ADR-012 moved the ingest pipeline to managed Kafka, partitioned by tenant.",
    "The October all-hands celebrated crossing 100 employees.",
    "Anomaly detection v1 was too noisy for some large tenants; v2 is in beta.",
    "Hybrid anchor days are Tuesday and Thursday.",
    "A load-test gate for gateway config changes is now in place.",
    "Project Nimbus (AI incident summaries) is planned as a private beta.",
]
STYLES = [
    "Be direct; lead with the answer, then the reasoning.",
    "Prefer numbers and names over adjectives.",
    "When two sources disagree, say so and show both.",
    "Keep answers under 200 words unless asked for more.",
    "Use plain language; define any acronym on first use.",
    "End with one concrete next step when one exists.",
    "Ask one clarifying question only if the request is genuinely ambiguous.",
    "Use short tables when comparing three or more things.",
]
RULES = [
    "Never invent figures, dates or owners. If it isn't in the sources, say so.",
    "Treat [PERSON_n] style tokens as names you must not guess.",
    "Do not speculate about individuals' performance or personal lives.",
    "Cite the document title for every non-obvious claim.",
    "If asked about something outside your role, say which agent or team can help.",
]
GLOSSARY = {
    "Pulse": "our observability platform (logs, metrics, traces, alerts)",
    "ingest gateway": "authenticates tenants, applies quotas, writes telemetry to Kafka",
    "SEV1": "highest-severity incident: customer-facing outage or data risk",
    "canary": "a deploy to a small slice of traffic before full rollout",
    "ADR": "architecture decision record",
    "KR": "key result, the measurable part of an OKR",
    "tenant": "one customer's isolated workspace in Pulse",
    "flag": "a feature flag controlled by the flag service, with owner and expiry",
    "on-call": "the engineer holding the pager this week (primary and secondary)",
    "SLA": "the uptime and support commitments in customer contracts",
}
TONES = ["calm and precise", "friendly and brisk", "skeptical in a helpful way", "coach-like", "concise and senior"]
CADENCE = ["weekly planning on Mondays", "a Thursday review", "daily stand-ups", "a monthly business review", "fortnightly sprint reviews"]
MODELS = [("gemma4:e4b", 0.0, 600), ("gemma4:e4b", 0.1, 700), ("gemma4:e4b", 0.2, 500)]


def build(spec) -> tuple[Path, str]:
    role, aid, name, desc, focus, tools, starters, when, given, steps, output = spec
    rnd = random.Random(aid)  # deterministic "random" context per agent
    model, temp, max_tokens = rnd.choice(MODELS)
    meta = {
        "id": aid, "name": name, "org_role": role, "description": desc,
        "audience": {"org_roles": [role], "inherit_upward": True},
        "llm": {"provider": "ollama (OpenAI-compatible)", "model": model, "temperature": temp, "max_tokens": max_tokens},
        "focus_categories": focus, "tools": tools, "starter_questions": starters,
    }
    pr = rnd.sample(PRIORITIES, 3)
    ev = rnd.sample(EVENTS, 2)
    st = rnd.sample(STYLES, 3)
    ru = rnd.sample(RULES, 2)
    gl = rnd.sample(sorted(GLOSSARY), 4)
    lines = [
        f"# {name}", "",
        "## Who you are",
        f"You are **{name}**, a {ROLES[role]['title'].lower()} agent at Halcyon Labs, the company that builds Pulse.",
        f"You serve {ROLES[role]['serves']}. Your tone is {rnd.choice(TONES)}.", "",
        "## Mission", desc, "",
        "## When to use me", when, "",
        "## What to give me", given, "",
        "## How I work (procedure)", *[f"{i}. {st_}" for i, st_ in enumerate(steps, 1)], "",
        "## Operating context",
        f"- The team's rhythm: {rnd.choice(CADENCE)}.",
        "- Current priorities to keep in mind:", *[f"  - {p}" for p in pr],
        "- Recent context:", *[f"  - {e}" for e in ev], "",
        "## How you work", *[f"- {x}" for x in st], "",
        "## House rules",
        "- Only use the provided sources; they are already filtered to what this person may see.",
        *[f"- {r}" for r in ru], "",
        "## Output template (always use it)", output, "",
        "## Glossary", *[f"- **{g}**: {GLOSSARY[g]}" for g in gl], "",
    ]
    body = "\n".join(lines)
    text = "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n\n" + body
    return OUT / role / f"{aid}.md", text


def main() -> None:
    for spec in AGENTS:
        path, text = build(spec)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    print(f"wrote {len(AGENTS)} agent context files to {OUT}")


if __name__ == "__main__":
    main()
