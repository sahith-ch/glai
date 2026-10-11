"""Google Calendar, free/busy only.

GLAI asks Google for one permission - calendar.freebusy, "see the availability on your calendars" - so it
learns when the learner is busy and never sees event titles, attendees or descriptions.

Sign-in is the installed-app OAuth flow with PKCE: the learner approves in their browser, Google redirects to
this server on 127.0.0.1, and the refresh token is kept in calendar_token.json next to the roadmaps.

Setup (once): in Google Cloud Console create an OAuth client of type "Desktop app", enable the Google Calendar
API, add yourself as a test user on the consent screen, and put GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in .env.
"""

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
FREEBUSY_URL = "https://www.googleapis.com/calendar/v3/freeBusy"
SCOPE = "https://www.googleapis.com/auth/calendar.freebusy"
TOKEN_FILE = "calendar_token.json"
STATE_TTL = 600  # seconds a sign-in may take

_pending: dict[str, dict] = {}  # state -> {verifier, redirect, created}
_lock = threading.Lock()


class CalendarError(Exception):
    pass


def client() -> tuple[str, str] | None:
    cid, secret = os.environ.get("GOOGLE_CLIENT_ID", "").strip(), os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    return (cid, secret) if cid and secret else None


def _post(url: str, data: dict, headers: dict | None = None, form: bool = True) -> dict:
    body = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded" if form else "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise CalendarError(f"Google answered {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise CalendarError(f"Could not reach Google ({e.reason}).") from e


# ---------------------------------------------------------------- sign-in


def start(redirect_uri: str, owner: dict | None = None) -> str:
    """The Google consent URL to open. Remembers a single-use state + PKCE verifier for the callback."""
    creds = client()
    if not creds:
        raise CalendarError("Google sign-in is not set up: add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to .env.")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    with _lock:
        now = time.time()
        for s in [s for s, p in _pending.items() if now - p["created"] > STATE_TTL]:
            _pending.pop(s, None)
        # owner: on the shared backend, who started this sign-in (Google's redirect carries no session)
        _pending[state] = {"verifier": verifier, "redirect": redirect_uri, "created": now, "owner": owner}
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": creds[0], "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })


def finish(root: Path, state: str, code: str) -> None:
    """Exchange the callback's code for tokens. The state must be one we issued, recently, and unused."""
    with _lock:
        pending = _pending.pop(state, None)
    if not pending or time.time() - pending["created"] > STATE_TTL:
        raise CalendarError("This sign-in link expired or was already used. Start again from the app.")
    creds = client()
    if not creds:
        raise CalendarError("Google sign-in is not set up.")
    tok = _post(TOKEN_URL, {"code": code, "client_id": creds[0], "client_secret": creds[1],
                            "redirect_uri": pending["redirect"], "grant_type": "authorization_code",
                            "code_verifier": pending["verifier"]})
    if "refresh_token" not in tok:
        raise CalendarError("Google did not return a refresh token. Remove GLAI's access in your Google account and connect again.")
    _save_token(root, {"refresh_token": tok["refresh_token"], "access_token": tok.get("access_token"),
                       "expires_at": time.time() + float(tok.get("expires_in", 0)) - 60,
                       "connected": datetime.now().isoformat(timespec="seconds")})


def pending_owner(state: str) -> dict | None:
    """Who started the sign-in with this state, without consuming it (the shared backend's callback)."""
    with _lock:
        p = _pending.get(state)
        return p["owner"] if p else None


def connected(root: Path) -> bool:
    return (root / TOKEN_FILE).exists()


def disconnect(root: Path) -> None:
    """Forget the token here, and ask Google to revoke it (best effort)."""
    tok = _load_token(root)
    (root / TOKEN_FILE).unlink(missing_ok=True)
    if tok and tok.get("refresh_token"):
        try:
            _post("https://oauth2.googleapis.com/revoke", {"token": tok["refresh_token"]})
        except CalendarError:
            pass


def _load_token(root: Path) -> dict | None:
    p = root / TOKEN_FILE
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _save_token(root: Path, tok: dict) -> None:
    p = root / TOKEN_FILE
    p.write_text(json.dumps(tok), encoding="utf-8")
    if isinstance(p, Path):  # a local file: owner-only; in the database it is protected by row-level security
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass


def _access_token(root: Path) -> str:
    tok = _load_token(root)
    if not tok:
        raise CalendarError("Google Calendar is not connected.")
    if tok.get("access_token") and time.time() < tok.get("expires_at", 0):
        return tok["access_token"]
    creds = client()
    if not creds:
        raise CalendarError("Google sign-in is not set up.")
    fresh = _post(TOKEN_URL, {"refresh_token": tok["refresh_token"], "client_id": creds[0],
                              "client_secret": creds[1], "grant_type": "refresh_token"})
    tok.update(access_token=fresh["access_token"], expires_at=time.time() + float(fresh.get("expires_in", 0)) - 60)
    _save_token(root, tok)
    return tok["access_token"]


# ---------------------------------------------------------------- free / busy


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def busy(root: Path, start: datetime, end: datetime, calendars: list[str] | None = None) -> list[tuple[datetime, datetime]]:
    """Busy intervals between start and end (aware datetimes), in local time, merged across calendars."""
    data = _post(FREEBUSY_URL, {
        "timeMin": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "timeMax": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "items": [{"id": c} for c in (calendars or ["primary"])],
    }, headers={"Authorization": f"Bearer {_access_token(root)}"}, form=False)
    local = datetime.now().astimezone().tzinfo
    out = []
    for cal in (data.get("calendars") or {}).values():
        if cal.get("errors"):
            raise CalendarError(f"Google Calendar refused: {cal['errors'][0].get('reason', 'unknown')}")
        out += [(_parse(b["start"]).astimezone(local), _parse(b["end"]).astimezone(local)) for b in cal.get("busy", [])]
    return sorted(out)
