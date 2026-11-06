"""Give each employee who uses a database agent their own Auto Ontology identity and API token.

For every employee with access to an agent that has the `structured_query` tool:
  1. the local admin creates an invitation with the **viewer** role (no email is sent)
  2. the invitation is accepted with a random one-time password, which is never stored or printed
  3. that employee's session mints an API token → var/auto-ontology-tokens.json (mode 600)

Auto Ontology's viewer role can chat and read the catalog but not edit it. Auto Ontology has no per-user table
scoping, so Omniq's table check (structured.authorize) remains the only per-person data control; these tokens add
identity, attribution in Auto Ontology's own logs, and least privilege on the engine.

Usage: .venv/bin/python scripts/auto_ontology_users.py
"""

from __future__ import annotations

import json
import os
import pathlib
import secrets
import sys

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from omniq import agents  # noqa: E402
from omniq.assistant import get_user  # noqa: E402
from omniq.db import db  # noqa: E402

URL = os.environ.get("OA_AO_URL", "http://localhost:3000").rstrip("/")
AO_ENV = ROOT / "var" / "vendor" / "auto-ontology" / ".env"
TOKENS = ROOT / "var" / "auto-ontology-tokens.json"


def _env() -> dict[str, str]:
    return dict(l.split("=", 1) for l in AO_ENV.read_text().splitlines() if "=" in l and not l.startswith("#"))


def needs_engine(uid: str) -> bool:
    user = get_user(uid)
    return any("structured_query" in (a.get("tools") or []) for a in agents.for_user(user))


def session(email: str, password: str) -> httpx.Client:
    c = httpx.Client(base_url=URL, timeout=60, headers={"Origin": URL})
    c.post("/api/auth/sign-in/email", json={"email": email, "password": password}).raise_for_status()
    return c


def main() -> int:
    env = _env()
    admin = session(env["AUTO_ONTOLOGY_ADMIN_EMAIL"].strip(), env["AUTO_ONTOLOGY_ADMIN_PASSWORD"].strip())
    tokens: dict[str, str] = json.loads(TOKENS.read_text()) if TOKENS.exists() else {}
    people = [u["id"] for u in db().users.find({}, {"id": 1}) if needs_engine(u["id"])]
    made = 0
    for uid in people:
        if uid in tokens:
            continue
        u = db().users.find_one({"id": uid})
        r = admin.post("/api/invitations", json={"email": u["email"], "name": u["name"], "role": "viewer"})
        if r.status_code >= 400:
            print(f"{uid}: invitation failed ({r.status_code}): {r.text[:120]}")
            continue
        token = r.json()["url"].rstrip("/").split("/")[-1].split("token=")[-1]
        password = secrets.token_urlsafe(24)
        a = httpx.post(f"{URL}/api/invitations/{token}/accept", json={"password": password},
                       headers={"Origin": URL}, timeout=60)
        if a.status_code >= 400:
            print(f"{uid}: accept failed ({a.status_code}): {a.text[:120]}")
            continue
        me = session(u["email"], password)
        k = me.post("/api/api-tokens", json={"name": f"omniq-{uid}"})
        k.raise_for_status()
        tokens[uid] = k.json()["token"]
        made += 1
        print(f"{uid}: viewer account and token created")
    TOKENS.write_text(json.dumps(tokens, indent=1))
    TOKENS.chmod(0o600)
    print(f"{made} new, {len(tokens)} total of {len(people)} employees who use database agents → {TOKENS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
