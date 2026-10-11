"""Claude calls, with an extractive offline fallback so the pipeline always runs."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

from .config import settings


@lru_cache(maxsize=1)
def _client():
    if not settings.anthropic_key:
        return None
    import anthropic

    return anthropic.Anthropic(api_key=settings.anthropic_key)


def available() -> bool:
    return _client() is not None


def complete(system: str, user: str, *, model: str | None = None, max_tokens: int = 500,
             temperature: float = 0.2) -> tuple[str, dict[str, Any]]:
    c = _client()
    if c is None:
        return _offline(user), {"model": "offline", "input_tokens": 0, "output_tokens": 0}
    msg = c.messages.create(model=model or settings.assistant_model, max_tokens=max_tokens,
                            temperature=temperature, system=system,
                            messages=[{"role": "user", "content": user}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return text, {"model": msg.model, "input_tokens": msg.usage.input_tokens,
                  "output_tokens": msg.usage.output_tokens}


def complete_json(system: str, user: str, *, model: str | None = None, max_tokens: int = 1200) -> dict[str, Any]:
    text, _ = complete(system + "\nRespond with a single JSON object and nothing else.", user,
                       model=model, max_tokens=max_tokens, temperature=0.0)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"model did not return JSON: {text[:200]}")
    return json.loads(m.group(0))


def _offline(prompt: str) -> str:
    """Extractive answer from the retrieved passages; enough to exercise retrieval scoring without a model."""
    passages = [p.strip() for p in re.findall(r"<source[^>]*>(.*?)</source>", prompt, re.S)]
    return "\n\n".join(passages)[:4000] if passages else "I couldn't find that in the sources you can access."
