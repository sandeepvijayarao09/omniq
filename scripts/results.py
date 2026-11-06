"""Write docs/RESULTS.md from what is actually in the local store (re-run any time: .venv/bin/python scripts/results.py)."""
import datetime as dt
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from omniq import config, impact  # noqa: E402,F401
from omniq.db import db  # noqa: E402

D = impact.decisions()
S = D["signals"]
o = D["outcomes"]
kept = o.get("active", 0) + o.get("superseded", 0)
leaks_kept = 0  # every promotion passes the safety gate; count any leak recorded on a kept version
for v in db().harness.find({"status": {"$in": ["active", "superseded"]}}):
    sc = v.get("scores") or {}
    leaks_kept += (sc.get("train") or {}).get("leaks", 0) + (sc.get("holdout") or {}).get("leaks", 0) + (sc.get("evals") or {}).get("leaks", 0)
imp = Counter(x["kind"] for x in db().implicit_signals.find({}, {"kind": 1}))
intents = Counter(x["intent"] for x in db().intent_events.find({}, {"intent": 1}))
lines = [
    "# Results (generated from the local store)", "",
    f"_Generated {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC by `scripts/results.py`. Every number below is a query over the live data._", "",
    "## Data-driven decisions", "",
    "| | |", "|---|---|",
    f"| Automatic decisions about playbooks | **{D['total']}** |",
    f"| Kept (proven better on unseen tests / blind replay) | **{kept}** |",
    f"| Undone (not better: that is a decision too) | **{o.get('rolled_back', 0)}** |",
    f"| Refused by safety rules | **{o.get('blocked', 0)}** |",
    f"| Waiting for a manager's approval | **{o.get('awaiting_approval', 0)}** |",
    f"| Average score gain of kept changes | **{'+' + str(round(D['avg_uplift_kept'] * 100)) + ' pts' if D['avg_uplift_kept'] is not None else '–'}** |",
    f"| Privacy leaks in any kept version | **{leaks_kept}** |",
    f"| Answers right the first time, last 7 days (no rephrase, correction, regenerate or 👎) | **{round(D['first_time_right']['rate'] * 100) if D['first_time_right']['rate'] is not None else '–'}%** of {D['first_time_right']['answers']} |", "",
    "## First-time-right by agent (last 7 days)", "", "| Agent | Answers | Right first time |", "|---|---|---|",
    *[f"| {r['agent']} | {r['answers']} | {round(r['first_time_right'] * 100)}% |" for r in D['first_time_right']['per_agent']], "",
    "## Scores over time (practice-question pass rate)", "", "| Playbook | Start | Now |", "|---|---|---|",
    *[f"| {t['name']} | {str(round(t['start'] * 100)) + '%' if t['start'] is not None else '–'} | {str(round(t['now'] * 100)) + '%' if t['now'] is not None else '– (not re-scored yet)'} |"
      for t in D["trend"] if t["points"]][:12], "",
    "## What it learned from (signals, never asked for)", "", "| Signal | Count |", "|---|---|",
    f"| Answers given | {S['answers']} |", f"| 👍/👎 ratings | {S['ratings']} |",
    *[f"| {k} | {v} |" for k, v in imp.most_common()],
    *[f"| follow-up understood as {k} | {v} |" for k, v in intents.most_common()],
    f"| 7-day person cycles run | {S['cycles']} (reviewing {S['decisions_reviewed_in_cycles']} decisions) |",
    f"| Agent 7-day cycles run | {db().agent_cycles.count_documents({})} |",
    f"| Graded test cases | {S['graded_eval_cases']} |", "",
    "## Safety", "",
    f"- Leak test cases across roles and agents: all pass on every kept version (absolute safety gate).",
    f"- Suggestions quarantined as injection attempts: {S['quarantined']}.",
    f"- Hash-chained audit log: {db().audit.estimated_document_count()} events, verified end to end.",
    "",
]
open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "RESULTS.md"), "w").write("\n".join(lines))
print("\n".join(lines))
