"""Tables and paired statistics from research/run.py output, computed from the raw JSONL files only (ARCHITECTURE 7.4).

  .venv/bin/python research/analyze.py [--experiment claim-a] [--split dev] [--baseline B3]

Per condition: answer accuracy (overall and by question type), leak rate (a forbidden value in the answer), exposure
rate (a forbidden value in what the model was shown), abstention recall (unanswerable questions declined) and
precision (declines that were right to decline), mean latency. Against the baseline: the accuracy difference on the
same answer cases with a paired-bootstrap 95% CI, and an exact McNemar test on the discordant pairs. Writes
research/results/<experiment>/summary-<split>.md.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import random
import sys
from collections import defaultdict
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from omniq.evals import ABSTAIN  # noqa: E402


def load(exp_dir: pathlib.Path, split: str) -> dict[str, dict[str, Any]]:
    """{condition: {"env": …, "rows": {case id: row}}}; with several seeds, the last file wins per condition."""
    out: dict[str, dict[str, Any]] = {}
    for f in sorted(exp_dir.glob(f"*-{split}-seed*.jsonl")):
        lines = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        env, rows = lines[0].get("environment", {}), lines[1:]
        out[env.get("condition", f.name.split("-")[0])] = {"env": env, "rows": {r["id"]: r for r in rows}}
    return out


def _rate(xs: list[bool]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def metrics(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rs = list(rows.values())
    ans = [r for r in rs if r["kind"] == "answer"]
    leak = [r for r in rs if r["kind"] == "leak"]
    abst = [r for r in rs if r["kind"] == "abstain"]
    declined = [r for r in rs if ABSTAIN.search(r["answer"] or "")]
    by_type: dict[str, list[bool]] = defaultdict(list)
    for r in ans:
        by_type[r["type"]].append(r["passed"])
    return {
        "n": len(rs), "accuracy": _rate([r["passed"] for r in ans]), "n_answer": len(ans),
        "leak_rate": _rate([bool(r["leaked"]) for r in rs if r["must_not_include"]]),
        "exposure_rate": _rate([bool(r["exposed"]) for r in leak]),
        "abstain_recall": _rate([r["passed"] for r in abst]),
        "abstain_precision": _rate([r["kind"] != "answer" for r in declined]),
        "latency": sum(r["latency"] or 0 for r in rs) / len(rs) if rs else None,
        "by_type": {t: _rate(v) for t, v in sorted(by_type.items())},
    }


def paired_bootstrap(a: list[bool], b: list[bool], iters: int = 5000, seed: int = 7) -> tuple[float, float, float]:
    """Mean of (b - a) over paired cases and its 95% percentile CI."""
    rng = random.Random(seed)
    d = [int(y) - int(x) for x, y in zip(a, b)]
    if not d:
        return 0.0, 0.0, 0.0
    means = sorted(sum(rng.choice(d) for _ in d) / len(d) for _ in range(iters))
    return sum(d) / len(d), means[int(0.025 * iters)], means[int(0.975 * iters) - 1]


def mcnemar_exact(a: list[bool], b: list[bool]) -> float:
    """Two-sided exact McNemar p-value from the discordant pairs."""
    n01 = sum(1 for x, y in zip(a, b) if not x and y)
    n10 = sum(1 for x, y in zip(a, b) if x and not y)
    n = n01 + n10
    if n == 0:
        return 1.0
    k = min(n01, n10)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{100 * x:.1f}%"


def report(data: dict[str, dict[str, Any]], baseline: str, split: str) -> str:
    lines = [f"# Results · split `{split}`", "",
             "| Condition | n | Accuracy (answer) | Leak rate | Exposure | Abstain recall | Abstain precision | Latency |",
             "|---|---|---|---|---|---|---|---|"]
    order = sorted(data, key=lambda c: (c[0] != "B", c))
    ms = {c: metrics(data[c]["rows"]) for c in order}
    for c in order:
        m = ms[c]
        lines.append(f"| {c} | {m['n']} | {_pct(m['accuracy'])} | {_pct(m['leak_rate'])} | {_pct(m['exposure_rate'])} | "
                     f"{_pct(m['abstain_recall'])} | {_pct(m['abstain_precision'])} | {m['latency']:.1f}s |")
    types = sorted({t for m in ms.values() for t in m["by_type"]})
    lines += ["", "**Accuracy by question type**", "", "| Condition | " + " | ".join(types) + " |",
              "|---|" + "---|" * len(types)]
    for c in order:
        lines.append(f"| {c} | " + " | ".join(_pct(ms[c]["by_type"].get(t)) for t in types) + " |")
    if baseline in data:
        lines += ["", f"**Paired against {baseline}** (answer cases both conditions ran)", "",
                  "| Condition | pairs | Δ accuracy | 95% CI | McNemar p |", "|---|---|---|---|---|"]
        base = data[baseline]["rows"]
        for c in order:
            if c == baseline:
                continue
            ids = [i for i, r in data[c]["rows"].items() if r["kind"] == "answer" and i in base]
            a = [base[i]["passed"] for i in ids]
            b = [data[c]["rows"][i]["passed"] for i in ids]
            d, lo, hi = paired_bootstrap(a, b)
            lines.append(f"| {c} | {len(ids)} | {100 * d:+.1f} pts | [{100 * lo:+.1f}, {100 * hi:+.1f}] | {mcnemar_exact(a, b):.3g} |")
    envs = {c: data[c]["env"] for c in order}
    lines += ["", "**Environments**", "", "| Condition | git | model | embedder | environment | benchmark |",
              "|---|---|---|---|---|---|"]
    for c, e in envs.items():
        lines.append(f"| {c} | {e.get('git')} | {e.get('model')} | {e.get('embedder')} | {e.get('environment')} | {e.get('benchmark_sha')} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", default="claim-a")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--baseline", default="B3")
    args = ap.parse_args()
    exp_dir = ROOT / "research" / "results" / args.experiment
    data = load(exp_dir, args.split)
    if not data:
        print(f"no results in {exp_dir} for split {args.split}")
        return 1
    text = report(data, args.baseline, args.split)
    (exp_dir / f"summary-{args.split}.md").write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
