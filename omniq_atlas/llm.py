"""Model-agnostic gateway. Every model call in the system goes through a *profile* from config/models.yaml:
answers, the improver (meta-agent), the blind replay judge and the 7-day cycle analyst.

Routing: requested profile (or a model id that matches one) → default → fallback chain → offline.
Data policy: when the context contains private-only categories (board, HR, finance), only `trust: private`
profiles are eligible. Every response records which profile served it and why others were skipped.
"""

from __future__ import annotations

import json
import os
import re
import time
from functools import lru_cache
from typing import Any

import httpx
import yaml

from .config import ROOT, settings

MODELS_FILE = ROOT / "config" / "models.yaml"


@lru_cache(maxsize=1)
def registry() -> dict[str, Any]:
    reg = yaml.safe_load(MODELS_FILE.read_text())
    if settings.anthropic_key:
        reg.setdefault("_default_override", "claude")
    return reg


def _resolve(p: dict[str, Any]) -> dict[str, Any]:
    out = dict(p)
    for k in ("model", "base_url", "api_key"):
        env = p.get(f"{k}_env")
        if env:
            out[k] = os.environ.get(env, "").strip() or out.get(k, "")
    return out


_up: dict[str, tuple[float, bool]] = {}


def is_available(name: str) -> bool:
    if name == "offline":
        return True
    p = registry()["profiles"].get(name)
    if not p:
        return False
    hit = _up.get(name)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    r = _resolve(p)
    ok = False
    try:
        if r["provider"] == "ollama":
            tags = httpx.get(r["base_url"].rstrip("/") + "/api/tags", timeout=2).json()
            ok = any(m.get("name") == r["model"] for m in tags.get("models", []))
        elif r["provider"] == "anthropic":
            ok = bool(r.get("api_key"))
        elif r["provider"] == "openai":
            ok = bool(r.get("api_key") and r.get("model") and r.get("base_url"))
    except Exception:
        ok = False
    _up[name] = (time.time(), ok)
    return ok


def default_profile() -> str:
    reg = registry()
    d = reg.get("_default_override") or reg.get("default", "offline")
    return d if is_available(d) else next((n for n in reg.get("fallback", []) if is_available(n)), "offline")


def profiles() -> list[dict[str, Any]]:
    reg = registry()
    dflt = default_profile()
    return [{"name": n, "label": p.get("label", n), "provider": p["provider"], "model": _resolve(p).get("model"),
             "trust": p.get("trust", "external"), "available": is_available(n), "default": n == dflt}
            for n, p in reg["profiles"].items()]


def _match(model_or_profile: str | None) -> str | None:
    if not model_or_profile:
        return None
    reg = registry()["profiles"]
    if model_or_profile in reg:
        return model_or_profile
    for n, p in reg.items():
        if _resolve(p).get("model") == model_or_profile:
            return n
    return None


def route(requested: str | None = None, private_only: bool = False) -> tuple[list[str], list[str]]:
    """Ordered candidate profiles and the reasons others were skipped."""
    reg = registry()
    order = [x for x in [_match(requested), reg.get("_default_override"), reg.get("default"), *reg.get("fallback", [])] if x]
    seen, cands, skipped = set(), [], []
    for n in order:
        if n in seen:
            continue
        seen.add(n)
        if n == "offline":
            continue
        p = reg["profiles"].get(n, {})
        if private_only and p.get("trust") != "private":
            skipped.append(f"{n}: context holds private-only data")
        elif not is_available(n):
            skipped.append(f"{n}: not available")
        else:
            cands.append(n)
    return cands + ["offline"], skipped


def provider() -> str:
    d = default_profile()
    return "offline" if d == "offline" else registry()["profiles"][d]["provider"]


def available() -> bool:
    return default_profile() != "offline"


@lru_cache(maxsize=4)
def _anthropic(key: str):
    import anthropic

    return anthropic.Anthropic(api_key=key)


def _call(name: str, system: str, user: str, max_tokens: int, temperature: float, json_mode: bool) -> tuple[str, dict[str, Any]]:
    p = _resolve(registry()["profiles"][name])
    if p["provider"] == "ollama":  # native API so reasoning models can be told not to think
        body: dict[str, Any] = {"model": p["model"], "stream": False, "think": False,
                                "options": {"num_predict": max_tokens, "temperature": temperature, "num_ctx": 16384, "seed": 7},
                                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        if json_mode:
            body["format"] = "json"
        d = httpx.post(p["base_url"].rstrip("/") + "/api/chat", json=body, timeout=300).raise_for_status().json()
        return d["message"].get("content", "").strip(), {"model": d.get("model", p["model"]),
                                                          "input_tokens": d.get("prompt_eval_count", 0),
                                                          "output_tokens": d.get("eval_count", 0)}
    if p["provider"] == "anthropic":
        msg = _anthropic(p["api_key"]).messages.create(model=p["model"], max_tokens=max_tokens, temperature=temperature,
                                                       system=system, messages=[{"role": "user", "content": user}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        return text, {"model": msg.model, "input_tokens": msg.usage.input_tokens, "output_tokens": msg.usage.output_tokens}
    payload: dict[str, Any] = {"model": p["model"], "max_tokens": max_tokens, "temperature": temperature,
                               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    d = httpx.post(p["base_url"].rstrip("/") + "/chat/completions", json=payload, timeout=180,
                   headers={"Authorization": f"Bearer {p.get('api_key', '')}"}).raise_for_status().json()
    text = re.sub(r"<think>.*?</think>", "", d["choices"][0]["message"].get("content") or "", flags=re.S).strip()
    u = d.get("usage", {})
    return text, {"model": d.get("model", p["model"]), "input_tokens": u.get("prompt_tokens", 0),
                  "output_tokens": u.get("completion_tokens", 0)}


def _stream_ollama(name: str, system: str, user: str, max_tokens: int, temperature: float, on_text) -> tuple[str, dict[str, Any]]:
    p = _resolve(registry()["profiles"][name])
    body = {"model": p["model"], "stream": True, "think": False,
            "options": {"num_predict": max_tokens, "temperature": temperature, "num_ctx": 16384, "seed": 7},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    parts, meta = [], {"model": p["model"], "input_tokens": 0, "output_tokens": 0}
    with httpx.stream("POST", p["base_url"].rstrip("/") + "/api/chat", json=body, timeout=300) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            d = json.loads(line)
            piece = (d.get("message") or {}).get("content", "")
            if piece:
                parts.append(piece)
                on_text(piece)
            if d.get("done"):
                meta.update(model=d.get("model", p["model"]), input_tokens=d.get("prompt_eval_count", 0),
                            output_tokens=d.get("eval_count", 0))
    return "".join(parts).strip(), meta


def complete(system: str, user: str, *, model: str | None = None, max_tokens: int = 500, temperature: float = 0.2,
             json_mode: bool = False, private_only: bool = False, on_text=None) -> tuple[str, dict[str, Any]]:
    """Call the best eligible profile; fall through the chain on errors. `model` may be a profile name or model id."""
    from . import usage

    usage.bump("llm_calls")
    cands, skipped = route(model, private_only)
    errors: list[str] = []
    for name in cands:
        if name == "offline":
            return _offline(user), {"model": "offline", "profile": "offline", "label": "offline (extractive)",
                                    "input_tokens": 0, "output_tokens": 0, "skipped": skipped + errors}
        try:
            if on_text and not json_mode and registry()["profiles"][name]["provider"] == "ollama":
                text, meta = _stream_ollama(name, system, user, max_tokens, temperature, on_text)
            else:
                text, meta = _call(name, system, user, max_tokens, temperature, json_mode)
                if on_text:
                    on_text(text)
            usage.bump(f"llm_calls_{name.replace('-', '_')}")
            return text, {**meta, "profile": name, "label": registry()["profiles"][name].get("label", name),
                          "skipped": skipped + errors}
        except Exception as e:
            errors.append(f"{name}: {str(e)[:80]}")
            _up[name] = (time.time(), False)
    return _offline(user), {"model": "offline", "profile": "offline", "skipped": skipped + errors}


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
