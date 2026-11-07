"""Run one research condition over an OrgQA split and write one JSONL row per case (ARCHITECTURE 7.2–7.5).

  .venv/bin/python research/run.py --condition O2 --split dev --seed 1 [--limit 40] [--experiment claim-a]

Every row holds what's needed to recompute every number later, by research/analyze.py, from the raw files only:
the inputs, the answer, the grade, the sources and context the model saw, latency and tokens, and an environment
block (git SHA, embedder, model, condition config, and harness.environment(): answer-path code + knowledge base +
cases). Nothing is hand-edited; results live under research/results/<experiment>/.

Conditions (Claim A). All start from the same plain default harness, so only the listed difference changes:
  B0  closed book: the model answers from the question alone
  B2  dense retrieval with the ACL as a pre-filter
  B3  B2 + BM25 hybrid
  O1  B3 + ontology facts with property- and link-level ACL off (object-level only)
  O2  B3 + ontology facts with the full three-level ACL
  O3  O2 + the structured-data engine for database questions
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import pathlib
import subprocess
import sys
import time
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from omniq import assistant, embed, evals, harness, llm  # noqa: E402

BENCH = ROOT / "data" / "benchmark" / "orgqa.yaml"


def condition_config(name: str) -> dict[str, Any] | None:
    """The harness each condition runs with. None means closed book (no retrieval at all)."""
    if name == "B0":
        return None
    cfg = copy.deepcopy(harness.DEFAULT_HARNESS)
    cfg["retrieval"]["top_k"] = 4  # one fixed retrieval budget for every condition, so only the listed change differs
    if name in ("B3", "O1", "O2", "O3"):
        cfg["retrieval"]["hybrid"] = True
    if name in ("O1", "O2", "O3"):
        cfg["retrieval"]["graph_context"] = True
    if name == "O1":
        cfg["ontology"].update(property_acl=False, link_acl=False)
    if name == "O3":
        cfg["tools"] = ["search_docs", "structured_query"]
    if name not in ("B2", "B3", "O1", "O2", "O3"):
        raise SystemExit(f"unknown condition {name}")
    return cfg


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def environment(condition: str, cfg: dict[str, Any] | None, seed: int) -> dict[str, Any]:
    return {"git": _git_sha(), "embedder": embed.describe(), "model": llm.default_profile(), "seed": seed,
            "condition": condition, "config_sha": hashlib.sha1(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12],
            "environment": harness.environment(), "benchmark_sha": hashlib.sha1(BENCH.read_bytes()).hexdigest()[:12],
            "started": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def closed_book(case: dict[str, Any]) -> dict[str, Any]:
    t0 = time.time()
    answer, meta = llm.complete(assistant.BASE_SYSTEM, f"Question: {case['question']}", max_tokens=400, temperature=0.0)
    return {"answer": answer, "sources": [], "_context": "", "latency": round(time.time() - t0, 2),
            "tokens": meta.get("input_tokens", 0) + meta.get("output_tokens", 0)}


def run_case(case: dict[str, Any], cfg: dict[str, Any] | None) -> dict[str, Any]:
    if cfg is None:
        res = closed_book(case)
    else:
        res = assistant.ask(case["persona"], case["question"], cfg_override=cfg, source="eval", personal_memory=False)
    g = evals.grade(case, res["answer"])
    context = res.get("_context", "")
    # Exposure: did a value this persona may not see reach the model at all, even if the answer didn't use it?
    exposed = [s for s in case.get("must_not_include", []) if s.lower() in context.lower()]
    return {"id": case["id"], "type": case["type"], "kind": case["kind"], "persona": case["persona"],
            "question": case["question"], "must_include": case.get("must_include", []),
            "must_not_include": case.get("must_not_include", []), "answer": res["answer"],
            "passed": g["passed"], "score": g["score"], "leaked": g["leaked"], "exposed": exposed,
            "sources": res.get("sources", []), "structured": (res.get("_structured") or {}).get("status"),
            "latency": res.get("latency"), "tokens": res.get("tokens")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", required=True)
    ap.add_argument("--split", default="dev", choices=["train", "dev", "test"])
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0, help="first N cases of the split (0 = all)")
    ap.add_argument("--experiment", default="claim-a")
    args = ap.parse_args()

    cases = [c for c in yaml.safe_load(BENCH.read_text())["cases"] if c["split"] == args.split]
    if args.limit:
        cases = cases[: args.limit]
    cfg = condition_config(args.condition)
    out_dir = ROOT / "research" / "results" / args.experiment
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{args.condition}-{args.split}-seed{args.seed}.jsonl"
    env = environment(args.condition, cfg, args.seed)
    with out.open("w") as f:
        f.write(json.dumps({"environment": env}) + "\n")
        for i, case in enumerate(cases, 1):
            row = run_case(case, cfg)
            f.write(json.dumps(row, default=str) + "\n")
            f.flush()
            print(f"[{i}/{len(cases)}] {'PASS' if row['passed'] else 'FAIL'} {row['kind']:<7} {row['id']}", flush=True)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
