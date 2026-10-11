"""Arena: practice challenges on demand, outside the daily schedule.

A challenge is generated from what the learner has already covered in a roadmap (finished tasks and
the current milestone's topics), graded by the same evaluation engine as roadmap quests, and earns
points by difficulty. It never changes the roadmap schedule or the skill graph.
Stored in arena.json next to the roadmaps.
"""

from datetime import date, datetime
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from . import evaluate as ev
from . import progress
from .quality import SPECIFIC_RULES, vague_issues
from .schemas import Task, TaskDraft

ARENA_FILE = "arena.json"
MAX_TRIES = 3

Event = Callable[[str, str, dict], None]


class Challenge(BaseModel):
    id: str  # "A1", "A2", ...
    created: str
    roadmap: str  # roadmap file stem it was drawn from
    skill: str
    based_on: list[str] = Field(default_factory=list)  # topics it practises
    task: Task


class Arena(BaseModel):
    challenges: list[Challenge] = Field(default_factory=list)


class ArenaError(Exception):
    pass


def load(root: Path) -> Arena:
    p = root / ARENA_FILE
    return Arena.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else Arena()


def save(arena: Arena, root: Path) -> None:
    (root / ARENA_FILE).write_text(arena.model_dump_json(indent=2), encoding="utf-8")


def find(arena: Arena, cid: str) -> Challenge:
    for c in arena.challenges:
        if c.id == cid:
            return c
    raise ArenaError(f"No challenge {cid!r}.")


def points_earned(arena: Arena) -> int:
    return sum(progress.earned(c.task) for c in arena.challenges)


def _covered(roadmap_path: Path) -> tuple[str, dict, list[str], list[str]]:
    """(skill, plan profile, finished task titles, topics covered or in progress)."""
    plan = progress.load(roadmap_path)
    done = [t for _, t in progress.all_tasks(plan) if t.status == "done" and t.kind == "core"]
    topics = list(dict.fromkeys(t.topic for t in done if t.topic and t.topic != "Skills check"))
    cur = progress.current_milestone(plan)
    if cur:  # what they're learning right now is fair game too
        topics += [t.topic for t in cur.tasks if t.topic and t.topic != "Skills check" and t.topic not in topics]
    return plan.roadmap.skill, plan.profile, [t.title for t in done][-15:], list(dict.fromkeys(topics))[:12]


def new_challenge(root: Path, roadmap_file: str, difficulty: str, on_event: Event = lambda *a: None) -> dict:
    from .graph import ask, get_llm

    if difficulty not in progress.BASE_POINTS:
        raise ArenaError("Difficulty must be easy, medium or hard.")
    path = root / f"{roadmap_file}.json"
    if not path.exists():
        raise ArenaError("Unknown roadmap.")
    skill, profile, done_titles, topics = _covered(path)
    if not topics:
        raise ArenaError("This roadmap has no tasks yet - open it once so its first milestone gets written.")
    arena = load(root)
    previous = [c.task.title for c in arena.challenges if c.roadmap == roadmap_file][-15:]

    on_event("write", "Writing a challenge from what you've covered", {})
    llm, feedback, draft = get_llm(), "", None
    for _ in range(MAX_TRIES):
        draft = ask(
            llm,
            TaskDraft,
            "You set ONE practice challenge for a learner, outside their roadmap schedule.\n\n"
            f"Skill: {skill} | level: {profile.get('current_level', 'beginner')}"
            + (f" | interests: {', '.join(profile['interests'])}" if profile.get("interests") else "")
            + f"\nTopics covered so far: {'; '.join(topics)}\n"
            f"Tasks already finished (do NOT repeat them): {'; '.join(done_titles) or 'none yet'}\n"
            f"Earlier arena challenges (do NOT repeat): {'; '.join(previous) or 'none'}\n\n"
            "Rules:\n"
            f"- Difficulty: {difficulty} (easy: one concept; medium: combines two or three; hard: a small realistic problem).\n"
            "- Use ONLY the topics covered so far - it must be solvable with what they know.\n"
            "- A fresh scenario, not a copy of a finished task. 0.5-2 hours.\n"
            "- The deliverable is ONE code file (.py, or .sql for SQL skills) with input -> expected output examples.\n\n"
            f"{SPECIFIC_RULES}{feedback}",
        )
        if draft is None:
            feedback = "\n\nYour previous answer was not valid JSON."
            continue
        issues = vague_issues([draft])
        if not issues:
            break
        feedback = "\n\nYour previous challenge was rejected:\n- " + "\n- ".join(issues)
    if draft is None:
        raise ArenaError("Couldn't write a challenge right now - try again.")

    n = 1 + max([int(c.id[1:]) for c in arena.challenges if c.id[1:].isdigit()] or [0])
    task = Task(
        id=f"A{n}", **draft.model_dump(exclude={"depends_on", "resources", "difficulty"}),
        difficulty=difficulty, topic="Arena",
    )
    task.estimated_hours = min(max(task.estimated_hours, 0.5), 2)
    ch = Challenge(id=task.id, created=datetime.now().isoformat(timespec="seconds"), roadmap=roadmap_file,
                   skill=skill, based_on=topics, task=task)
    arena.challenges.append(ch)
    save(arena, root)
    on_event("saved", "Challenge ready", {})
    return {"id": ch.id}


def _context(c: Challenge) -> str:
    return f"Skill: {c.skill} | an arena practice challenge using: {', '.join(c.based_on[:6])}"


def prepare_tests(root: Path, cid: str, on_event: Event = lambda *a: None) -> dict:
    from .graph import get_llm
    from .submissions import tests_view

    arena = load(root)
    c = find(arena, cid)
    if c.task.tests is None:
        on_event("tests", "Writing tests and checking them against a reference solution", {})
        try:
            ev.ensure_tests(c.task, _context(c), get_llm())
        except ev.EvaluationError as e:
            raise ArenaError(str(e)) from e
        save(arena, root)
    return tests_view(c.task)


def submit(root: Path, cid: str, source: str, branch: str | None = None, subdir: str | None = None,
           today: date | None = None, on_event: Event = lambda *a: None) -> dict:
    """Grade a challenge. Passing (the first time) awards points; failing just gives feedback."""
    from .graph import get_llm

    today = today or date.today()
    arena = load(root)
    c = find(arena, cid)
    llm = get_llm()
    if c.task.tests is None:
        on_event("tests", "Writing tests and checking them against a reference solution", {})
        try:
            ev.ensure_tests(c.task, _context(c), llm)
        except ev.EvaluationError as e:
            raise ArenaError(str(e)) from e
        save(arena, root)
    on_event("run", "Running your code against the tests, then reviewing it", {})
    try:
        result = ev.evaluate(c.task, source, _context(c), llm, branch, subdir)
    except ev.EvaluationError as e:
        raise ArenaError(str(e)) from e
    c.task.evaluations.append(result)
    c.task.attempts += 1
    outcome: dict = {"action": "feedback"}
    if not result.passed and c.task.status != "done":
        c.task.fail_count += 1  # each failed attempt costs 25% of the eventual points (min 50%)
    if result.passed and c.task.status != "done":
        c.task.status = "done"
        c.task.points = progress.award(c.task)
        outcome = {"action": "done", "points": c.task.points, "max_points": progress.max_points(c.task)}
    save(arena, root)
    on_event("saved", "Saved", {})
    return {"task_id": c.id, "evaluation": result.model_dump(), "outcome": outcome}
