"""Voyage AI embeddings and reranking, with a deterministic offline fallback."""

from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache

from .config import settings

_WORD = re.compile(r"[a-z0-9]+")


@lru_cache(maxsize=1)
def _voyage():
    if not settings.voyage_key:
        return None
    import voyageai

    return voyageai.Client(api_key=settings.voyage_key)


def available() -> bool:
    return _voyage() is not None


def _hash_embed(text: str) -> list[float]:
    vec = [0.0] * settings.embed_dim
    for tok in _WORD.findall(text.lower()):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % settings.embed_dim] += 1.0 if (h >> 12) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def embed(texts: list[str], kind: str = "document") -> list[list[float]]:
    vo = _voyage()
    if vo is None:
        return [_hash_embed(t) for t in texts]
    out: list[list[float]] = []
    for i in range(0, len(texts), 64):
        res = vo.embed(texts[i : i + 64], model=settings.embed_model, input_type=kind,
                       output_dimension=settings.embed_dim)
        out.extend(res.embeddings)
    return out


def embed_one(text: str, kind: str = "query") -> list[float]:
    return embed([text], kind)[0]


def rerank(query: str, docs: list[str], top_k: int) -> list[tuple[int, float]]:
    """Return (index, score) pairs, best first. Falls back to input order."""
    vo = _voyage()
    if vo is None or not docs:
        return [(i, 1.0 - i * 0.01) for i in range(min(top_k, len(docs)))]
    res = vo.rerank(query, docs, model=settings.rerank_model, top_k=top_k)
    return [(r.index, r.relevance_score) for r in res.results]
