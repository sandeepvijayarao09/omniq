#!/usr/bin/env bash
# Start everything the public demo needs on this machine: API, tunnel, and print the URL to give Modelence.
set -euo pipefail
cd "$(dirname "$0")/.."
curl -sf localhost:11434/api/tags >/dev/null || { echo "Start Ollama first (open the Ollama app)"; exit 1; }
pkill -f "oa serve" || true
nohup .venv/bin/oa serve --port 8000 > /tmp/oa_srv.log 2>&1 &
sleep 3
curl -sf 127.0.0.1:8000/api/status >/dev/null && echo "API up on :8000"
pkill -f "cloudflared tunnel" || true
nohup cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8000 > /tmp/cf.log 2>&1 &
for _ in $(seq 1 30); do
  url=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' /tmp/cf.log | head -1 || true)
  [ -n "$url" ] && break; sleep 1
done
echo "Tunnel: $url"
if [ "$url" != "$(cat modelence/upstream.txt)" ]; then
  echo "Tunnel URL changed. Set FASTAPI_URL=$url in Modelence (Configuration -> Environment variables) and redeploy,"
  echo "or: echo $url > modelence/upstream.txt && git commit -am 'tunnel' && (cd modelence && npx modelence@latest deploy --app omniq-mv4a4zfo47x --env prod)"
fi
