"""Settings, read once from the environment (and .env if present)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class Settings:
    mongodb_uri: str = _env("MONGODB_URI", "mongodb://localhost:27017/?directConnection=true")
    mongodb_db: str = _env("MONGODB_DB", "omniq_atlas")
    voyage_key: str = _env("VOYAGE_API_KEY")
    embed_model: str = _env("VOYAGE_EMBED_MODEL", "voyage-4")
    rerank_model: str = _env("VOYAGE_RERANK_MODEL", "rerank-3")
    anthropic_key: str = _env("ANTHROPIC_API_KEY")
    # Any OpenAI-compatible endpoint (Ollama by default) is used when no Anthropic key is set.
    llm_base_url: str = _env("OA_LLM_BASE_URL", "http://localhost:11434/v1")
    llm_api_key: str = _env("OA_LLM_API_KEY", "ollama")
    assistant_model: str = _env("OA_ASSISTANT_MODEL", "claude-haiku-5-5" if _env("ANTHROPIC_API_KEY") else "gemma4:e4b")
    meta_model: str = _env("OA_META_MODEL", "claude-sonnet-5-5" if _env("ANTHROPIC_API_KEY") else "gemma4:e4b")
    raindrop_key: str = _env("RAINDROP_WRITE_KEY")
    elevenlabs_key: str = _env("ELEVENLABS_API_KEY")
    elevenlabs_voice: str = _env("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
    jwt_secret: str = _env("OA_JWT_SECRET", "dev-secret-change-me")
    seed_dir: Path = ROOT / "data" / "seed"
    evals_file: Path = ROOT / "data" / "evals.yaml"
    embed_dim: int = 1024


settings = Settings()
