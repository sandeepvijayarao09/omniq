"""Calibrate the four similarity thresholds for the embedder in use.

The system uses cosine similarity in four places, each needing a threshold that
depends on the embedding model (different models put scores on different scales):

  rephrase     consecutive questions this similar mean the user is re-asking (query vs query)
  theme        recommendations this similar are grouped into one theme (document vs document)
  duplicate    preferences this similar are merged, newest wins (document vs document)
  project_doc  a project passage this similar to the question is pulled into context (query vs document)

For each, a small labelled set of positive and negative pairs is embedded, and
the threshold that maximizes balanced accuracy is chosen (ties: the midpoint of
the gap). Results, distributions and the chosen thresholds are written to
research/results/calibration-<embedder>.json and picked up by omniq/calibration.py.

    .venv/bin/python research/calibrate_thresholds.py
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from omniq import embed  # noqa: E402

REPHRASE_POS = [
    ("Who is on call this week?", "Which engineer is on call right now?"),
    ("How do I roll back a bad deploy?", "What's the rollback procedure for a broken release?"),
    ("What is our parental leave policy?", "How much parental leave do we get?"),
    ("Why did we move sessions to Postgres?", "What was the reason for moving session storage off Redis?"),
    ("What's the status of the Kafka migration?", "How far along is the move to Kafka?"),
    ("Which accounts are at risk this quarter?", "Which customers might churn this quarter?"),
    ("What discount can I approve on my own?", "How big a discount am I allowed to give without approval?"),
    ("What happened in the August outage?", "Can you explain the August incident?"),
    ("Who leads the security team?", "Who is the head of security?"),
    ("When does SSO ship?", "What's the release date for SSO/SAML?"),
    ("What are the API rate limits for Business?", "How many requests per minute does the Business plan allow?"),
    ("How do I request access to a new app?", "What's the process to get access to an application?"),
    ("Summarize the Q3 OpEx variance", "Why was Q3 operating expense over budget?"),
    ("What did the board decide about Bluefin?", "What's the board's decision on Project Bluefin?"),
    ("Who owns the ingest gateway?", "Which team is responsible for the ingest gateway?"),
    ("What's in the Q4 roadmap?", "What are we shipping in Q4?"),
]
REPHRASE_NEG = [
    ("Who is on call this week?", "What is our parental leave policy?"),
    ("How do I roll back a bad deploy?", "Which accounts are at risk this quarter?"),
    ("What's the status of the Kafka migration?", "What discount can I approve on my own?"),
    ("Who leads the security team?", "When does SSO ship?"),
    ("What are the API rate limits for Business?", "What did the board decide about Bluefin?"),
    ("Summarize the Q3 OpEx variance", "How do I request access to a new app?"),
    ("Who owns the ingest gateway?", "What's the hotel limit when traveling?"),
    ("What happened in the August outage?", "How much is the home office stipend?"),
    ("Why did we move sessions to Postgres?", "Who is the champion at Pinecrest Bank?"),
    ("What's in the Q4 roadmap?", "How do I report a phishing email?"),
    # same topic, different question: must NOT count as a rephrase
    ("Who is on call this week?", "Who is on call next week?"),
    ("How do I roll back a bad deploy?", "Write the PR description for the rollback fix"),
    ("What happened in the August outage?", "Now draft the customer email about the outage"),
    ("Which accounts are at risk this quarter?", "Add the renewal dates to that table"),
    ("What's the status of the Kafka migration?", "Who reviewed ADR-012?"),
    ("Who owns the ingest gateway?", "What does the ingest gateway depend on?"),
]
THEME_POS = [
    ("Keep answers shorter", "Make the reply more concise"),
    ("Always list the owners", "Include who owns each item"),
    ("Add the rollback command", "Show the exact command to roll back"),
    ("Put risks first", "Lead with the biggest risks"),
    ("Use a table for the deals", "Format the deals as a table"),
    ("Cite the ADR for every decision", "Reference the relevant ADR each time"),
    ("Don't include customer names", "Leave out the names of customers"),
    ("End with next steps", "Finish with what to do next"),
    ("Give dates for every milestone", "Include the date of each milestone"),
    ("Mention the SLA clock", "State how much SLA time is left"),
]
THEME_NEG = [
    ("Keep answers shorter", "Always list the owners"),
    ("Add the rollback command", "Use a table for the deals"),
    ("Put risks first", "Give dates for every milestone"),
    ("Cite the ADR for every decision", "End with next steps"),
    ("Don't include customer names", "Mention the SLA clock"),
    ("Keep answers shorter", "Give more detail on the root cause"),
    ("Use a table for the deals", "Use prose, not bullet points"),
    ("Always list the owners", "Include the escalation path"),
    ("Lead with the biggest risks", "Lead with the good news"),
    ("End with next steps", "Start with a one-line summary"),
]
DUP_POS = [
    ("I prefer bullet points", "I prefer bullets"),
    ("Keep it under 150 words", "Keep it under 150 words, please"),
    ("Always include the rollback command", "Always include the rollback command."),
    ("Use British spelling", "Please use British spelling"),
    ("Sign emails as Maya", "Sign my emails as Maya"),
    ("Lead with the risks", "Lead with risks"),
    ("No emoji", "Don't use emoji"),
    ("Show amounts in USD", "Show all amounts in USD"),
]
DUP_NEG = [
    ("I prefer bullet points", "I prefer prose paragraphs"),
    ("Keep it under 150 words", "Keep it under 400 words"),
    ("Always include the rollback command", "Always include the deploy command"),
    ("Use British spelling", "Use American spelling"),
    ("Lead with the risks", "Lead with the wins"),
    ("Sign emails as Maya", "Sign emails as the Platform team"),
    ("Show amounts in USD", "Show amounts in EUR"),
    ("No emoji", "No tables"),
    ("Keep answers shorter", "Always list the owners"),
    ("Put risks first", "Cite the ADR for every decision"),
]
PROJECT_POS = [
    ("What's left before SSO ships?", "SSO/SAML is in private beta with six customers and ships to all Business and Enterprise plans in December."),
    ("Where are we on the Kafka migration?", "The Kafka migration (ADR-012) is 70% complete; the last tenants move in November."),
    ("What does ADR-007 change?", "Store sessions in the primary Postgres cluster with Redis as a read-through cache."),
    ("What did research find about on-call engineers?", "Mobile is triage, desktop is investigation: 11 of 14 acknowledge on mobile, then move to a laptop."),
    ("What's the plan for anomaly detection noise?", "Noise rate down from 38% to 11% in the anomaly detection v2 beta."),
    ("Who is the champion at Globex?", "Globex Financial — Enterprise — champion Rachel Torres, at risk after the August outage."),
]
PROJECT_NEG = [
    ("What's left before SSO ships?", "Full-time employees receive 25 days of paid time off per calendar year."),
    ("Where are we on the Kafka migration?", "Hotels up to $300 per night in major cities; meals up to $75 per day."),
    ("What does ADR-007 change?", "Guest Wi-Fi for visitors: network Halcyon-Guest."),
    ("What did research find about on-call engineers?", "The corporate card program uses one card for marketplace purchases."),
    ("What's the plan for anomaly detection noise?", "Promotions happen in the January and July cycles."),
    ("Who is the champion at Globex?", "Report phishing with the Report button in your email client."),
]

TASKS = {
    "rephrase": (REPHRASE_POS, REPHRASE_NEG, "query", "query"),
    "theme": (THEME_POS, THEME_NEG, "document", "document"),
    "duplicate": (DUP_POS, DUP_NEG, "document", "document"),
    "project_doc": (PROJECT_POS, PROJECT_NEG, "query", "document"),
}


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _best_threshold(pos: list[float], neg: list[float]) -> tuple[float, float]:
    cands = sorted(set(pos + neg))
    best_t, best_acc = 0.5, -1.0
    for i, t in enumerate(cands):
        tpr = sum(p >= t for p in pos) / len(pos)
        tnr = sum(n < t for n in neg) / len(neg)
        acc = (tpr + tnr) / 2
        if acc > best_acc + 1e-9:
            # midpoint between this candidate and the next lower score, so the threshold sits in the gap
            lower = max([x for x in cands[:i]], default=t)
            best_t, best_acc = (t + lower) / 2 if lower < t else t, acc
    return round(best_t, 4), round(best_acc, 4)


def calibrate() -> dict:
    out = {"embedder": embed.describe(), "tasks": {}}
    for name, (pos_pairs, neg_pairs, ka, kb) in TASKS.items():
        def sims(pairs: list[tuple[str, str]]) -> list[float]:
            return [_cos(embed.embed_one(a, ka), embed.embed_one(b, kb)) for a, b in pairs]

        pos, neg = sims(pos_pairs), sims(neg_pairs)
        t, acc = _best_threshold(pos, neg)
        out["tasks"][name] = {
            "threshold": t, "balanced_accuracy": acc, "n_pos": len(pos), "n_neg": len(neg),
            "pos": {"min": round(min(pos), 4), "mean": round(statistics.mean(pos), 4), "max": round(max(pos), 4)},
            "neg": {"min": round(min(neg), 4), "mean": round(statistics.mean(neg), 4), "max": round(max(neg), 4)},
            "errors": {"missed_pos": [p for p, s in zip(pos_pairs, pos) if s < t],
                       "false_pos": [p for p, s in zip(neg_pairs, neg) if s >= t]},
        }
    return out


if __name__ == "__main__":
    res = calibrate()
    slug = re.sub(r"[^a-z0-9]+", "-", res["embedder"].lower()).strip("-")
    path = ROOT / "research" / "results" / f"calibration-{slug}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(res, indent=2))
    for name, r in res["tasks"].items():
        print(f"{name:12} threshold {r['threshold']:.3f}  balanced acc {r['balanced_accuracy']:.2f}  "
              f"pos {r['pos']['min']:.2f}-{r['pos']['max']:.2f}  neg {r['neg']['min']:.2f}-{r['neg']['max']:.2f}")
    print(f"wrote {path.relative_to(ROOT)}")
