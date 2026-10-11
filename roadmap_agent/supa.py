"""Supabase: sign-in and storage for the shared (deployed) backend.

Locally GLAI keeps a folder of JSON files. Deployed, each learner's "folder" is a set of rows in Postgres,
and every request runs as that learner: the backend calls Supabase with the learner's own access token, so
row-level security - not this code - is what keeps one learner's data from another's.

`Folder` is the seam. It offers the slice of pathlib.Path that GLAI's modules use (`/`, read_text,
write_text, glob, exists, unlink, stat, stem, parent, ...), backed by two tables:

    user_profile.json   -> profiles   (one row per learner; public ones are what recruiters search)
    everything else     -> documents  (owner, name, data) - roadmaps, arena, Leveller's state, calendar

so roadmaps, grading, ranking and the rest run unchanged against either a real folder or a learner's rows.
Code execution (workspaces, test runs) needs a real disk and is refused before it reaches a Folder
(see execution.py).
"""

import fnmatch
import json
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx

from .schemas import SkillGraph, UserProfile

PROFILE_DOC = "user_profile.json"
TIMEOUT = 20


class SupabaseError(Exception):
    pass


class Supabase:
    """PostgREST + Auth over HTTP, as one learner (token) or anonymously (publishable key only)."""

    def __init__(self, url: str, key: str, token: str | None = None):
        self.url, self.key, self.token = url.rstrip("/"), key, token

    def _headers(self, extra: dict | None = None) -> dict:
        return {"apikey": self.key, "Authorization": f"Bearer {self.token or self.key}", **(extra or {})}

    def _check(self, r: httpx.Response):
        if r.status_code >= 400:
            raise SupabaseError(f"Supabase {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None

    def select(self, table: str, params: dict) -> list[dict]:
        return self._check(httpx.get(f"{self.url}/rest/v1/{table}", params=params, headers=self._headers(), timeout=TIMEOUT))

    def upsert(self, table: str, row: dict, on_conflict: str) -> None:
        self._check(httpx.post(f"{self.url}/rest/v1/{table}", params={"on_conflict": on_conflict}, json=row,
                               headers=self._headers({"Prefer": "resolution=merge-duplicates,return=minimal"}), timeout=TIMEOUT))

    def delete(self, table: str, params: dict) -> None:
        self._check(httpx.delete(f"{self.url}/rest/v1/{table}", params=params,
                                 headers=self._headers({"Prefer": "return=minimal"}), timeout=TIMEOUT))

    def user(self) -> dict:
        """The signed-in learner for this token (Supabase checks the signature and expiry)."""
        r = httpx.get(f"{self.url}/auth/v1/user", headers=self._headers(), timeout=TIMEOUT)
        if r.status_code in (401, 403):
            raise SupabaseError("Not signed in, or the session expired.")
        return self._check(r)


_users: dict[str, tuple[float, dict]] = {}
_users_lock = threading.Lock()
USER_CACHE_SECONDS = 60


def verify(url: str, key: str, token: str) -> dict:
    """Who a bearer token belongs to. Cached briefly so each request doesn't round-trip to Supabase Auth."""
    now = time.time()
    with _users_lock:
        hit = _users.get(token)
        if hit and now - hit[0] < USER_CACHE_SECONDS:
            return hit[1]
    u = Supabase(url, key, token).user()
    with _users_lock:
        _users[token] = (now, u)
        for t in [t for t, (at, _) in _users.items() if now - at > USER_CACHE_SECONDS]:
            _users.pop(t, None)
    return u


# ---------------------------------------------------------------- a learner's rows, as a folder


@dataclass
class _Stat:
    st_mtime: float
    st_size: int


class Folder:
    """One learner's storage, path-shaped. `key` is the path inside the folder ("" is the folder itself)."""

    def __init__(self, db: Supabase, owner: str, key: str = "", _cache: dict | None = None):
        self.db, self.owner, self.key = db, owner, key.strip("/")
        self._cache = _cache if _cache is not None else {"docs": None, "lock": threading.Lock()}

    # --- path shape
    def __truediv__(self, part: str) -> "Folder":
        return Folder(self.db, self.owner, f"{self.key}/{part}" if self.key else str(part), self._cache)

    @property
    def name(self) -> str:
        return self.key.rsplit("/", 1)[-1]

    @property
    def suffix(self) -> str:
        return "." + self.name.rsplit(".", 1)[1] if "." in self.name else ""

    @property
    def stem(self) -> str:
        return self.name[: -len(self.suffix)] if self.suffix else self.name

    @property
    def parent(self) -> "Folder":
        return Folder(self.db, self.owner, self.key.rsplit("/", 1)[0] if "/" in self.key else "", self._cache)

    def with_suffix(self, suffix: str) -> "Folder":
        base = self.key[: -len(self.suffix)] if self.suffix else self.key
        return Folder(self.db, self.owner, base + suffix, self._cache)

    def resolve(self) -> "Folder":
        return self

    def __eq__(self, other) -> bool:
        return isinstance(other, Folder) and (self.owner, self.key) == (other.owner, other.key)

    def __hash__(self) -> int:
        return hash((self.owner, self.key))

    def __lt__(self, other: "Folder") -> bool:  # sorted() over paths, as with pathlib
        return (self.owner, self.key) < (other.owner, other.key)

    def __str__(self) -> str:
        return f"supabase://{self.owner}/{self.key}"

    __repr__ = __str__

    # --- contents
    def _docs(self) -> dict[str, tuple[str, float]]:
        """name -> (text, mtime) for every document of this learner, read once per request."""
        with self._cache["lock"]:
            if self._cache["docs"] is None:
                rows = self.db.select("documents", {"select": "name,data,updated_at", "owner": f"eq.{self.owner}"})
                docs = {r["name"]: (json.dumps(r["data"]), _mtime(r["updated_at"])) for r in rows}
                prof = self.db.select("profiles", {"select": "profile,updated_at", "id": f"eq.{self.owner}"})
                if prof and prof[0]["profile"]:
                    docs[PROFILE_DOC] = (json.dumps(prof[0]["profile"]), _mtime(prof[0]["updated_at"]))
                self._cache["docs"] = docs
            return self._cache["docs"]

    def exists(self) -> bool:
        if not self.key:
            return True
        docs = self._docs()
        return self.key in docs or any(n.startswith(self.key + "/") for n in docs)

    def is_file(self) -> bool:
        return self.key in self._docs()

    def is_dir(self) -> bool:
        return self.exists() and not self.is_file()

    def stat(self) -> _Stat:
        text, mtime = self._docs().get(self.key, ("", 0.0))
        return _Stat(mtime, len(text.encode("utf-8")))

    def read_text(self, encoding: str = "utf-8", errors: str | None = None) -> str:
        doc = self._docs().get(self.key)
        if doc is None:
            raise FileNotFoundError(str(self))
        return doc[0]

    def write_text(self, text: str, encoding: str = "utf-8", newline: str | None = None) -> int:
        data = json.loads(text)
        if self.key == PROFILE_DOC:
            self._save_profile(data)
        else:
            self.db.upsert("documents", {"owner": self.owner, "name": self.key, "data": data}, "owner,name")
        with self._cache["lock"]:
            if self._cache["docs"] is not None:
                self._cache["docs"][self.key] = (text, time.time())
        return len(text)

    def unlink(self, missing_ok: bool = False) -> None:
        if not self.is_file():
            if missing_ok:
                return
            raise FileNotFoundError(str(self))
        if self.key == PROFILE_DOC:
            raise PermissionError("The profile is removed with the account.")
        self.db.delete("documents", {"owner": f"eq.{self.owner}", "name": f"eq.{self.key}"})
        with self._cache["lock"]:
            self._cache["docs"].pop(self.key, None)

    def mkdir(self, parents: bool = False, exist_ok: bool = False) -> None:
        pass  # folders exist as soon as something is written under them

    def glob(self, pattern: str) -> list["Folder"]:
        prefix = f"{self.key}/" if self.key else ""
        names = [n[len(prefix):] for n in self._docs() if n.startswith(prefix)]
        return sorted((self / n for n in names if "/" not in n and fnmatch.fnmatch(n, pattern)), key=lambda f: f.key)

    def rglob(self, pattern: str) -> list["Folder"]:
        prefix = f"{self.key}/" if self.key else ""
        return sorted((Folder(self.db, self.owner, n, self._cache) for n in self._docs()
                       if n.startswith(prefix) and fnmatch.fnmatch(n.rsplit("/", 1)[-1], pattern)), key=lambda f: f.key)

    # --- the profile is a row of its own, so recruiters can search it
    def _save_profile(self, data: dict) -> None:
        from . import user  # the in-progress graph needs this learner's roadmaps

        profile = UserProfile.model_validate(data)
        row = {"id": self.owner, "name": profile.name, "headline": profile.headline, "location": profile.location,
               "profile": data}
        try:
            # a demo profile is hand-written with no roadmaps behind it: its saved graph is the whole story
            # computed from the learner's whole folder (self is the profile document itself)
            graph = profile.skill_graph if profile.demo else user.learning_graph(self.parent, profile)
            row["learning_graph"] = graph.model_dump()
        except Exception:
            pass  # the earned graph is what matters for search; the profile page falls back to it
        self.db.upsert("profiles", row, "id")

    # --- everyone else, for search and ranking
    def candidates(self, today: date):
        """The local learner as "me" plus every public profile: what the hiring search and ranking rank."""
        from . import hire, user

        me = user.rebuild(self, today)
        out = [hire.Candidate("me", me, user.learning_graph(self, me))]
        out += [c for c in public_candidates(self.db) if c.id != self.owner]
        return out

    def record_rank(self, place: dict) -> None:
        """Today's rank, for rank history (one row per learner per day)."""
        # UTC: the same day wherever the server runs (a server's local date would split one day into two)
        day = datetime.now(timezone.utc).date().isoformat()
        self.db.upsert("rank_snapshots", {"user_id": self.owner, "day": day, "rank": place["rank"],
                                          "total": place["total"], "points": place["points"], "tier": place["tier"]},
                       "user_id,day")

    def rank_history(self, days: int = 90) -> list[dict]:
        return self.db.select("rank_snapshots", {"select": "day,rank,total,points,tier", "user_id": f"eq.{self.owner}",
                                                 "order": "day.asc", "limit": str(days)})


def public_candidates(db: Supabase) -> list:
    """Every public profile, newest first (what recruiters see)."""
    from . import hire

    rows = db.select("profiles", {"select": "id,profile,learning_graph", "is_public": "eq.true", "order": "points.desc"})
    out = []
    for r in rows:
        try:
            profile = UserProfile.model_validate(r["profile"] or {})
            learning = SkillGraph.model_validate(r["learning_graph"]) if r.get("learning_graph") else None
            out.append(hire.Candidate(r["id"], profile, learning))
        except Exception:
            continue  # one malformed profile shouldn't take the site down
    return out


class Public:
    """The hiring site's view: anonymous, public profiles only."""

    def __init__(self, db: Supabase):
        self.db = db

    def candidates(self, today: date):
        return public_candidates(self.db)


def _mtime(ts: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
