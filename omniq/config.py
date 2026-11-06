"""Settings, read once from the environment (and .env if present).

Everything runs locally: an embedded document store (one SQLite file), local
embeddings through Ollama (or a hashing fallback), and any model behind the
gateway in ``config/models.yaml``. No hosted service or account is required.
"""

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
    data_dir: Path = Path(_env("OA_DATA_DIR", str(ROOT / "var")))
    db_file: str = _env("OA_DB_FILE", "omniq.db")
    # Embeddings: auto | ollama | hash. BGE expects an instruction prefix on queries.
    embedder: str = _env("OA_EMBEDDER", "auto")
    ollama_url: str = _env("OA_OLLAMA_URL", "http://localhost:11434")
    embed_model: str = _env("OA_EMBED_MODEL", "hf.co/CompendiumLabs/bge-base-en-v1.5-gguf:latest")
    query_prefix: str = _env("OA_QUERY_PREFIX", "Represent this sentence for searching relevant passages: ")
    embed_dim: int = int(_env("OA_EMBED_DIM", "768"))
    anthropic_key: str = _env("ANTHROPIC_API_KEY")
    meta_model: str = _env("OA_META_MODEL", "")
    jwt_secret: str = _env("OA_JWT_SECRET", "dev-secret-change-me")
    seed_dir: Path = ROOT / "data" / "seed"
    evals_file: Path = ROOT / "data" / "evals.yaml"

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_file


settings = Settings()
