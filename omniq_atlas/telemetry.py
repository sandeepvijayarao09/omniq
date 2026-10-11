"""Raindrop monitoring: every assistant answer and every eval verdict is sent as an event,
and employee feedback / eval failures as signals. The evolution loop consumes the same
signals (mirrored in Atlas `feedback` and `episodes`), so Raindrop sees what the harness learns from."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from .config import settings


@lru_cache(maxsize=1)
def _rd():
    if not settings.raindrop_key:
        return None
    import raindrop.analytics as raindrop

    raindrop.init(settings.raindrop_key)
    return raindrop


def available() -> bool:
    return _rd() is not None


def track_answer(event_id: str, user_id: str, question: str, answer: str, model: str,
                 convo_id: str, properties: dict[str, Any]) -> None:
    rd = _rd()
    if rd is None:
        return
    from . import usage

    usage.bump("raindrop_events")
    try:
        rd.track_ai(user_id=user_id, event="assistant_answer", model=model, input=question, output=answer,
                    convo_id=convo_id, event_id=event_id, properties=properties)
    except Exception:
        pass


def signal(event_id: str, name: str, negative: bool, comment: str = "") -> None:
    rd = _rd()
    if rd is None:
        return
    from . import usage

    usage.bump("raindrop_signals")
    try:
        kwargs: dict[str, Any] = {"event_id": event_id, "name": name,
                                  "sentiment": "NEGATIVE" if negative else "POSITIVE"}
        if comment:
            kwargs.update(signal_type="feedback", comment=comment)
        rd.track_signal(**kwargs)
    except Exception:
        pass


def flush() -> None:
    rd = _rd()
    if rd is not None:
        try:
            rd.flush()
        except Exception:
            pass
