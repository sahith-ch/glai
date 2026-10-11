"""Two separate apps, each a JSON API plus its own built React frontend:

    learner  (python main.py web,  port 8001, learner-web/dist)  - the roadmap dashboard; desktop app later
             GET /api/roadmaps            roadmap files in the folder
             GET /api/roadmaps/<file>     one roadmap: schedule, today, milestones and tasks
             GET /api/me                  the local learner's profile: stats + skill graph
             GET /api/stats               daily activity for the stats page
             GET /api/today               today's quests across every roadmap (multi-skill)
             GET /api/arena               practice challenges + upcoming quests to work ahead on
             POST /api/arena/challenge    {roadmap, difficulty}: write a new challenge -> {job}
             POST /api/arena/tests        {id}: write + validate a challenge's tests -> {job}
             POST /api/arena/submit       {id, source, branch?, subdir?}: grade a challenge -> {job}
             POST /api/roadmaps           start generating a roadmap (background job) -> {job}
             GET /api/jobs/<id>           a background job's live progress (+ result)
             POST /api/tests              {roadmap, task_id}: write + validate a task's tests -> {job}
             POST /api/submit             {roadmap, task_id, source, branch?, subdir?, spent?}: evaluate -> {job}
             POST /api/write-ahead        {roadmap}: write any milestone that is due (retry after a failure) -> {job}
             POST /api/workspace/open     {roadmap, task_id} | {challenge}: the task's environment + files
             POST /api/workspace/save     {..., files: [{name, content}], deleted?}: write the learner's files
             POST /api/workspace/run      {..., mode: script|sql|cells|shell, ...}: run in the task folder
             GET /api/state/<name>        an app's saved state (Leveller's records) -> {data}
             GET /api/ranking             the local learner's global rank (overall + by area) and the top of the board
             GET /api/calendar            free blocks for the week and the tasks suggested for each
             POST /api/calendar/connect   -> {url}: Google sign-in (free/busy only)
             GET /api/calendar/callback   where Google returns after sign-in (a page, not JSON)
             POST /api/calendar/settings  {weekday, weekend, min_slot_minutes, buffer_minutes, horizon_days}
             POST /api/calendar/disconnect
             POST /api/state/<name>       {data}: save it (leveller/<name>.json)
    hire     (python main.py hire, port 8000, web/dist)          - the hiring site
             GET /api/search?q=...        candidates ranked by acquired skills (semantic search)
             GET /api/candidates/<id>     one candidate profile, with their global ranking
             GET /api/leaderboard?area=   everyone ranked by points, overall or in one area

Data is re-read on every request, so pages always reflect the latest `done` / `fail` / `submit`.

Several endpoints run code on this machine (grading, workspaces), so the server is only for the apps that
belong to it: it listens on 127.0.0.1, and requests a browser marks as coming from another site are refused
(see _origin_ok). GLAI_ORIGINS adds origins, comma-separated.
"""

import html
import json
import mimetypes
import os
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, unquote, urlparse

from . import app_state, arena, calendar_plan, cloud, dashboard, execution, gcal, hire, jobs, ranking, submissions, workspace

PROJECT = Path(__file__).resolve().parent.parent
LEARNER_DIST = PROJECT / "learner-web" / "dist"
HIRE_DIST = PROJECT / "web" / "dist"  # the hiring app

NotFound = (404, {"error": "not found"})
LEARNER_PORT = 8001  # set by serve_learner; Google's sign-in returns to 127.0.0.1 on this port
CALLBACK_PATH = "/api/calendar/callback"


def _today() -> date:
    """date.today(), or GLAI_TODAY=YYYY-MM-DD to preview another day (testing / demos)."""
    forced = os.environ.get("GLAI_TODAY")
    return date.fromisoformat(forced) if forced else date.today()


# The learner app, its dev server, and Leveller (dev servers and the Tauri shell).
APP_ORIGINS = {
    "http://127.0.0.1:8001", "http://localhost:8001", "http://127.0.0.1:8000", "http://localhost:8000",
    "http://127.0.0.1:5174", "http://localhost:5174",
    "http://localhost:1420", "http://127.0.0.1:1420", "http://localhost:1430", "http://127.0.0.1:1430",
    "tauri://localhost", "http://tauri.localhost", "https://tauri.localhost",
}


def _origin_ok(headers) -> bool:
    """Refuse requests a browser sent on behalf of another site.

    A browser always sends Origin on cross-site POSTs, and Sec-Fetch-Site on modern requests; a page elsewhere
    on the web could otherwise POST to 127.0.0.1 and make this machine run code. Requests with neither header
    (curl, scripts, the CLI) are not from a browser page and are allowed.
    """
    allowed = APP_ORIGINS | {o.strip() for o in os.environ.get("GLAI_ORIGINS", "").split(",") if o.strip()}
    origin = headers.get("Origin")
    if origin:
        return origin in allowed
    return headers.get("Sec-Fetch-Site") in (None, "same-origin", "none")


def _workspace_target(body: dict) -> dict:
    return {"roadmap": str(body.get("roadmap") or "") or None, "task_id": str(body.get("task_id") or "") or None,
            "challenge": str(body.get("challenge") or "") or None}


def learner_api(root: Path, method: str, path: str, query: dict, body: dict) -> tuple[int, dict]:
    """One request for one learner. `root` is their folder: a directory locally, their rows when deployed
    (supa.Folder) - which is also who owns any background job this request starts."""
    token = jobs.current_owner.set(getattr(root, "owner", None))
    try:
        if execution.runs_code(path) and not execution.allowed():
            return 503, execution.REFUSAL
        if path == "/api/cloud" or path.startswith("/api/cloud/"):
            return cloud.api(root, method, path, body)
        status, payload = _learner_api(root, method, path, query, body)
        if method == "POST" and status < 400:
            cloud.changed(root)  # signed in to GLAI Hire: push this change (a graded submission, a new roadmap)
        return status, payload
    finally:
        jobs.current_owner.reset(token)


def _public_url() -> str:
    """Where Google returns after calendar sign-in: this machine locally, the public address when deployed."""
    return os.environ.get("GLAI_PUBLIC_URL", "").rstrip("/") or f"http://127.0.0.1:{LEARNER_PORT}"


def _learner_api(root: Path, method: str, path: str, query: dict, body: dict) -> tuple[int, dict]:
    today = _today()
    if path == "/api/ranking":
        learners = [ranking.Learner(c.id, c.profile) for c in hire.load_candidates(root, today)]
        board = ranking.leaderboard(learners, limit=10)
        me = ranking.rank_all(learners).get("me")
        if me and hasattr(root, "record_rank"):  # deployed: keep a daily snapshot for rank history
            root.record_rank({**me["overall"]})
        return 200, {"me": me, "leaderboard": board}
    if path == "/api/ranking/history":
        return 200, {"history": root.rank_history() if hasattr(root, "rank_history") else []}
    if path == "/api/calendar" and method == "GET":
        return 200, calendar_plan.calendar_payload(root, today)
    if method == "POST" and path.startswith("/api/calendar/"):
        try:
            if path == "/api/calendar/connect":
                owner = {"id": root.owner, "token": root.db.token} if hasattr(root, "db") else None
                return 200, {"url": gcal.start(f"{_public_url()}{CALLBACK_PATH}", owner)}
            if path == "/api/calendar/settings":
                return 200, {"settings": calendar_plan.save_settings(root, body)}
            if path == "/api/calendar/disconnect":
                gcal.disconnect(root)
                return 200, {"connected": False}
        except (gcal.CalendarError, ValueError, TypeError) as e:
            return 400, {"error": str(e)}
        return NotFound
    if path.startswith("/api/state/"):
        name = unquote(path[len("/api/state/"):])
        try:
            if method == "POST":
                if "data" not in body:
                    return 400, {"error": "Send {data}."}
                return 200, app_state.save(root, name, body["data"])
            return 200, app_state.load(root, name)
        except app_state.StateError as e:
            return 400, {"error": str(e)}
    if method == "POST" and path.startswith("/api/workspace/"):
        target = _workspace_target(body)
        try:
            if path == "/api/workspace/open":
                return 200, workspace.open_workspace(root, **target)
            if path == "/api/workspace/save":
                files = body.get("files") or []
                if not isinstance(files, list):
                    return 400, {"error": "files must be a list"}
                return 200, workspace.save_files(root, files, body.get("deleted") or [], **target)
            if path == "/api/workspace/run":
                return 200, workspace.run(root, body, **target)
        except (workspace.WorkspaceError, arena.ArenaError) as e:
            return 400, {"error": str(e)}
        return NotFound
    if method == "POST" and path == "/api/roadmaps":
        try:
            form = jobs.parse_form(body)
        except (ValueError, TypeError) as e:
            return 400, {"error": str(e)}
        return 202, {"job": jobs.start(root, form)}
    if method == "POST" and path in ("/api/tests", "/api/submit"):
        file = next((p for p in dashboard.roadmap_files(root) if p.stem == str(body.get("roadmap", ""))), None)
        task_id = str(body.get("task_id", "")).strip()
        if not file or not task_id:
            return 400, {"error": "Which roadmap and task?"}
        if path == "/api/tests":
            return 202, {"job": jobs.run("tests", task_id, lambda ev: submissions.prepare_tests(file, task_id, ev))}
        source = str(body.get("source", "")).strip()
        if not source:
            return 400, {"error": "Give a folder path or a git repo URL."}
        try:
            spent = float(body["spent"]) if body.get("spent") not in (None, "") else None
        except (TypeError, ValueError):
            return 400, {"error": "Hours spent must be a number."}
        branch = str(body.get("branch") or "").strip() or None
        subdir = str(body.get("subdir") or "").strip() or None
        return 202, {"job": jobs.run("submit", task_id, lambda ev: submissions.submit(
            file, task_id, source, branch, subdir, spent, today, on_event=ev))}
    if method == "POST" and path == "/api/write-ahead":
        file = next((p for p in dashboard.roadmap_files(root) if p.stem == str(body.get("roadmap", ""))), None)
        if not file:
            return 400, {"error": "Which roadmap?"}
        return 202, {"job": jobs.run("write", file.stem, lambda ev: submissions.write_due(file, today, ev))}
    if path.startswith("/api/jobs/"):
        job = jobs.get(path[len("/api/jobs/"):])
        return (200, job) if job else NotFound
    if path == "/api/arena":
        return 200, dashboard.arena_payload(root, today)
    if method == "POST" and path == "/api/arena/challenge":
        roadmap, difficulty = str(body.get("roadmap", "")), str(body.get("difficulty", "medium"))
        return 202, {"job": jobs.run("challenge", roadmap, lambda ev: arena.new_challenge(root, roadmap, difficulty, ev))}
    if method == "POST" and path in ("/api/arena/tests", "/api/arena/submit"):
        cid = str(body.get("id", "")).strip()
        if not cid:
            return 400, {"error": "Which challenge?"}
        if path == "/api/arena/tests":
            return 202, {"job": jobs.run("tests", cid, lambda ev: arena.prepare_tests(root, cid, ev))}
        source = str(body.get("source", "")).strip()
        if not source:
            return 400, {"error": "Give a folder path or a git repo URL."}
        branch = str(body.get("branch") or "").strip() or None
        subdir = str(body.get("subdir") or "").strip() or None
        return 202, {"job": jobs.run("submit", cid, lambda ev: arena.submit(root, cid, source, branch, subdir, today, ev))}
    if path == "/api/today":
        return 200, dashboard.today_payload(root, today)
    if path == "/api/stats":
        return 200, dashboard.stats_payload(root, today)
    if path == "/api/roadmaps":
        return 200, {"roadmaps": dashboard.roadmap_list(root)}
    if path.startswith("/api/roadmaps/"):
        name = unquote(path[len("/api/roadmaps/"):])
        file = next((p for p in dashboard.roadmap_files(root) if p.stem == name), None)
        return (200, dashboard.roadmap_payload(file, today)) if file else NotFound
    if path == "/api/me":
        return 200, dashboard.me_payload(root, today)
    return NotFound


def hire_api(root: Path, method: str, path: str, query: dict, body: dict) -> tuple[int, dict]:
    today = _today()
    if path == "/api/search":
        candidates = hire.load_candidates(root, today)
        payload = hire.search_payload(candidates, query.get("q", [""])[0])
        standings = ranking.rank_all([ranking.Learner(c.id, c.profile) for c in candidates])
        for r in payload["results"]:
            o = standings[r["id"]]["overall"]
            r["rank"] = {"rank": o["rank"], "total": o["total"], "tier": o["tier"], "top_percent": o["top_percent"]}
        return 200, payload
    if path == "/api/leaderboard":
        learners = [ranking.Learner(c.id, c.profile) for c in hire.load_candidates(root, today)]
        try:
            return 200, ranking.leaderboard(learners, query.get("area", [""])[0] or None)
        except ValueError as e:
            return 400, {"error": str(e)}
    if path.startswith("/api/candidates/"):
        cid = unquote(path[len("/api/candidates/"):])
        candidates = hire.load_candidates(root, today)
        found = next((c for c in candidates if c.id == cid), None)
        if not found:
            return NotFound
        standings = ranking.rank_all([ranking.Learner(c.id, c.profile) for c in candidates])
        return 200, {**hire.candidate_payload(found), "ranking": standings[cid]}
    return NotFound


class _Server(ThreadingHTTPServer):
    # http.server sets SO_REUSEADDR, which on Windows lets a second server bind a port that is already taken:
    # both run, requests go to either, and an old copy keeps answering with old code. Refuse instead.
    allow_reuse_address = os.name != "nt"


def serve(root: Path, port: int, dist: Path, api: Callable, name: str, open_path: str = "/", open_browser: bool = True) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            if url.path == CALLBACK_PATH and api is learner_api:
                # Google's redirect is cross-site by nature; the single-use state it carries is the check
                return self._calendar_callback(parse_qs(url.query))
            if url.path.startswith("/api/") and not _origin_ok(self.headers):
                return self._send(403, b'{"error": "requests from other sites are not allowed"}', "application/json")
            if url.path.startswith("/api/"):
                return self._api("GET", url, {})
            return self._static(url.path)

        def do_POST(self):
            url = urlparse(self.path)
            if not _origin_ok(self.headers):
                return self._send(403, b'{"error": "requests from other sites are not allowed"}', "application/json")
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except (ValueError, json.JSONDecodeError):
                return self._send(400, b'{"error": "invalid JSON"}', "application/json")
            return self._api("POST", url, body if isinstance(body, dict) else {})

        def _api(self, method: str, url, body: dict):
            try:
                code, payload = api(root, method, url.path, parse_qs(url.query), body)
            except Exception as e:  # a broken file shouldn't kill the server
                code, payload = 500, {"error": f"{type(e).__name__}: {e}"}
            self._send(code, json.dumps(payload).encode("utf-8"), "application/json")

        def _static(self, path: str):
            if not dist.exists():
                msg = f"Frontend not built yet: run `npm install && npm run build` in {dist.parent.name}/"
                return self._send(503, msg.encode("utf-8"), "text/plain")
            file = (dist / path.lstrip("/")).resolve()
            if not file.is_file() or dist not in file.parents:
                file = dist / "index.html"  # client-side routes load the app
            self._send(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or "application/octet-stream")

        def _calendar_callback(self, query: dict):
            q = {k: v[0] for k, v in query.items()}
            if q.get("error"):
                ok, message = False, f"Google did not connect the calendar ({q['error']})."
            else:
                try:
                    gcal.finish(root, q.get("state", ""), q.get("code", ""))
                    ok, message = True, "Google Calendar is connected. GLAI sees only when you are free or busy."
                except gcal.CalendarError as e:
                    ok, message = False, str(e)
            page = (f"<!doctype html><meta charset=utf-8><title>GLAI calendar</title><body style='font-family:system-ui;"
                    f"background:#101411;color:#edf1ed;display:grid;place-items:center;height:90vh'><div><h1 style='color:"
                    f"{'#c5ed9a' if ok else '#f5a6a6'}'>{'Connected' if ok else 'Not connected'}</h1><p>{html.escape(message)}</p>"
                    f"<p>You can close this tab and go back to the app.</p></div>")
            self._send(200 if ok else 400, page.encode("utf-8"), "text/html")

        def _send(self, code: int, data: bytes, ctype: str):
            self.send_response(code)
            text = ctype.startswith(("text/", "application/j"))
            self.send_header("Content-Type", f"{ctype}; charset=utf-8" if text else ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):  # keep the terminal quiet
            pass

    try:
        server = _Server(("127.0.0.1", port), Handler)
    except OSError:
        raise SystemExit(f"{name}: port {port} is already in use - is it already running? Stop it first, "
                         f"or start this one with --port.")
    url = f"http://127.0.0.1:{port}{open_path}"
    print(f"{name} running at {url}  (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


def serve_learner(root: Path, port: int = 8001, open_path: str = "/", open_browser: bool = True) -> None:
    global LEARNER_PORT
    LEARNER_PORT = port
    cloud.start(root)  # signed in to GLAI Hire: keep the account in sync while the app runs
    serve(root, port, LEARNER_DIST, learner_api, "GLAI Learn", open_path, open_browser)


def serve_hire(root: Path, port: int = 8000, open_browser: bool = True) -> None:
    import threading

    from . import vector

    # load the search models now, so the first search doesn't wait seconds for them
    threading.Thread(target=vector.get, daemon=True).start()
    serve(root, port, HIRE_DIST, hire_api, "GLAI Hire", "/", open_browser)
