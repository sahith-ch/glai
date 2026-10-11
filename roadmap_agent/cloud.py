"""Your GLAI Hire account, from your own computer.

Grading runs locally (in Leveller or `python main.py web`), where running your code is safe. Sign in once and
this pushes your progress to your account in the cloud: roadmaps, Arena, Leveller's saved state and your profile
(skill graph, stats). Recruiters then find you on GLAI Hire, and your global rank is among everyone there.

    POST /api/cloud/signin  {email, password}       POST /api/cloud/signup {email, password, name}
    GET  /api/cloud                                 POST /api/cloud/sync   POST /api/cloud/signout

The session (a Supabase refresh token) is kept in cloud_account.json next to your roadmaps, readable only by
you, like the calendar token. A background thread pushes whenever a synced file changes - after a graded
submission it is woken straight away - and at most once every few seconds.

Locally only: the deployed backend already stores everything in the database.
"""

import json
import os
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from . import supa

# the project's public address and publishable key (safe to ship; row-level security does the guarding)
DEFAULT_URL = "https://lzoyhpbxwuowpbrmnpdm.supabase.co"
DEFAULT_KEY = "sb_publishable_cIfxHzNY1wAH3p8q0NIRWQ_DBJKb70g"
DEFAULT_HIRE = "https://glai-hire.vercel.app"
ACCOUNT_FILE = "cloud_account.json"
SYNCED = ["arena.json", "calendar.json", "calendar_log.json", "leveller/leveller.json"]  # + roadmaps, profile
MIN_GAP = 5.0        # seconds between pushes
CHECK_EVERY = 15.0   # how often to look for changes nobody told us about (e.g. a grading job finishing)
OTHERS_TTL = 60.0


def _url() -> str:
    return os.environ.get("SUPABASE_URL", DEFAULT_URL).rstrip("/")


def _key() -> str:
    return os.environ.get("SUPABASE_PUBLISHABLE_KEY", DEFAULT_KEY)


def _hire() -> str:
    return os.environ.get("GLAI_HIRE_URL", DEFAULT_HIRE).rstrip("/")


def _local(root) -> bool:
    return isinstance(root, Path)


class CloudError(Exception):
    pass


# ---------------------------------------------------------------- the saved session


def _read(root: Path) -> dict | None:
    try:
        return json.loads((root / ACCOUNT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(root: Path, account: dict | None) -> None:
    p = root / ACCOUNT_FILE
    if account is None:
        p.unlink(missing_ok=True)
        return
    p.write_text(json.dumps(account, indent=2), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def connected(root) -> bool:
    return _local(root) and (root / ACCOUNT_FILE).exists()


def _auth(path: str, body: dict, token: str | None = None) -> dict:
    headers = {"apikey": _key(), **({"Authorization": f"Bearer {token}"} if token else {})}
    try:
        r = httpx.post(f"{_url()}/auth/v1/{path}", json=body, headers=headers, timeout=20)
    except httpx.HTTPError as e:
        raise CloudError("Couldn't reach GLAI Hire. Check your internet connection.") from e
    data = r.json() if r.content else {}
    if r.status_code >= 400:
        raise CloudError(_friendly(data.get("msg") or data.get("error_description") or data.get("error") or r.text[:200]))
    return data


def _friendly(msg: str) -> str:
    low = msg.lower()
    if "invalid login credentials" in low:
        return "That email and password don't match."
    if "email not confirmed" in low:
        return "Confirm your email first: open the link we sent you, then sign in."
    if "already registered" in low or "already been registered" in low:
        return "There's already an account with that email. Sign in instead."
    if "email rate limit" in low:  # sign-up with email confirmation on: Supabase's mailer allows ~2 emails an hour
        return ("GLAI Hire couldn't send the confirmation email right now (its email limit was reached). "
                "Try again later, or ask the project owner to turn off email confirmation.")
    if "rate limit" in low:
        return "Too many attempts. Wait a minute and try again."
    return msg


_lock = threading.RLock()  # one sign-in / refresh / push at a time


def _session(root: Path) -> tuple[str, dict]:
    """A fresh access token and the account. Supabase rotates refresh tokens, so the new one is saved."""
    with _lock:
        account = _read(root)
        if not account:
            raise CloudError("Not signed in.")
        if account.get("expires_at", 0) - 60 > time.time() and account.get("access_token"):
            return account["access_token"], account
        try:
            t = _auth("token?grant_type=refresh_token", {"refresh_token": account["refresh_token"]})
        except CloudError as e:
            account["last_error"] = f"Signed out: {e}. Sign in again."
            _write(root, account)
            raise
        account.update(access_token=t["access_token"], refresh_token=t["refresh_token"],
                       expires_at=time.time() + int(t.get("expires_in", 3600)))
        _write(root, account)
        return account["access_token"], account


def _store(root: Path, t: dict) -> dict:
    account = {"email": t["user"]["email"], "user_id": t["user"]["id"], "access_token": t["access_token"],
               "refresh_token": t["refresh_token"], "expires_at": time.time() + int(t.get("expires_in", 3600)),
               "last_synced": None, "last_error": None}
    _write(root, account)
    return account


# ---------------------------------------------------------------- account actions


def status(root: Path) -> dict:
    account = _read(root) if _local(root) else None
    if not account:
        return {"connected": False, "hire_url": _hire()}
    return {"connected": True, "email": account["email"], "user_id": account["user_id"],
            "profile_url": f"{_hire()}/c/{account['user_id']}", "hire_url": _hire(),
            "last_synced": account.get("last_synced"), "last_error": account.get("last_error"),
            "syncing": _syncer.busy}


def sign_in(root: Path, email: str, password: str) -> dict:
    with _lock:
        _store(root, _auth("token?grant_type=password", {"email": email.strip(), "password": password}))
    _syncer.start(root)
    _syncer.poke(force=True)
    return status(root)


def sign_up(root: Path, email: str, password: str, name: str) -> dict:
    from . import user

    if len(password) < 8:
        raise CloudError("Use at least 8 characters for the password.")
    t = _auth("signup", {"email": email.strip(), "password": password, "data": {"name": name.strip()}})
    if name.strip():  # recruiters see this name; keep any name already set locally
        profile = user.load(root)
        if not profile.name:
            profile.name = name.strip()
            user.save(profile, root)
    if not t.get("access_token"):
        return {**status(root), "confirm": True}
    with _lock:
        _store(root, t)
    _syncer.start(root)
    _syncer.poke(force=True)
    return status(root)


def sign_out(root: Path) -> dict:
    with _lock:
        account = _read(root)
        _write(root, None)
    if account and account.get("access_token"):
        try:
            _auth("logout", {}, account["access_token"])
        except CloudError:
            pass  # the local session is gone either way
    _others.clear()
    return status(root)


# ---------------------------------------------------------------- pushing


def _files(root: Path) -> list[str]:
    return sorted(p.name for p in root.glob("roadmap_*.json")) + [n for n in SYNCED if (root / n).exists()]


def _signature(root: Path) -> tuple:
    names = _files(root) + (["user_profile.json"] if (root / "user_profile.json").exists() else [])
    return tuple((n, (root / n).stat().st_mtime_ns) for n in names)


def push(root: Path) -> list[str]:
    """Send every synced file to the account; the profile last (its in-progress graph needs the roadmaps)."""
    from . import user

    with _lock:
        token, account = _session(root)
        user.rebuild(root, date.today())  # stats and skill graph up to date before recruiters see them
        folder = supa.Folder(supa.Supabase(_url(), _key(), token), account["user_id"])
        sent = []
        for name in _files(root) + ["user_profile.json"]:
            if (root / name).exists():
                (folder / name).write_text((root / name).read_text(encoding="utf-8"))
                sent.append(name)
        account = _read(root) or account
        account.update(last_synced=datetime.now(timezone.utc).isoformat(timespec="seconds"), last_error=None)
        _write(root, account)
        _others.clear()
        return sent


class _Syncer:
    """Pushes in the background whenever a synced file changes."""

    def __init__(self):
        self.root: Path | None = None
        self.wake = threading.Event()
        self.thread: threading.Thread | None = None
        self.busy = False
        self.force = False
        self.pushed: tuple | None = None
        self.last = 0.0

    def start(self, root: Path) -> None:
        if not _local(root):
            return
        self.root = root
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, name="glai-cloud-sync", daemon=True)
            self.thread.start()

    def poke(self, force: bool = False) -> None:
        self.force = self.force or force
        self.wake.set()

    def _run(self) -> None:
        while True:
            self.wake.wait(CHECK_EVERY)
            self.wake.clear()
            root = self.root
            if not root or not connected(root):
                continue
            try:
                if not self.force and _signature(root) == self.pushed:
                    continue
                gap = MIN_GAP - (time.time() - self.last)
                if gap > 0:
                    time.sleep(gap)  # let a burst of saves settle
                self.busy, self.force = True, False
                push(root)
                self.pushed = _signature(root)  # after the push: rebuild rewrote the profile
            except Exception as e:  # offline, signed out elsewhere: keep the reason for the app to show
                account = _read(root)
                if account:
                    account["last_error"] = str(e)[:300]
                    _write(root, account)
            finally:
                self.busy = False
                self.last = time.time()


_syncer = _Syncer()


def start(root: Path) -> None:
    """Called when the local learner server starts: resume syncing for a signed-in learner."""
    if connected(root):
        _syncer.start(root)
        _syncer.poke(force=True)


def changed(root) -> None:
    """Something was saved through the API (a submission, a new roadmap): push soon."""
    if connected(root):
        _syncer.start(root)
        _syncer.poke()


# ---------------------------------------------------------------- ranking against everyone on GLAI Hire


class _Others:
    def __init__(self):
        self.at, self.data, self.lock = 0.0, None, threading.Lock()

    def clear(self) -> None:
        with self.lock:
            self.at, self.data = 0.0, None

    def get(self, root: Path) -> list | None:
        with self.lock:
            if self.data is not None and time.time() - self.at < OTHERS_TTL:
                return self.data
        account = _read(root)
        try:
            everyone = supa.public_candidates(supa.Supabase(_url(), _key()))
        except Exception:
            return self.data  # offline: last known list, or local candidates if none
        others = [c for c in everyone if c.id != account["user_id"]]
        with self.lock:
            self.at, self.data = time.time(), others
        return others


_others = _Others()


def public_others(root) -> list | None:
    """Signed in: everyone else on GLAI Hire, to rank against. None: use the local candidates."""
    return _others.get(root) if connected(root) else None


# ---------------------------------------------------------------- HTTP


def api(root, method: str, path: str, body: dict) -> tuple[int, dict]:
    if not _local(root):
        return 404, {"error": "Not found"}
    try:
        if path == "/api/cloud" and method == "GET":
            return 200, status(root)
        if method != "POST":
            return 404, {"error": "Not found"}
        if path == "/api/cloud/signin":
            return 200, sign_in(root, str(body.get("email", "")), str(body.get("password", "")))
        if path == "/api/cloud/signup":
            return 200, sign_up(root, str(body.get("email", "")), str(body.get("password", "")), str(body.get("name", "")))
        if path == "/api/cloud/signout":
            return 200, sign_out(root)
        if path == "/api/cloud/sync":
            if not connected(root):
                return 400, {"error": "Not signed in."}
            push(root)
            return 200, status(root)
    except CloudError as e:
        return 400, {"error": str(e)}
    except supa.SupabaseError as e:
        return 502, {"error": f"GLAI Hire refused the update: {e}"}
    return 404, {"error": "Not found"}
