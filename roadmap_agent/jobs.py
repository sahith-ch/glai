"""Background roadmap generation for the learner app (takes ~2 minutes, so it can't block a request)."""

import contextvars
import threading
import uuid
from datetime import date, datetime
from pathlib import Path

from .create import create_roadmap

_jobs: dict[str, dict] = {}
# Who is asking, set per request by the server; jobs remember it so one learner can't read another's job.
current_owner: contextvars.ContextVar[str | None] = contextvars.ContextVar("glai_job_owner", default=None)
_lock = threading.Lock()


def run(kind: str, label: str, fn) -> str:
    """Run fn(on_event) -> result dict in a background thread; poll it with get(job_id)."""
    job_id = uuid.uuid4().hex
    job = {"id": job_id, "owner": current_owner.get(), "kind": kind, "status": "running", "skill": label, "events": [], "file": None,
           "result": None, "error": None, "started": datetime.now().isoformat(timespec="seconds")}
    with _lock:
        _jobs[job_id] = job

    def on_event(kind_: str, message: str, data: dict) -> None:
        with _lock:
            job["events"].append({"kind": kind_, "message": message, "data": data,
                                  "at": datetime.now().isoformat(timespec="seconds")})

    def work() -> None:
        try:
            result = fn(on_event)
            with _lock:
                job.update(status="done", result=result, file=(result or {}).get("file"))
        except Exception as e:  # report it to the page instead of dying silently
            expected = type(e).__name__ in ("SubmissionError", "EvaluationError", "ArenaError", "ValueError")
            with _lock:
                job.update(status="error", error=str(e) if expected else f"{type(e).__name__}: {e}")

    threading.Thread(target=work, daemon=True).start()
    return job_id


def start(root: Path, form: dict) -> str:
    """Generate a new roadmap from the New roadmap form."""
    def make(on_event):
        path, _, _ = create_roadmap(
            root, form["skill"], level=form["level"], weekly_hours=form["weekly_hours"],
            target_weeks=form["target_weeks"], goal=form["goal"], new_skills=form["known_skills"],
            days_per_week=form["days_per_week"], start=form["start"], on_event=on_event,
        )
        return {"file": path.stem}
    return run("roadmap", form["skill"], make)


def get(job_id: str) -> dict | None:
    """The job, if it exists and belongs to the current owner (locally there is one owner: None)."""
    with _lock:
        job = _jobs.get(job_id)
        if not job or job.get("owner") != current_owner.get():
            return None
        return {k: v for k, v in job.items() if k != "owner"} | {"events": list(job["events"])}


def parse_form(body: dict) -> dict:
    """Validate the New roadmap form. Raises ValueError with a message for the user."""
    skill = str(body.get("skill", "")).strip()
    if not skill:
        raise ValueError("Tell us what you want to learn.")

    def num(key, lo, hi, default=None):
        v = body.get(key)
        if v in (None, ""):
            return default
        v = int(v)
        if not lo <= v <= hi:
            raise ValueError(f"{key.replace('_', ' ')} must be between {lo} and {hi}.")
        return v

    level = body.get("level") or "beginner"
    if level not in ("beginner", "intermediate", "advanced"):
        raise ValueError("Level must be beginner, intermediate or advanced.")
    start = body.get("start")
    return {
        "skill": skill[:120],
        "level": level,
        "weekly_hours": num("weekly_hours", 1, 60, 8),
        "days_per_week": num("days_per_week", 1, 7, 7),
        "target_weeks": num("target_weeks", 1, 104),
        "goal": str(body.get("goal", "")).strip()[:200],
        "known_skills": [s.strip() for s in str(body.get("known_skills", "")).split(",") if s.strip()][:30],
        "start": date.fromisoformat(start) if start else None,
    }
