"""Similarity thresholds, calibrated per embedder (see research/calibrate_thresholds.py).

Different embedding models put cosine scores on different scales, so each
threshold is measured on labelled pairs for the embedder in use and read from
research/results/calibration-<embedder>.json. If no calibration file exists for
the current embedder, the BGE-base values below are used.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from .config import ROOT

DEFAULTS = {"rephrase": 0.675, "theme": 0.733, "duplicate": 0.884, "project_doc": 0.375}  # bge-base-en-v1.5


@lru_cache(maxsize=4)
def _load(embedder: str) -> dict[str, float]:
    slug = re.sub(r"[^a-z0-9]+", "-", embedder.lower()).strip("-")
    path = ROOT / "research" / "results" / f"calibration-{slug}.json"
    if not path.exists():
        return dict(DEFAULTS)
    tasks = json.loads(path.read_text()).get("tasks", {})
    return {k: float(tasks.get(k, {}).get("threshold", v)) for k, v in DEFAULTS.items()}


def threshold(name: str) -> float:
    from . import embed

    return _load(embed.describe())[name]
