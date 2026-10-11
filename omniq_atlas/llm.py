"""Model calls: Claude if a key is set, else any OpenAI-compatible endpoint (Ollama by default),
else an extractive offline fallback so the pipeline always runs."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

import httpx

from .config import settings


@lru_cache(maxsize=1)
def _client():
    if not settings.anthropic_key:
        return None
    import anthropic

    return anthropic.Anthropic(api_key=settings.anthropic_key)


@lru_cache(maxsize=1)
def _compat_up() -> bool:
    if not settings.llm_base_url:
        return False
    try:
        httpx.get(settings.llm_base_url.rstrip("/") + "/models", timeout=3,
                  headers={"Authorization": f"Bearer {settings.llm_api_key}"}).raise_for_status()
        return True
    except Exception:
        return False


def provider() -> str:
    return "anthropic" if _client() else "openai_compatible" if _compat_up() else "offline"


def available() -> bool:
    return provider() != "offline"


def _ollama(system: str, user: str, model: str, max_tokens: int, temperature: float,
            json_mode: bool) -> tuple[str, dict[str, Any]]:
    """Ollama's native API, so reasoning models can be told not to think (it eats the token budget)."""
    base = settings.llm_base_url.rstrip("/").removesuffix("/v1")
    body: dict[str, Any] = {"model": model, "stream": False, "think": False,
                            "options": {"num_predict": max_tokens, "temperature": temperature, "num_ctx": 16384, "seed": 7},
                            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if json_mode:
        body["format"] = "json"
    r = httpx.post(base + "/api/chat", json=body, timeout=300)
    r.raise_for_status()
    d = r.json()
    return d["message"].get("content", "").strip(), {"model": d.get("model", model),
                                                       "input_tokens": d.get("prompt_eval_count", 0),
                                                       "output_tokens": d.get("eval_count", 0)}


def _compat(system: str, user: str, model: str, max_tokens: int, temperature: float,
            json_mode: bool = False) -> tuple[str, dict[str, Any]]:
    if ":11434" in settings.llm_base_url:
        return _ollama(system, user, model, max_tokens, temperature, json_mode)
    r = httpx.post(settings.llm_base_url.rstrip("/") + "/chat/completions", timeout=180,
                   headers={"Authorization": f"Bearer {settings.llm_api_key}"},
                   json={"model": model, "max_tokens": max_tokens, "temperature": temperature,
                         "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    r.raise_for_status()
    d = r.json()
    text = d["choices"][0]["message"].get("content") or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    u = d.get("usage", {})
    return text, {"model": d.get("model", model), "input_tokens": u.get("prompt_tokens", 0),
                  "output_tokens": u.get("completion_tokens", 0)}


def complete(system: str, user: str, *, model: str | None = None, max_tokens: int = 500,
             temperature: float = 0.2, json_mode: bool = False) -> tuple[str, dict[str, Any]]:
    from . import usage

    usage.bump("llm_calls")
    c = _client()
    if c is None:
        if _compat_up():
            return _compat(system, user, model or settings.assistant_model, max_tokens, temperature, json_mode)
        return _offline(user), {"model": "offline", "input_tokens": 0, "output_tokens": 0}
    msg = c.messages.create(model=model or settings.assistant_model, max_tokens=max_tokens,
                            temperature=temperature, system=system,
                            messages=[{"role": "user", "content": user}])
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    return text, {"model": msg.model, "input_tokens": msg.usage.input_tokens,
                  "output_tokens": msg.usage.output_tokens}


def complete_json(system: str, user: str, *, model: str | None = None, max_tokens: int = 2500) -> dict[str, Any]:
    text, _ = complete(system + "\nRespond with a single JSON object and nothing else.", user,
                       model=model, max_tokens=max_tokens, temperature=0.0, json_mode=True)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"model did not return JSON: {text[:200]}")
    return json.loads(m.group(0))


def _offline(prompt: str) -> str:
    """Extractive answer from the retrieved passages; enough to exercise retrieval scoring without a model."""
    passages = [p.strip() for p in re.findall(r"<source[^>]*>(.*?)</source>", prompt, re.S)]
    return "\n\n".join(passages)[:4000] if passages else "I couldn't find that in the sources you can access."
