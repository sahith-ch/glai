"""Payloads for the learner dashboard (the React "Learn" page): roadmaps, schedule, today, profile."""

from datetime import date
from pathlib import Path

from . import progress, submissions, user
from .schemas import Plan, Task


def roadmap_files(root: Path) -> list[Path]:
    return sorted(root.glob("roadmap_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def _scheduled(root: Path, today: date) -> tuple[list[tuple[Path, Plan]], str | None]:
    """Every readable roadmap, each scheduled around the learner's calendar when one is connected."""
    from . import calendar_plan

    loaded = []
    for p in roadmap_files(root):
        try:
            loaded.append((p, progress.load(p)))
        except Exception:
            continue
    problem = calendar_plan.apply(root, [(p.stem, plan) for p, plan in loaded], today)
    return loaded, problem


def roadmap_list(root: Path) -> list[dict]:
    out = []
    for p in roadmap_files(root):
        try:
            plan = progress.load(p)
        except Exception:
            continue
        out.append({"file": p.stem, "skill": plan.roadmap.skill})
    return out


def _task(t: Task, span) -> dict:
    last = t.evaluations[-1] if t.evaluations else None
    return {
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "deliverable": t.deliverable,
        "status": t.status,
        "kind": t.kind,
        "topic": t.topic,
        "hours": t.estimated_hours,
        "difficulty": progress.difficulty(t),
        "points": progress.earned(t) if t.status == "done" else progress.max_points(t),
        "depends_on": t.depends_on,
        "spent_hours": t.spent_hours,
        "fail_count": t.fail_count,
        "start": span[0].isoformat() if span else None,
        "end": span[1].isoformat() if span else None,
        "submission": (
            {"passed": last.passed, "tests": f"{last.tests_passed}/{last.tests_total}",
             "score": last.review.score if last.review else None}
            if last else None
        ),
        "evaluations": [e.model_dump() for e in reversed(t.evaluations[-5:])],  # newest first
        "tests": submissions.tests_view(t),
    }


def roadmap_payload(path: Path, today: date) -> dict:
    loaded, _ = _scheduled(path.parent, today)
    plan: Plan = next((p for f, p in loaded if f.stem == path.stem), None) or progress.load(path)
    sched = progress.schedule(plan, today)
    proj = progress.projection(plan, today)
    current = progress.current_milestone(plan)
    nxt = progress.next_task(plan)

    day = today if progress.is_study_day(plan, today) else progress.next_study_day(plan, today)
    milestones = []
    for i, m in enumerate(plan.roadmap.milestones, 1):
        core = [t for t in m.tasks if t.kind == "core"]
        milestones.append({
            "index": i,
            "title": m.title,
            "objective": m.objective,
            "week_start": m.week_start,
            "week_end": m.week_end,
            "hours": progress.milestone_hours(plan, m),
            "tasks_written": m.tasks_written,
            "scheduled": [d.isoformat() for d in sched[f"M{i}"]] if f"M{i}" in sched else None,
            "done": sum(t.status == "done" for t in core),
            "total": len(core),
            "current": m is current,
            "success_criteria": m.success_criteria,
            "tasks": [_task(t, sched.get(t.id)) for t in m.tasks],
        })

    return {
        "file": path.stem,
        "skill": plan.roadmap.skill,
        "summary": plan.roadmap.summary,
        "prerequisites": plan.roadmap.prerequisites,
        "profile": {k: plan.profile.get(k) for k in ("current_level", "weekly_hours", "days_per_week", "goal", "start_date")},
        "daily_hours": round(progress.daily_hours(plan), 1),
        "today": today.isoformat(),
        "study_day": day.isoformat(),
        "is_today": day == today,
        "today_tasks": [t.id for t in progress.tasks_on(plan, day, today)],
        "next_task": nxt.id if nxt else None,
        "needs_writing": bool(progress.milestones_to_write(plan)),
        "calendar": plan._capacity is not None,  # scheduled around the learner's Google Calendar  # a milestone is due but not written yet
        "projection": {
            "done_hours": proj["done_hours"],
            "left_hours": proj["left_hours"],
            "planned_end": proj["planned_end"].isoformat(),
            "projected_end": proj["projected_end"].isoformat(),
            "slip_days": proj["slip_days"],
        },
        "milestones": milestones,
    }


def today_payload(root: Path, today: date) -> dict:
    """Daily quests across every roadmap: what's scheduled today per skill, plus what was finished today."""
    groups = []
    loaded, _ = _scheduled(root, today)
    for path, plan in loaded:
        sched = progress.schedule(plan, today)
        study_today = progress.is_study_day(plan, today)
        scheduled = progress.tasks_on(plan, today, today) if study_today else []
        finished = {h["task"] for h in plan.history if h.get("event") == "done" and h.get("at", "")[:10] == today.isoformat()}
        done_today = [t for _, t in progress.all_tasks(plan) if t.id in finished]
        daily = progress.daily_hours(plan)
        hours_today = min(daily, sum(progress.remaining_hours(t) for t in scheduled)) if study_today else 0
        nxt = progress.next_task(plan)
        proj = progress.projection(plan, today)
        total = proj["done_hours"] + proj["left_hours"]
        cur = progress.current_milestone(plan)
        groups.append({
            "file": path.stem,
            "skill": plan.roadmap.skill,
            "milestone": cur.title if cur else None,
            "study_today": study_today,
            "next_day": progress.next_study_day(plan, today).isoformat() if not study_today else None,
            "daily_hours": round(daily, 1),
            "hours_today": round(hours_today, 1),
            "progress": round(proj["done_hours"] / total, 3) if total else 0,
            "slip_days": proj["slip_days"],
            "next_task": nxt.id if nxt else None,
            "tasks": [_task(t, sched.get(t.id)) for t in scheduled],
            "done_today": [_task(t, None) for t in done_today],
        })
    active = [g for g in groups if g["tasks"]]
    return {
        "today": today.isoformat(),
        "groups": groups,
        "quests": sum(len(g["tasks"]) for g in groups),
        "done": sum(len(g["done_today"]) for g in groups),
        "skills_today": len(active),
        "hours_today": round(sum(g["hours_today"] for g in groups), 1),
    }


def arena_payload(root: Path, today: date) -> dict:
    """Arena: every unfinished roadmap quest (solve any of them now), plus extra generated challenges."""
    from . import arena

    a = arena.load(root)
    challenges = [
        {**_task(c.task, None), "challenge": True, "roadmap": c.roadmap, "skill": c.skill, "created": c.created,
         "based_on": c.based_on}
        for c in reversed(a.challenges)
    ]
    quest_groups, roadmaps = [], []
    loaded, _ = _scheduled(root, today)
    for path, plan in loaded:
        roadmaps.append({"file": path.stem, "skill": plan.roadmap.skill})
        sched = progress.schedule(plan, today)
        milestones, locked, done, total = [], 0, 0, 0
        for i, m in enumerate(plan.roadmap.milestones, 1):
            if not m.tasks_written:
                locked += 1
                continue
            total += len(m.tasks)
            done += sum(t.status == "done" for t in m.tasks)
            open_tasks = [t for t in m.tasks if t.status != "done"]
            if open_tasks:
                milestones.append({"index": i, "title": m.title,
                                   "tasks": [_task(t, sched.get(t.id)) for t in open_tasks]})
        quest_groups.append({"file": path.stem, "skill": plan.roadmap.skill, "milestones": milestones,
                             "locked_milestones": locked, "done": done, "total": total})
    return {
        "today": today.isoformat(),
        "roadmaps": roadmaps,
        "quest_groups": quest_groups,
        "open_quests": sum(len(m["tasks"]) for g in quest_groups for m in g["milestones"]),
        "challenges": challenges,
        "solved": sum(c["status"] == "done" for c in challenges),
        "points": arena.points_earned(a),
        "points_by_difficulty": progress.BASE_POINTS,
    }


def stats_payload(root: Path, today: date, weeks: int = 12) -> dict:
    """Daily activity across all roadmaps for the last `weeks` weeks (Monday-aligned), for the heatmap."""
    from datetime import timedelta

    start = today - timedelta(days=today.weekday() + 7 * (weeks - 1))
    days = {(start + timedelta(days=i)).isoformat(): {"hours": 0.0, "tasks": 0} for i in range((today - start).days + 1)}
    for p in roadmap_files(root):
        try:
            plan = progress.load(p)
        except Exception:
            continue
        for h in plan.history:
            d = h.get("at", "")[:10]
            if d in days:
                days[d]["hours"] += h.get("spent", 0) or 0
                days[d]["tasks"] += h.get("event") == "done"
    return {"start": start.isoformat(), "today": today.isoformat(),
            "activity": [{"date": d, "hours": round(v["hours"], 1), "tasks": v["tasks"]} for d, v in days.items()]}


def me_payload(root: Path, today: date) -> dict:
    p = user.rebuild(root, today)
    out = p.model_dump(include={"name", "headline", "location", "contact", "interests", "claimed_skills", "demo",
                                "skill_graph", "stats"})
    # skill_graph is the earned proof; learning_graph adds skills in progress, for the learner's own pages
    out["learning_graph"] = user.learning_graph(root, p).model_dump()
    return out
