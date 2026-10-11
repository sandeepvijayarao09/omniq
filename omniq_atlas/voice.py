"""ElevenLabs voice: Scribe speech-to-text in, Flash text-to-speech out. Keys stay server-side."""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO

from .config import settings


@lru_cache(maxsize=1)
def _el():
    if not settings.elevenlabs_key:
        return None
    from elevenlabs.client import ElevenLabs

    return ElevenLabs(api_key=settings.elevenlabs_key)


def available() -> bool:
    return _el() is not None


def transcribe(audio: bytes) -> str:
    el = _el()
    if el is None:
        raise RuntimeError("ElevenLabs is not configured")
    res = el.speech_to_text.convert(file=BytesIO(audio), model_id="scribe_v2")
    return res.text


def speak(text: str) -> bytes:
    el = _el()
    if el is None:
        raise RuntimeError("ElevenLabs is not configured")
    audio = el.text_to_speech.convert(text=text[:2500], voice_id=settings.elevenlabs_voice,
                                      model_id="eleven_flash_v2_5", output_format="mp3_44100_128")
    return b"".join(audio)
