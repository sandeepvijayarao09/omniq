"""OpenAI-compatible embeddings endpoint that fixes the output size, for engines that can't ask for one.

Auto Ontology v1.2.0 stores embeddings in a fixed 2048-dimension column and never sends `dimensions`. Qwen3-Embedding
is trained to be truncated (Matryoshka), and Ollama returns any requested size, so this proxy forwards
`POST /v1/embeddings` to Ollama with `dimensions` set. Nothing else is proxied.

  .venv/bin/python scripts/embed_proxy.py            # listens on 0.0.0.0:11435 (reachable from Docker)
  env: OA_EMBED_PROXY_PORT, OA_EMBED_PROXY_DIMS (2048), OA_OLLAMA_URL (http://localhost:11434)
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

PORT = int(os.environ.get("OA_EMBED_PROXY_PORT", "11435"))
DIMS = int(os.environ.get("OA_EMBED_PROXY_DIMS", "2048"))
OLLAMA = os.environ.get("OA_OLLAMA_URL", "http://localhost:11434").rstrip("/")
client = httpx.Client(timeout=600)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/v1/embeddings":
            return self._send(404, b'{"error":"only /v1/embeddings is proxied"}')
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self._send(400, b'{"error":"invalid JSON"}')
        body = {k: req[k] for k in ("model", "input", "encoding_format") if k in req}
        body["dimensions"] = DIMS
        r = client.post(f"{OLLAMA}/v1/embeddings", json=body)
        self._send(r.status_code, r.content, r.headers.get("content-type", "application/json"))

    def do_GET(self) -> None:  # noqa: N802
        self._send(200, json.dumps({"ok": True, "dimensions": DIMS, "upstream": OLLAMA}).encode())

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    print(f"embedding proxy on :{PORT} → {OLLAMA} ({DIMS} dims)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
