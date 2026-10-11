"""Submitting work for a task: tests -> run -> review -> plan update.

Shared by the CLI (`python main.py tests / submit`) and the learner app, so both behave the same.
Progress is reported through `on_event(kind, message, data)`.
"""

from datetime import date
from pathlib import Path
from typing import Callable

from . import evaluate as ev
from . import progress, user
from .schemas import Milestone, Plan, Task

SUBMIT_FORMAT = "At the top of a folder or repo, or in a subfolder named {id}/ or task-{id}/. Include any data files it reads."

Event = Callable[[str, str, dict], None]


class SubmissionError(Exception):
    pass


def context(plan: Plan, m: Milestone) -> str:
    p = plan.profile
    return (
        f"Skill: {plan.roadmap.skill} | learner level: {p.get('current_level', 'beginner')} | milestone: {m.title}"
        + (f" | interests: {', '.join(p['interests'])}" if p.get("interests") else "")
    )


def tests_view(t: Task) -> dict:
    """What a learner needs before submitting: files, layout, and the (graded) checks."""
    tests = t.tests
    return {
        "task_id": t.id,
        "required_files": tests.required_files if tests else ev.required_files(t),
        "format": SUBMIT_FORMAT.format(id=t.id),
        "ready": tests is not None,
        "checks": [
            {"test": c.test, "verifies": c.verifies, "quote": c.quote}
            for c in (tests.checks if tests else [])
            if c.test not in tests.deselected
        ],
        "dropped": len(tests.deselected) if tests else 0,
    }


def write_ahead(plan: Plan, path: Path, on_event: Event = lambda *a: None) -> list[str]:
    """Write tasks for the current milestone if missing, and the next one when the current is nearly done.

    The CLI does this on every command (main.write_ahead); the learner app does it after each submission,
    so a learner who only uses the app still gets the next milestone - written from what they found hard.
    Returns the titles of the milestones written. A failure (no key, network) is reported, not raised:
    the submission already counted, and the next one retries.
    """
    from .graph import fill_milestone

    written = []
    for i in progress.milestones_to_write(plan):
        m = plan.roadmap.milestones[i]
        on_event("write_next", f"Writing tasks for milestone {i + 1}: {m.title}", {"milestone": i + 1})
        try:
            plan.profile["interests"] = user.load(path.parent).interests
            fill_milestone(plan, i)
        except Exception as e:
            on_event("write_failed", f"Couldn't write milestone {i + 1} now ({type(e).__name__}: {e})", {"milestone": i + 1})
            break
        written.append(m.title)
    return written


def write_due(path: Path, today: date, on_event: Event = lambda *a: None) -> dict:
    """The learner app's retry: write whatever milestone is due, save, and say what was written."""
    plan = progress.load(path)
    written = write_ahead(plan, path, on_event)
    if written:
        progress.save(plan, path)
        user.rebuild(path.parent, today, extra_plan=path)
    elif progress.milestones_to_write(plan):
        raise SubmissionError("Couldn't write the next milestone right now. Check the model settings in .env and try again.")
    return {"file": path.stem, "written": written}


def _llm():
    from .graph import get_llm

    return get_llm()


def prepare_tests(path: Path, task_id: str, on_event: Event = lambda *a: None) -> dict:
    """Generate (and validate) a task's tests if needed, save them, and describe them."""
    plan = progress.load(path)
    m, t = progress.find(plan, task_id)
    if t.tests is None:
        on_event("tests", "Writing tests and checking them against a reference solution", {})
        try:
            ev.ensure_tests(t, context(plan, m), _llm())
        except ev.EvaluationError as e:
            raise SubmissionError(str(e)) from e
        progress.save(plan, path)
    return tests_view(t)


def submit(
    path: Path,
    task_id: str,
    source: str,
    branch: str | None = None,
    subdir: str | None = None,
    spent: float | None = None,
    today: date | None = None,
    force: bool = False,
    on_event: Event = lambda *a: None,
) -> dict:
    """Evaluate a submission and update the plan. Returns the evaluation plus what changed in the plan."""
    today = today or date.today()
    plan = progress.load(path)
    m, t = progress.find(plan, task_id)
    if t.status == "done" and not force:
        raise SubmissionError(f"{t.id} is already done.")
    llm = _llm()
    ctx = context(plan, m)

    if t.tests is None:
        on_event("tests", "Writing tests and checking them against a reference solution", {})
        try:
            ev.ensure_tests(t, ctx, llm)
        except ev.EvaluationError as e:
            raise SubmissionError(str(e)) from e
        progress.save(plan, path)  # keep the tests even if the submission can't be fetched

    on_event("run", "Running your code against the tests, then reviewing it", {})
    try:
        result = ev.evaluate(t, source, ctx, llm, branch, subdir)
    except ev.EvaluationError as e:
        raise SubmissionError(str(e)) from e

    t.evaluations.append(result)
    progress.log_event(plan, "submitted", t.id, today, passed=result.passed,
                       tests=f"{result.tests_passed}/{result.tests_total}",
                       score=result.review.score if result.review else None)

    outcome: dict = {"action": "feedback"}
    if result.passed and t.status != "done":
        progress.mark_done(plan, t.id, today, spent)
        outcome = {"action": "done", "points": t.points, "max_points": progress.max_points(t)}
    elif not result.passed and spent:
        on_event("adapt", "Adapting your plan", {})
        action, added = progress.mark_failed(plan, t.id, spent, today, llm=llm)
        outcome = {"action": action, "added": [{"id": a.id, "title": a.title, "hours": a.estimated_hours} for a in added]}

    if outcome["action"] != "feedback":  # the plan moved on: the next milestone may be due
        if written := write_ahead(plan, path, on_event):
            outcome["written"] = written

    progress.save(plan, path)
    user.rebuild(path.parent, today, extra_plan=path)
    on_event("saved", "Saved", {})
    return {"task_id": t.id, "evaluation": result.model_dump(), "outcome": outcome}
