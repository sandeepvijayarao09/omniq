"""End to end: Omniq agents answering from the company databases through a live Auto Ontology.

Needs `scripts/auto_ontology.sh up` and `scripts/auto_ontology_bootstrap.py` (compiled warehouse) first.
Prints each check and writes the raw records to var/structured_live.jsonl.
"""

from __future__ import annotations

import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from omniq import assistant, structured  # noqa: E402

PM, DIRECTOR, CTO = "emp-1048", "emp-1012", "emp-1009"
OUT = ROOT / "var" / "structured_live.jsonl"

CASES = [
    # (name, user, agent, question, expected status, text the answer must contain or None)
    ("PM: total ARR at risk", PM, "metrics_agent",
     "What is the total ARR of customers whose health is At risk?", "ok", "730"),
    ("PM: accounts renewing soonest", PM, "metrics_agent",
     "Which customer renews soonest, and what is its ARR?", "ok", "Initech"),
    ("CTO via Metrics agent: service credits (finance is outside the agent's focus)", CTO, "metrics_agent",
     "How much do we owe in incident service credits in total?", "withheld", None),
    ("CTO via Vendor agent: service credits", CTO, "vendor_agent",
     "How much do we owe in incident service credits in total?", "ok", "42"),
    ("Director via Status agent: question the data can't answer", DIRECTOR, "status_report_agent",
     "What was our return on advertising spend last quarter?", "unanswerable", None),
]


def main() -> int:
    eng = structured.engine()
    if not eng or not eng.available():
        print("Auto Ontology is not reachable on", getattr(eng, "url", "?"))
        return 2
    passed = 0
    with OUT.open("w") as f:
        for name, uid, agent, q, want, must in CASES:
            t0 = time.time()
            r = assistant.ask(uid, q, agent_id=agent, source="eval")
            s = r.get("_structured") or {}
            shown = (r.get("structured") or {}).get("status")
            # What the person sees must not tell "withheld" from "unanswerable".
            neutral = shown == ("ok" if want == "ok" else "not_answerable")
            ok = neutral and s.get("status") == want and (must is None or must.lower() in r["answer"].lower().replace(",", ""))
            if want == "withheld":  # no figure from the withheld query may reach the answer
                ok = ok and "42000" not in r["answer"].replace(",", "") and "42k" not in r["answer"].lower()
            passed += ok
            print(f"{'PASS' if ok else 'FAIL'} {name}  [{s.get('status')}, coverage={s.get('coverage')}, "
                  f"tables={s.get('tables')}, {time.time() - t0:.0f}s]")
            print("     answer:", r["answer"][:220].replace("\n", " "))
            if s.get("sql"):
                print("     sql:   ", s["sql"][:220].replace("\n", " "))
            if s.get("reason"):
                print("     reason:", s["reason"])
            f.write(json.dumps({"case": name, "user": uid, "agent": agent, "question": q, "expected": want,
                                "ok": ok, "structured": s, "answer": r["answer"]}, default=str) + "\n")
    print(f"\n{passed}/{len(CASES)} passed · raw records in {OUT}")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
