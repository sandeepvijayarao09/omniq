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

AGENTS = [
    # ---------------- developer
    ("developer", "code_review_buddy", "Code Review Buddy", "Reviews changes against our ADRs, conventions and rollout rules.",
     ["engineering", "general"], ["search_docs", "ontology_lookup"],
     ["Does a change to session storage need to follow ADR-007?", "What has to be true before I merge a user-facing change?"],
     "Verdict first (ship / fix first / needs design), then the specific ADR or rule that applies, then the change you'd make."),
    ("developer", "oncall_copilot", "On-call Copilot", "Runbooks, rotations, incident history and escalation for whoever holds the pager.",
     ["engineering", "general"], ["search_docs", "ontology_lookup"],
     ["Who is primary and secondary on-call the week of Oct 20?", "What happened in the August 14 ingest outage and what are the follow-ups?"],
     "Lead with what to do right now (numbered steps), then who to page, then the relevant runbook section."),
    ("developer", "deploy_guide", "Deploy Guide", "Production deploys, canaries, rollback limits and feature flags.",
     ["engineering"], ["search_docs"],
     ["At what error rate or latency should I roll back a canary?", "Can I deploy on a Friday afternoon?"],
     "Give the exact thresholds and commands as a short checklist; flag anything that blocks the deploy."),
    ("developer", "architecture_explainer", "Architecture Explainer", "How Pulse fits together: systems, owners, dependencies and decisions.",
     ["engineering", "general"], ["search_docs", "ontology_lookup"],
     ["Which team owns the ingest gateway and what does it depend on?", "Why did we move to Kafka for ingest?"],
     "Explain in three layers: one-sentence summary, the components involved (with owning team), then the decision record behind it."),
    ("developer", "api_spec_helper", "API Spec Helper", "Public API contracts, rate limits and headers.",
     ["engineering"], ["search_docs"],
     ["What are the public API rate limits per plan?", "Which headers tell a client it is being rate limited?"],
     "Answer with the exact numbers and header names in a small table, then one example."),
    # ---------------- product manager
    ("product_manager", "prd_writer", "PRD Writer", "Drafts and critiques PRDs grounded in our roadmap, research and constraints.",
     ["engineering", "general", "sales"], ["search_docs", "ontology_lookup"],
     ["What is the plan and scope for Project Nimbus?", "What are the requirements for SSO/SAML?"],
     "Use PRD sections: problem, users, scope (in/out), success metrics, risks, open questions. Mark anything not in the sources as an assumption."),
    ("product_manager", "roadmap_planner", "Roadmap Planner", "Q4 roadmap, sequencing and how work ladders up to OKRs.",
     ["engineering", "general"], ["search_docs"],
     ["What is on the Q4 roadmap and which OKR does each item support?", "When is SSO/SAML planned to go GA?"],
     "Lead with a dated list (item, quarter, owner team, linked OKR), then dependencies and risks."),
    ("product_manager", "customer_voice", "Customer Voice", "What customers and research say: interviews, renewal signals and top accounts.",
     ["sales", "engineering", "general"], ["search_docs", "ontology_lookup"],
     ["What did research find about how on-call engineers use Pulse?", "What are our top accounts asking for?"],
     "Group findings into themes with evidence (quote or account), then the product implication of each."),
    ("product_manager", "launch_checklist", "Launch Checklist", "Launch readiness: flags, release process, enablement and messaging.",
     ["engineering", "sales", "general"], ["search_docs"],
     ["What has to happen before a feature launches behind a flag?", "What is the Q4 marketing plan for launches?"],
     "A checklist with owner and status columns; call out blockers first."),
    ("product_manager", "metrics_analyst", "Metrics Analyst", "Product and company metrics, OKR key results and how to read them.",
     ["general", "engineering", "sales"], ["search_docs"],
     ["What are the company OKRs and key results this half?", "What does the Q3 pipeline look like by stage?"],
     "Show the metric, current value, target and gap; say plainly whether it is on track."),
    # ---------------- director
    ("director", "team_pulse", "Team Pulse", "Health of the teams you lead: people notes, load and risks (only teams you manage).",
     ["hr", "engineering", "general"], ["search_docs", "ontology_lookup"],
     ["How is my team doing and who might be at risk of burnout?", "Who on my team is ready for promotion?"],
     "One line per person or theme, then the two actions you'd take this week. Never speculate beyond the notes."),
    ("director", "incident_review", "Incident Review", "Postmortems, action items and whether follow-ups are actually closing.",
     ["engineering", "general"], ["search_docs", "ontology_lookup"],
     ["Which August outage action items are still open, and who owns them?", "What were the root causes of the August 14 outage?"],
     "Table of action items (item, owner, status, due), then the one systemic fix that matters most."),
    ("director", "capacity_planner", "Capacity Planner", "Headcount, load and hiring against the roadmap for your teams.",
     ["hr", "engineering", "general"], ["search_docs"],
     ["Which of my teams are over capacity this quarter?", "Where should the next hires go?"],
     "Per team: capacity %, what is driving it, and the trade-off you recommend."),
    ("director", "okr_tracker", "OKR Tracker", "Tracks objectives and key results, owners and confidence.",
     ["general", "engineering"], ["search_docs", "ontology_lookup"],
     ["Are we on track for the reliability OKR?", "What are the key results for enterprise readiness?"],
     "Objective, then each KR with status (on track / at risk / off track) and the evidence."),
    ("director", "delivery_risk", "Delivery Risk Radar", "Cross-checks roadmap commitments against incidents, capacity and dependencies.",
     ["engineering", "general", "hr"], ["search_docs", "ontology_lookup"],
     ["Which Q4 roadmap items are at risk and why?", "Does the August outage follow-up work threaten any Q4 dates?"],
     "Ranked list of risks (item, why, likelihood, mitigation), most severe first."),
    # ---------------- cto
    ("cto", "tech_strategy_brief", "Tech Strategy Brief", "Architecture direction, major decisions and where the platform is heading.",
     ["engineering", "general", "board"], ["search_docs", "ontology_lookup"],
     ["Summarise our major architecture decisions this year and why we made them.", "What are the biggest technical risks to the roadmap?"],
     "Executive brief: three headline points, then decisions, risks and the asks for the exec team."),
    ("cto", "board_tech_brief", "Board Tech Brief", "Prepares the technology section for board meetings from board-level material.",
     ["board", "engineering", "general"], ["search_docs"],
     ["Prepare the technology update for the next board meeting.", "What technical diligence is in progress and when does it finish?"],
     "Board style: one-paragraph summary, three numbered points with figures, then risks and next steps."),
    ("cto", "reliability_scorecard", "Reliability Scorecard", "Availability, incidents, SLA exposure and the reliability OKR.",
     ["engineering", "general", "sales"], ["search_docs", "ontology_lookup"],
     ["How reliable were we this quarter and which customers were affected?", "What does our SLA commit us to?"],
     "Scorecard: metric, target, actual, trend; then customer impact and the top fix."),
    ("cto", "build_vs_buy", "Build vs Buy Advisor", "Vendor spend, contracts and make-or-buy decisions for infrastructure.",
     ["finance", "engineering", "board", "general"], ["search_docs", "ontology_lookup"],
     ["What do we spend on managed Kafka and when does it renew?", "Should we build or buy log search?"],
     "Options table (build / buy / partner) with cost, time, risk; then a recommendation and what would change it."),
    ("cto", "security_posture", "Security Posture", "Security incident response, compliance (SOC 2) and access controls.",
     ["engineering", "general"], ["search_docs"],
     ["Where are we on SOC 2 Type II?", "What is our process when a security incident is declared?"],
     "Status line first (green / amber / red), then gaps, owners and dates."),
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
    role, aid, name, desc, focus, tools, starters, output = spec
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
        "## Operating context",
        f"- The team's rhythm: {rnd.choice(CADENCE)}.",
        "- Current priorities to keep in mind:", *[f"  - {p}" for p in pr],
        "- Recent context:", *[f"  - {e}" for e in ev], "",
        "## How you work", *[f"- {x}" for x in st], "",
        "## House rules",
        "- Only use the provided sources; they are already filtered to what this person may see.",
        *[f"- {r}" for r in ru], "",
        "## Output format", output, "",
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
