"""OpenAI-compatible front for Ollama that fixes two things engines like Auto Ontology can't configure.

  POST /v1/embeddings         returns exactly OA_EMBED_PROXY_DIMS (2048) values per text. Auto Ontology v1.2.0
                              stores embeddings in a fixed 2048-d column and never asks for a size. Smaller vectors
                              (e.g. BGE-base, 768-d) are zero-padded, which leaves every dot product and norm, and so
                              every cosine similarity, unchanged; larger ones are requested at 2048 from models
                              trained to be truncated (Matryoshka, e.g. Qwen3-Embedding).
  POST /v1/chat/completions   adds `reasoning_effort: "none"` unless the caller set one. Gemma 4 is a thinking
                              model: on Ollama's OpenAI endpoint it otherwise spends the whole output budget on hidden
                              reasoning and returns empty content, so structured-output calls fail and time out.
                              Streaming responses are passed through as they arrive.

  .venv/bin/python scripts/ollama_proxy.py     # listens on 0.0.0.0:11435 (reachable from Docker)
  env: OA_OLLAMA_PROXY_PORT, OA_EMBED_PROXY_DIMS (2048), OA_OLLAMA_URL (http://localhost:11434),
       OA_REASONING_EFFORT (none)
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

PORT = int(os.environ.get("OA_OLLAMA_PROXY_PORT", os.environ.get("OA_EMBED_PROXY_PORT", "11435")))
DIMS = int(os.environ.get("OA_EMBED_PROXY_DIMS", "2048"))
EFFORT = os.environ.get("OA_REASONING_EFFORT", "none")
OLLAMA = os.environ.get("OA_OLLAMA_URL", "http://localhost:11434").rstrip("/")
client = httpx.Client(timeout=httpx.Timeout(900, connect=10))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"  # streamed bodies end when the connection closes

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.rstrip("/")
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self._send(400, b'{"error":"invalid JSON"}')
        if path == "/v1/embeddings":
            body = {k: req[k] for k in ("model", "input") if k in req}
            r = client.post(f"{OLLAMA}/v1/embeddings", json={**body, "dimensions": DIMS})
            if r.status_code != 200:
                return self._send(r.status_code, r.content, r.headers.get("content-type", "application/json"))
            out = r.json()
            for item in out.get("data", []):
                v = item["embedding"]
                item["embedding"] = v[:DIMS] + [0.0] * (DIMS - len(v)) if len(v) != DIMS else v
            return self._send(200, json.dumps(out).encode())
        if path == "/v1/chat/completions":
            req.setdefault("reasoning_effort", EFFORT)
            if not req.get("stream"):
                r = client.post(f"{OLLAMA}/v1/chat/completions", json=req)
                return self._send(r.status_code, r.content, r.headers.get("content-type", "application/json"))
            with client.stream("POST", f"{OLLAMA}/v1/chat/completions", json=req) as r:
                self.send_response(r.status_code)
                self.send_header("Content-Type", r.headers.get("content-type", "text/event-stream"))
                self.end_headers()
                for chunk in r.iter_raw():
                    self.wfile.write(chunk)
                    self.wfile.flush()
            return None
        return self._send(404, b'{"error":"only /v1/embeddings and /v1/chat/completions are proxied"}')

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/v1/models":  # some clients probe this
            r = client.get(f"{OLLAMA}/v1/models")
            return self._send(r.status_code, r.content)
        self._send(200, json.dumps({"ok": True, "dimensions": DIMS, "reasoning_effort": EFFORT, "upstream": OLLAMA}).encode())

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    print(f"ollama proxy on :{PORT} → {OLLAMA} (embeddings {DIMS}-d, reasoning_effort={EFFORT})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
