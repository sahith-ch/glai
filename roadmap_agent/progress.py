"""Progress tracking, day-by-day scheduling and adaptive re-ordering of roadmap tasks.

What happens to a task depends on what the learner actually did:

    skip           didn't start it      -> moved to the next study day, everything after shifts
    fail, little   spent < ENOUGH_TIME  -> not a real attempt: continue it the next study day
    fail, enough   spent >= ENOUGH_TIME -> 1st time: insert helper tasks that build what it needs
                                           again:    split it into smaller steps
                                           tasks that don't need it move ahead of the retry,
                                           tasks that depend on it wait behind it

The LLM only writes new task *content*; order and dates are computed here, so they're always valid.
"""

import math
from datetime import date, datetime, timedelta
from pathlib import Path

from .quality import SPECIFIC_RULES
from .schemas import Milestone, Plan, RemedialTasks, SplitTasks, Task, TaskDraft

LOOKAHEAD_TASKS = 2  # write the next milestone's tasks when this many are left in the current one
ENOUGH_TIME = 0.75  # share of the estimate a learner must spend for a failure to count as real
MAX_NOTES_CHARS = 2000  # research notes are context for small follow-up prompts

# lower runs first within a milestone, among tasks whose dependencies are met
_BUCKET = {"remedial": 0, "step": 0, "core": 1}


# ---------------------------------------------------------------- storage


def load(path: Path) -> Plan:
    return Plan.model_validate_json(path.read_text(encoding="utf-8"))


def save(plan: Plan, path: Path) -> None:
    path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")


# ---------------------------------------------------------------- queries


def all_tasks(plan: Plan) -> list[tuple[Milestone, Task]]:
    return [(m, t) for m in plan.roadmap.milestones for t in m.tasks]


def find(plan: Plan, task_id: str) -> tuple[Milestone, Task]:
    for m, t in all_tasks(plan):
        if t.id == task_id:
            return m, t
    raise KeyError(f"No task with id {task_id!r}. Run `status` to see ids.")


def _unfinished(m: Milestone) -> bool:
    return not m.tasks_written or any(t.status != "done" for t in m.tasks)


def current_milestone(plan: Plan) -> Milestone | None:
    """Milestones are sequential: the current one is the first with unfinished (or not yet written) tasks."""
    return next((m for m in plan.roadmap.milestones if _unfinished(m)), None)


def milestones_to_write(plan: Plan) -> list[int]:
    """Indexes of milestones whose tasks should be written now: the current one if it has none yet,
    and the next one once the current milestone is down to its last LOOKAHEAD_TASKS tasks."""
    ms = plan.roadmap.milestones
    cur = current_milestone(plan)
    if cur is None:
        return []
    i = ms.index(cur)
    out = [i] if not cur.tasks_written else []
    left = sum(t.status != "done" for t in cur.tasks) if cur.tasks_written else math.inf
    if i + 1 < len(ms) and not ms[i + 1].tasks_written and left <= LOOKAHEAD_TASKS:
        out.append(i + 1)
    return out


def milestone_hours(plan: Plan, m: Milestone) -> float:
    """Task hours, or the milestone's week budget while its tasks aren't written yet."""
    if m.tasks_written:
        return sum(t.estimated_hours for t in m.tasks)
    return (m.week_end - m.week_start + 1) * (plan.profile.get("weekly_hours") or 8)


def next_task(plan: Plan) -> Task | None:
    m = current_milestone(plan)
    if not m:
        return None
    done = {t.id for t in m.tasks if t.status == "done"}
    return next((t for t in m.tasks if t.status != "done" and set(t.depends_on) <= done), None)


def remaining_hours(t: Task) -> float:
    return 0 if t.status == "done" else max(0.5, t.estimated_hours - t.spent_hours)


def learned_skills(plan: Plan) -> list[str]:
    """What a learner has proven in this plan: fully done milestones, else fully done sub-skills."""
    out = []
    for m in plan.roadmap.milestones:
        if m.tasks and all(t.status == "done" for t in m.tasks):
            out.append(f"{m.title} ({plan.roadmap.skill})")
            continue
        topics: dict[str, bool] = {}
        for t in m.tasks:
            if t.topic and t.kind == "core":
                topics[t.topic] = topics.get(t.topic, True) and t.status == "done"
        out += [f"{name} ({plan.roadmap.skill})" for name, done in topics.items() if done and name != "Skills check"]
    return out


# ---------------------------------------------------------------- calendar


def _days_per_week(plan: Plan) -> int:
    return min(7, max(1, int(plan.profile.get("days_per_week") or 7)))


def daily_hours(plan: Plan) -> float:
    return (plan.profile.get("weekly_hours") or 8) / _days_per_week(plan)


def is_study_day(plan: Plan, d: date) -> bool:
    return d.weekday() < _days_per_week(plan)  # 5 -> Mon-Fri, 6 -> Mon-Sat, 7 -> every day


def next_study_day(plan: Plan, d: date) -> date:
    d += timedelta(days=1)
    while not is_study_day(plan, d):
        d += timedelta(days=1)
    return d


def hours_logged(plan: Plan, d: date) -> float:
    return sum(h.get("spent", 0) for h in plan.history if h.get("at", "")[:10] == d.isoformat())


def set_capacity(plan: Plan, hours_by_day: dict[date, float] | None) -> Plan:
    """Hours the learner actually has on each day (from their calendar). Days not listed keep the plan's
    even split; a busy day can be 0. Kept on the loaded plan only - never saved."""
    plan._capacity = hours_by_day
    return plan


def capacity(plan: Plan, d: date) -> float:
    """Study hours planned for day d: the calendar's figure when known, else weekly hours / study days."""
    if not is_study_day(plan, d):
        return 0.0
    known = plan._capacity
    if known is not None and d in known:
        return max(0.0, known[d])
    return daily_hours(plan)


def schedule(plan: Plan, today: date) -> dict[str, tuple[date, date]]:
    """Lay unfinished tasks onto study days from today, in plan order. task id -> (first day, last day).
    A milestone whose tasks aren't written yet is scheduled as one block under the key "M<n>".
    Each day holds `capacity(plan, day)` hours: the even split, or what the learner's calendar leaves free."""
    day = today
    cap = capacity(plan, today) - hours_logged(plan, today)
    out: dict[str, tuple[date, date]] = {}

    def advance():
        nonlocal day, cap
        day = next_study_day(plan, day)
        cap = capacity(plan, day)

    def place(key: str, need: float) -> None:
        nonlocal cap
        while cap <= 1e-9:
            advance()
        start = day
        while need > 1e-9:
            if cap <= 1e-9:
                advance()
            used = min(need, cap)
            need, cap = need - used, cap - used
        out[key] = (start, day)

    for n, m in enumerate(plan.roadmap.milestones, 1):
        if not m.tasks_written:
            place(f"M{n}", milestone_hours(plan, m))
            continue
        for t in m.tasks:
            if t.status == "done":
                continue
            if t.not_before and t.not_before > day:
                day, cap = t.not_before, capacity(plan, t.not_before)
                if not is_study_day(plan, day):
                    advance()
            place(t.id, remaining_hours(t))
    return out


def start_date(plan: Plan) -> date:
    return date.fromisoformat(plan.profile.get("start_date") or date.today().isoformat())


def projection(plan: Plan, today: date) -> dict:
    sched = schedule(plan, today)
    done_h = sum(t.estimated_hours for _, t in all_tasks(plan) if t.status == "done")
    left_h = sum(remaining_hours(t) for _, t in all_tasks(plan)) + sum(
        milestone_hours(plan, m) for m in plan.roadmap.milestones if not m.tasks_written
    )
    planned_end = start_date(plan) + timedelta(weeks=plan.roadmap.total_weeks) - timedelta(days=1)
    projected_end = max((e for _, e in sched.values()), default=today)
    return {
        "done_hours": done_h,
        "left_hours": left_h,
        "planned_end": planned_end,
        "projected_end": projected_end,
        "slip_days": (projected_end - planned_end).days,
        "projected_weeks": math.ceil(((projected_end - start_date(plan)).days + 1) / 7),
        "planned_weeks": plan.roadmap.total_weeks,
    }


def tasks_on(plan: Plan, d: date, today: date) -> list[Task]:
    sched = schedule(plan, today)
    return [t for _, t in all_tasks(plan) if t.id in sched and sched[t.id][0] <= d <= sched[t.id][1]]


# ---------------------------------------------------------------- ordering


def _rank(t: Task) -> int:
    if t.status == "failed":
        return 2  # retry after everything else that is ready
    return _BUCKET[t.kind]


def reorder(m: Milestone) -> None:
    """Stable dependency-aware sort of one milestone's unfinished tasks."""
    done = [t for t in m.tasks if t.status == "done"]
    pending = [t for t in m.tasks if t.status != "done"]
    pos = {t.id: i for i, t in enumerate(pending)}
    local = {t.id for t in m.tasks}
    placed = {t.id for t in done}

    ordered = []
    while pending:
        ready = [t for t in pending if all(d in placed or d not in local for d in t.depends_on)]
        pick = min(ready or pending, key=lambda t: (_rank(t), pos[t.id]))  # `or`: never deadlock on bad links
        ordered.append(pick)
        placed.add(pick.id)
        pending.remove(pick)
    m.tasks = done + ordered


# ---------------------------------------------------------------- updates


def _log(plan: Plan, event: str, task_id: str, today: date, **extra) -> None:
    now = datetime.now()
    at = datetime.combine(today, now.time()) if today != now.date() else now
    plan.history.append({"at": at.isoformat(timespec="seconds"), "event": event, "task": task_id, **extra})


# ---------------------------------------------------------------- points

BASE_POINTS = {"easy": 10, "medium": 20, "hard": 40}
SUPPORT_FACTOR = 0.5  # helper tasks, split steps and the skills check: catch-up / verification work
FAIL_PENALTY = 0.25  # per real failure, down to MIN_ATTEMPT_FACTOR
MIN_ATTEMPT_FACTOR = 0.5


def difficulty(t: Task) -> str:
    """The task's rating; older plans have none, so infer it: mini projects are hard, otherwise by hours."""
    if t.difficulty:
        return t.difficulty
    if "project" in (t.topic or "").lower():
        return "hard"
    return "hard" if t.estimated_hours >= 3.5 else "medium" if t.estimated_hours >= 2 else "easy"


def focus(t: Task) -> str:
    """What kind of time a task needs: deep (long uninterrupted work), practice, or light (fits a short gap).
    Tasks written before this existed get it inferred from their kind, topic and size."""
    if t.focus:
        return t.focus
    if t.kind != "core" or t.topic == "Skills check" or t.estimated_hours <= 0.75:
        return "light"
    if "project" in (t.topic or "").lower() or t.estimated_hours >= 3:
        return "deep"
    return "practice"


def _is_support(t: Task) -> bool:
    return t.kind != "core" or t.topic == "Skills check"


def max_points(t: Task) -> int:
    """Points for a perfect first-try completion."""
    return round(BASE_POINTS[difficulty(t)] * (SUPPORT_FACTOR if _is_support(t) else 1))


def award(t: Task) -> int:
    """difficulty base x task type x attempts (−25% per real failure, min 50%) x code review (0.8..1.2)."""
    attempts = max(MIN_ATTEMPT_FACTOR, 1 - FAIL_PENALTY * t.fail_count)
    review = next((e.review for e in reversed(t.evaluations) if e.review), None)
    quality = 0.8 + 0.4 * max(0, min(100, review.score)) / 100 if review else 1.0
    return round(max_points(t) * attempts * quality)


def earned(t: Task) -> int:
    """Points a task has earned: what was awarded, or (older plans) what it would have been."""
    if t.status != "done":
        return 0
    return t.points if t.points is not None else award(t)


def log_event(plan: Plan, event: str, task_id: str, today: date, **extra) -> None:
    _log(plan, event, task_id, today, **extra)


def mark_done(plan: Plan, task_id: str, today: date, spent: float | None = None) -> Task:
    m, t = find(plan, task_id)
    spent = remaining_hours(t) if spent is None else spent
    t.status = "done"
    t.attempts += 1
    t.not_before = None
    t.points = award(t)
    _log(plan, "done", task_id, today, spent=spent, points=t.points)
    reorder(m)
    return t


def skip(plan: Plan, task_id: str, today: date) -> date:
    """Learner didn't start the task: push it (and so everything after it) to the next study day."""
    _, t = find(plan, task_id)
    t.not_before = next_study_day(plan, today)
    _log(plan, "skipped", task_id, today)
    return t.not_before


def mark_failed(plan: Plan, task_id: str, spent: float, today: date, llm=None) -> tuple[str, list[Task]]:
    """Returns ("continue", []) for a short attempt, or ("helpers"|"split", added_tasks) for a real failure."""
    m, t = find(plan, task_id)
    t.spent_hours += spent
    _log(plan, "attempt", task_id, today, spent=spent)

    if t.spent_hours < ENOUGH_TIME * t.estimated_hours:
        t.not_before = next_study_day(plan, today)  # not a real attempt yet: keep going next study day
        return "continue", []

    t.status = "failed"
    t.attempts += 1
    t.fail_count += 1
    t.not_before = None
    if t.fail_count == 1:
        action, added = "helpers", _helpers(plan, m, t, llm)
    else:
        # helper work that didn't help is superseded by the smaller steps
        stale = {x.id for x in m.tasks if x.kind == "remedial" and x.status != "done" and x.id.startswith(f"{t.id}r")}
        m.tasks = [x for x in m.tasks if x.id not in stale]
        t.depends_on = [d for d in t.depends_on if d not in stale]
        action, added = "split", _split(plan, m, t, llm)
    t.spent_hours = 0  # the retry is a fresh attempt

    # insert right before the failed task; reorder() decides the final position
    idx = m.tasks.index(t)
    m.tasks[idx:idx] = added
    t.depends_on = sorted(set(t.depends_on) | {a.id for a in added})
    _log(plan, "failed", task_id, today, added=[a.id for a in added])
    reorder(m)
    return action, added


# ---------------------------------------------------------------- LLM helpers


def _context(plan: Plan, m: Milestone, t: Task) -> str:
    p = plan.profile
    done = [x.title for _, x in all_tasks(plan) if x.status == "done"]
    return (
        f"Skill: {p.get('skill')}  |  learner level: {p.get('current_level')}  |  goal: {p.get('goal') or '-'}\n"
        f"Milestone: {m.title} - {m.objective}\n"
        f"Knew before starting: {'; '.join(p.get('known_skills') or []) or 'nothing listed'}\n"
        f"Already completed: {'; '.join(done[-8:]) or 'nothing yet'}\n\n"
        f"The learner worked on this task for {t.spent_hours:g}h (planned {t.estimated_hours:g}h) "
        f"and still could NOT complete it (failure #{t.fail_count}):\n"
        f"  Title: {t.title}\n  What to do: {t.description}\n  Deliverable: {t.deliverable}\n\n"
        f"Research notes (for resources):\n{plan.research_notes[:MAX_NOTES_CHARS]}\n"
    )


def _clean(drafts: list[TaskDraft], n_max: int, hours_cap: float) -> list[TaskDraft]:
    drafts = [d for d in drafts if d.title.strip()][:n_max]
    for d in drafts:
        if d.estimated_hours <= 0:
            d.estimated_hours = 1
    total = sum(d.estimated_hours for d in drafts)
    if total > hours_cap > 0:  # keep extra work proportionate
        for d in drafts:
            d.estimated_hours = max(0.5, round(d.estimated_hours * hours_cap / total, 1))
    return drafts


def _build(
    plan: Plan, m: Milestone, drafts: list[TaskDraft], prefix: str, kind: str, chain: bool, topic: str = ""
) -> list[Task]:
    """Ids continue any earlier numbering, e.g. a second split of 2.3 gives 2.3s3, 2.3s4."""
    known = set(plan.sources)
    start = 1 + sum(1 for x in m.tasks if x.id.startswith(prefix) and x.id[len(prefix):].isdigit())
    out: list[Task] = []
    for i, d in enumerate(drafts, start):
        out.append(
            Task(
                id=f"{prefix}{i}",
                **d.model_dump(exclude={"depends_on", "resources"}),
                resources=[u for u in d.resources if u in known],
                depends_on=[out[-1].id] if chain and out else [],
                kind=kind,
                topic=topic,  # helpers count toward the sub-skill of the task they were added for
            )
        )
    return out


def _helpers(plan: Plan, m: Milestone, t: Task, llm) -> list[Task]:
    cap = max(1.0, 0.5 * t.estimated_hours)
    drafts: list[TaskDraft] = []
    if llm is not None:
        try:
            from .graph import structured

            res: RemedialTasks = structured(llm, RemedialTasks).invoke(
                "You are a tutor adapting a learning roadmap after a learner got stuck.\n\n"
                f"{_context(plan, m, t)}\n"
                "Work out which prerequisite skills or concepts this task depends on that the learner "
                "most likely hasn't mastered yet. Write 1-2 SHORT tasks that build exactly those, "
                "so the learner can succeed when retrying the task.\n"
                f"- Total hours at most {cap:g}.\n"
                "- Do not repeat the failed task itself; target what it builds on.\n"
                "- resources must be URLs copied from the research notes; leave empty if none fit.\n\n"
                f"{SPECIFIC_RULES}"
            )
            drafts = _clean(res.tasks, 2, cap)
        except Exception as e:  # a model hiccup must not lose the learner's progress
            print(f"  (LLM unavailable for helper tasks: {e})")
    if not drafts:
        drafts = [
            TaskDraft(
                title=f"Build the foundations for: {t.title}",
                description="List the concepts and tools this task uses. For each one you're unsure about, "
                "work through one small worked example end to end.",
                estimated_hours=cap,
                deliverable="A short note per concept plus a working mini-example for each.",
            )
        ]
    return _build(plan, m, drafts, f"{t.id}r", "remedial", chain=False, topic=t.topic)


def _split(plan: Plan, m: Milestone, t: Task, llm) -> list[Task]:
    hours = t.estimated_hours
    drafts: list[TaskDraft] = []
    if llm is not None:
        try:
            from .graph import structured

            res: SplitTasks = structured(llm, SplitTasks).invoke(
                "You are a tutor adapting a learning roadmap. The learner has failed this task more than once "
                "despite putting in the time, so it is too big a step.\n\n"
                f"{_context(plan, m, t)}\n"
                "Break it into 2-4 smaller steps, in order, each building on the previous one, that together "
                "reach the original deliverable.\n"
                f"- Step hours add up to about {hours:g}.\n"
                "- resources must be URLs copied from the research notes; leave empty if none fit.\n\n"
                f"{SPECIFIC_RULES}"
            )
            drafts = _clean(res.steps, 4, hours * 1.2)
        except Exception as e:
            print(f"  (LLM unavailable for splitting: {e})")
    if len(drafts) < 2:
        half = max(0.5, round(hours / 2, 1))
        drafts = [
            TaskDraft(
                title=f"{t.title} - part 1: minimal version",
                description=f"Build the simplest possible version of: {t.description}",
                estimated_hours=half,
                deliverable="A working minimal version, even if incomplete.",
            ),
            TaskDraft(
                title=f"{t.title} - part 2: extend it",
                description="Extend the minimal version step by step until it meets the original deliverable.",
                estimated_hours=half,
                deliverable=t.deliverable,
            ),
        ]
    # the original task becomes a short final check once the steps are done
    t.estimated_hours = max(0.5, round(0.25 * hours, 1))
    return _build(plan, m, drafts, f"{t.id}s", "step", chain=True, topic=t.topic)
