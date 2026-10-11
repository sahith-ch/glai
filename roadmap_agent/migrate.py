"""Move a local GLAI folder into a learner's account on the shared backend (Supabase).

    python main.py upload --email you@example.com

Signs in as that learner (Supabase Auth, email + password) and writes each local file as their own row, so
row-level security applies exactly as it will for the deployed backend. Roadmaps first, the profile last (its
in-progress graph is computed from the roadmaps). The Google Calendar token is not copied: it belongs to the
local sign-in address, so connect the calendar again from the deployed app.
"""

import os
from pathlib import Path

import httpx

from . import supa

FILES = ["arena.json", "calendar.json", "calendar_log.json", "leveller/leveller.json"]


def sign_in(url: str, key: str, email: str, password: str) -> dict:
    """{access_token, user: {id, ...}} for an existing account."""
    r = httpx.post(f"{url.rstrip('/')}/auth/v1/token", params={"grant_type": "password"},
                   json={"email": email, "password": password}, headers={"apikey": key}, timeout=20)
    if r.status_code >= 400:
        raise supa.SupabaseError(f"Sign-in failed: {r.json().get('error_description') or r.text[:200]}")
    return r.json()


def upload(root: Path, email: str, password: str, url: str | None = None, key: str | None = None) -> list[str]:
    url = url or os.environ["SUPABASE_URL"]
    key = key or os.environ["SUPABASE_PUBLISHABLE_KEY"]
    session = sign_in(url, key, email, password)
    folder = supa.Folder(supa.Supabase(url, key, session["access_token"]), session["user"]["id"])
    names = sorted(p.name for p in root.glob("roadmap_*.json")) + [n for n in FILES if (root / n).exists()]
    sent = []
    for name in names:
        (folder / name).write_text((root / name).read_text(encoding="utf-8"))
        sent.append(name)
    if (root / "user_profile.json").exists():
        (folder / "user_profile.json").write_text((root / "user_profile.json").read_text(encoding="utf-8"))
        sent.append("user_profile.json")
    return sent
