"""Local embeddings and reranking. No hosted service is required.

Embedders (``OA_EMBEDDER``):
  ollama   a local embedding model served by Ollama (default: BGE-base-en-v1.5)
  hash     deterministic hashed bag-of-words; no model, used by the test suite

``auto`` (the default) uses Ollama when it answers, otherwise falls back to
hash. The embedder in use is recorded in every experiment report, because
retrieval results depend on it.

Reranking is local too: a reciprocal-rank fusion of dense similarity and
BM25 (see ``lexical.py``). It is the knob the harness can learn to turn on.
"""

from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache

import httpx

from .config import settings

_WORD = re.compile(r"[a-z0-9]+")


def _hash_embed(text: str) -> list[float]:
    vec = [0.0] * settings.embed_dim
    for tok in _WORD.findall(text.lower()):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[h % settings.embed_dim] += 1.0 if (h >> 12) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


@lru_cache(maxsize=1)
def backend() -> str:
    """The embedder actually in use: 'ollama' or 'hash'."""
    want = settings.embedder
    if want == "hash":
        return "hash"
    try:
        r = httpx.post(f"{settings.ollama_url}/api/embed", json={"model": settings.embed_model, "input": ["ping"]},
                       timeout=10)
        r.raise_for_status()
        if r.json().get("embeddings"):
            return "ollama"
    except (httpx.HTTPError, ValueError):
        pass
    if want == "ollama":
        raise RuntimeError(f"Ollama embedding model '{settings.embed_model}' is not reachable at {settings.ollama_url}")
    return "hash"


def available() -> bool:
    """True when a real (semantic) embedding model is in use."""
    return backend() != "hash"


def describe() -> str:
    return f"ollama:{settings.embed_model}" if backend() == "ollama" else f"hash:{settings.embed_dim}"


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embed(texts: list[str], kind: str = "document") -> list[list[float]]:
    if backend() == "hash":
        return [_hash_embed(t) for t in texts]
    from . import usage

    usage.bump("embed_calls")
    usage.bump("texts_embedded", len(texts))
    prefix = settings.query_prefix if kind == "query" else ""
    out: list[list[float]] = []
    for i in range(0, len(texts), 32):
        batch = [prefix + t for t in texts[i: i + 32]]
        r = httpx.post(f"{settings.ollama_url}/api/embed", json={"model": settings.embed_model, "input": batch},
                       timeout=120)
        r.raise_for_status()
        out.extend(_normalize(v) for v in r.json()["embeddings"])
    return out


@lru_cache(maxsize=4096)
def _embed_one_cached(text: str, kind: str) -> tuple[float, ...]:
    return tuple(embed([text], kind)[0])


def embed_one(text: str, kind: str = "query") -> list[float]:
    return list(_embed_one_cached(text, kind))


def dim() -> int:
    return len(embed_one("dimension probe", "document"))


def rerank(query: str, docs: list[str], top_k: int) -> list[tuple[int, float]]:
    """(index, score) pairs, best first: reciprocal-rank fusion of dense cosine and BM25."""
    if not docs:
        return []
    from .lexical import BM25

    q = embed_one(query)
    dense = [sum(a * b for a, b in zip(q, d)) for d in embed(docs)]
    lex = BM25(docs).scores(query)
    rank_d = {i: r for r, i in enumerate(sorted(range(len(docs)), key=lambda i: -dense[i]))}
    rank_l = {i: r for r, i in enumerate(sorted(range(len(docs)), key=lambda i: -lex[i]))}
    fused = [(i, 1 / (60 + rank_d[i]) + 1 / (60 + rank_l[i])) for i in range(len(docs))]
    fused.sort(key=lambda x: -x[1])
    return fused[:top_k]
