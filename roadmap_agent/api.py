"""The deployable backend: GLAI's APIs for many learners, on Supabase.

    uvicorn roadmap_agent.api:app --host 0.0.0.0 --port 8080

Same endpoints as the local servers (server.py), so Leveller and both web apps talk to it unchanged:

    /api/...         the learner API. Needs `Authorization: Bearer <Supabase access token>`; every request
                     runs as that learner against their own rows (supa.Folder), so row-level security in
                     Postgres - not this file - keeps learners apart.
    /hire/api/...    the hiring API. Public; reads only public profiles.
    /api/config      what a client needs to sign in (Supabase URL + publishable key). Public.
    /api/health      for the host's health check.

Learners sign up and sign in with Supabase Auth directly (email + password); this backend only verifies the
token. Code execution is off unless GLAI_CODE_EXECUTION=local (see execution.py).

Environment:
    SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY   the project (both public values)
    GLAI_ALLOWED_ORIGINS                     comma-separated web origins allowed to call the API
    GLAI_PUBLIC_URL                          this backend's public address (Google Calendar sign-in returns here)
    LLM_PROVIDER / LLM_MODEL / *_API_KEY, TAVILY_API_KEY   as for local GLAI
"""

import html
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

load_dotenv()
os.environ.setdefault("GLAI_CODE_EXECUTION", "off")  # a shared server must not run learners' code

from . import execution, gcal, server, supa  # noqa: E402  (after the environment is settled)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
ORIGINS = [o.strip() for o in os.environ.get("GLAI_ALLOWED_ORIGINS", "").split(",") if o.strip()] + sorted(server.APP_ORIGINS)
LEARNER_DIST = server.LEARNER_DIST

app = FastAPI(title="GLAI", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_methods=["GET", "POST", "OPTIONS"],
                   allow_headers=["Authorization", "Content-Type"], max_age=600)


def _configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_KEY)


def _error(status: int, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": message, **extra}, status_code=status)


async def _body(request: Request) -> dict:
    if request.method != "POST":
        return {}
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _query(request: Request) -> dict:
    out: dict[str, list[str]] = {}
    for k, v in request.query_params.multi_items():
        out.setdefault(k, []).append(v)
    return out


# ---------------------------------------------------------------- public


@app.get("/api/health")
def health():
    return {"ok": True, "database": _configured(), "code_execution": execution.allowed()}


@app.get("/api/config")
def config():
    """Public values a client needs to sign in against this backend's Supabase project."""
    return {"supabase_url": SUPABASE_URL, "supabase_key": SUPABASE_KEY, "code_execution": execution.allowed()}


@app.get(server.CALLBACK_PATH, response_class=HTMLResponse)
def calendar_callback(state: str = "", code: str = "", error: str = ""):
    """Google returns here after calendar sign-in. The state names the learner who started it."""
    owner = gcal.pending_owner(state)
    ok, message = False, "This sign-in link expired or was already used. Start again from the app."
    if error:
        message = f"Google did not connect the calendar ({error})."
    elif owner:
        try:
            gcal.finish(supa.Folder(supa.Supabase(SUPABASE_URL, SUPABASE_KEY, owner["token"]), owner["id"]), state, code)
            ok, message = True, "Google Calendar is connected. GLAI sees only when you are free or busy."
        except (gcal.CalendarError, supa.SupabaseError) as e:
            message = str(e)
    page = (f"<!doctype html><meta charset=utf-8><title>GLAI calendar</title><body style='font-family:system-ui;"
            f"display:grid;place-items:center;height:90vh'><div><h1>{'Connected' if ok else 'Not connected'}</h1>"
            f"<p>{html.escape(message)}</p><p>You can close this tab and go back to the app.</p></div>")
    return HTMLResponse(page, status_code=200 if ok else 400)


@app.api_route("/hire/api/{rest:path}", methods=["GET"])
async def hire(rest: str, request: Request):
    if not _configured():
        return _error(503, "The database is not configured (SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY).")
    public = supa.Public(supa.Supabase(SUPABASE_URL, SUPABASE_KEY))
    code, payload = await run_in_threadpool(server.hire_api, public, request.method, f"/api/{rest}", _query(request), {})
    return JSONResponse(payload, status_code=code)


# ---------------------------------------------------------------- signed in


@app.api_route("/api/{rest:path}", methods=["GET", "POST"])
async def learner(rest: str, request: Request):
    if not _configured():
        return _error(503, "The database is not configured (SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY).")
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token:
        return _error(401, "Sign in first.", code="signed_out")
    try:
        user = await run_in_threadpool(supa.verify, SUPABASE_URL, SUPABASE_KEY, token)
    except supa.SupabaseError as e:
        return _error(401, str(e), code="signed_out")
    folder = supa.Folder(supa.Supabase(SUPABASE_URL, SUPABASE_KEY, token), user["id"])
    try:
        code, payload = await run_in_threadpool(server.learner_api, folder, request.method, f"/api/{rest}",
                                                _query(request), await _body(request))
    except supa.SupabaseError as e:
        return _error(502, str(e))
    except Exception as e:  # a broken record shouldn't become a bare 500 page (the local server does the same)
        return _error(500, f"{type(e).__name__}: {e}")
    return JSONResponse(payload, status_code=code)


# ---------------------------------------------------------------- the learner web app (optional)


@app.get("/{path:path}")
def learner_web(path: str):
    if not LEARNER_DIST.exists():
        return _error(404, "No web app is bundled with this backend.")
    file = (LEARNER_DIST / path).resolve()
    if not file.is_file() or LEARNER_DIST.resolve() not in file.parents:
        file = LEARNER_DIST / "index.html"  # client-side routes load the app
    return FileResponse(file)
