"""GLAI Hire account from the desktop (cloud.py), offline: Supabase is faked, so no network or account is needed."""

import json
import tempfile
import time
from pathlib import Path

from roadmap_agent import cloud, server

calls = []


def fake_auth(path, body, token=None):
    calls.append(path)
    if path.startswith("token?grant_type=password"):
        if body["password"] != "right-password":
            raise cloud.CloudError("That email and password don't match.")
        return {"access_token": "a1", "refresh_token": "r1", "expires_in": 3600,
                "user": {"id": "user-1", "email": body["email"]}}
    if path.startswith("token?grant_type=refresh_token"):
        assert body["refresh_token"] == "r1"
        return {"access_token": "a2", "refresh_token": "r2", "expires_in": 3600}
    if path == "signup":
        return {"user": {"id": "user-2"}}  # the project asks for email confirmation: no session yet
    return {}


pushed = []
cloud._auth = fake_auth
cloud.push = lambda root: pushed.append(root) or []  # the real push needs a Supabase account: see "Desktop learners" in DEPLOY.md
cloud._syncer.start = lambda root: None  # no background thread in a unit test

with tempfile.TemporaryDirectory() as d:
    root = Path(d)

    # signed out: nothing to push, and ranking uses the local candidates
    assert server.learner_api(root, "GET", "/api/cloud", {}, {}) == (200, {"connected": False, "hire_url": cloud._hire()})
    assert cloud.public_others(root) is None

    # a wrong password says so, and leaves no session behind
    status, body = server.learner_api(root, "POST", "/api/cloud/signin", {}, {"email": "a@b.co", "password": "nope"})
    assert status == 400 and "don't match" in body["error"]
    assert not (root / cloud.ACCOUNT_FILE).exists()

    # signing in keeps the session locally and links the public profile
    status, body = server.learner_api(root, "POST", "/api/cloud/signin", {}, {"email": "a@b.co", "password": "right-password"})
    assert status == 200 and body["connected"] and body["email"] == "a@b.co"
    assert body["profile_url"].endswith("/c/user-1")
    saved = json.loads((root / cloud.ACCOUNT_FILE).read_text())
    assert saved["refresh_token"] == "r1"

    # an expired access token is refreshed, and the rotated refresh token is saved
    saved["expires_at"] = time.time() - 10
    (root / cloud.ACCOUNT_FILE).write_text(json.dumps(saved))
    token, _ = cloud._session(root)
    assert token == "a2" and json.loads((root / cloud.ACCOUNT_FILE).read_text())["refresh_token"] == "r2"

    # sign-up on a project that confirms emails: told to confirm, not signed in; the name is kept for the profile
    (root / cloud.ACCOUNT_FILE).unlink()
    status, body = server.learner_api(root, "POST", "/api/cloud/signup", {}, {"email": "n@b.co", "password": "long-enough", "name": "Nia"})
    assert status == 200 and body["confirm"] and not body["connected"]
    assert json.loads((root / "user_profile.json").read_text())["name"] == "Nia"
    status, body = server.learner_api(root, "POST", "/api/cloud/signup", {}, {"email": "n@b.co", "password": "short", "name": "Nia"})
    assert status == 400 and "8 characters" in body["error"]

    # signing out forgets the session
    server.learner_api(root, "POST", "/api/cloud/signin", {}, {"email": "a@b.co", "password": "right-password"})
    status, body = server.learner_api(root, "POST", "/api/cloud/signout", {}, {})
    assert status == 200 and not body["connected"] and not (root / cloud.ACCOUNT_FILE).exists()

# the deployed backend (database storage, not a folder) has no desktop account routes
assert cloud.api(object(), "GET", "/api/cloud", {})[0] == 404

print("ALL OK")
