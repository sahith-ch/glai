"""Create a roadmap: research -> outline -> schedule -> first milestone's tasks -> saved plan.

Shared by the CLI (`python main.py new`) and the learner app's "New roadmap" form. Progress is
reported through `on_event(kind, message, data)` so each caller can show it its own way.
"""

import re
from datetime import date
from pathlib import Path
from typing import Callable

from . import progress, user
from .schemas import Plan

Event = Callable[[str, str, dict], None]


def slug(skill: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", skill.lower()).strip("_")


def known_skills(root: Path, skill: str, new_skills: list[str], today: date, use_other_roadmaps: bool = True) -> list[str]:
    """Skills the new roadmap builds on: newly told ones (saved to the profile), claimed ones,
    and skills finished in other roadmaps."""
    profile = user.load(root)
    if new_skills:
        profile.claimed_skills = list(dict.fromkeys(profile.claimed_skills + new_skills))
        user.save(profile, root)
    if not use_other_roadmaps:
        return list(dict.fromkeys(new_skills + profile.claimed_skills))
    return user.known_skills(user.rebuild(root, today), exclude_roadmap=skill)


def create_roadmap(
    root: Path,
    skill: str,
    level: str = "beginner",
    weekly_hours: int = 8,
    target_weeks: int | None = None,
    goal: str = "",
    new_skills: list[str] | None = None,
    days_per_week: int = 7,
    start: date | None = None,
    today: date | None = None,
    out: Path | None = None,
    use_other_roadmaps: bool = True,
    on_event: Event = lambda kind, msg, data: None,
) -> tuple[Path, Plan, list[str]]:
    """Run the agent and save the plan. Returns (path, plan, unresolved issues)."""
    from .graph import graph

    today = today or date.today()
    inputs = {
        "skill": skill,
        "current_level": level,
        "weekly_hours": weekly_hours,
        "target_weeks": target_weeks,
        "goal": goal,
        "known_skills": known_skills(root, skill, new_skills or [], today, use_other_roadmaps),
        "interests": user.load(root).interests,
    }
    from . import calendar_plan

    slot_minutes, free_week = calendar_plan.typical_slot_minutes(root, today)
    if slot_minutes:
        inputs["slot_minutes"] = slot_minutes
        on_event("calendar", "Sizing tasks to your calendar", {"slot_minutes": slot_minutes, "free_hours_week": free_week})
        if free_week is not None and free_week < weekly_hours:
            on_event("calendar", f"Your calendar has about {free_week:g}h free per week in your study window, "
                     f"less than the {weekly_hours}h planned: expect the schedule to stretch.",
                     {"free_hours_week": free_week})
    if inputs["known_skills"]:
        on_event("known", "Building on what you already know", {"skills": inputs["known_skills"]})

    final: dict = {}
    for update in graph.stream(inputs, stream_mode="updates"):
        for node, delta in update.items():
            delta = delta or {}
            if node == "plan_queries":
                on_event("plan_queries", "Planned web searches", {"queries": delta["queries"]})
            elif node == "search":
                on_event("search", "Searched the web", {"results": len(delta.get("search_results", []))})
            elif node == "synthesize":
                on_event("synthesize", "Summarised the research", {})
            elif node == "outline":
                on_event("outline", "Outlined milestones", {})
            elif node == "validate":
                on_event("validate", "Checked the outline", {"issues": delta.get("issues") or []})
            elif node == "schedule":
                on_event("schedule", "Scheduled milestones over the weeks", {})
            elif node == "write_tasks":
                on_event("write_tasks", "Wrote tasks for the first milestone", {})
            if node == "search":
                final.setdefault("search_results", []).extend(delta.get("search_results", []))
            else:
                final.update(delta)

    plan = Plan(
        profile={**inputs, "start_date": (start or today).isoformat(), "days_per_week": days_per_week},
        research_notes=final.get("research_notes", ""),
        sources=sorted({r["url"] for r in final.get("search_results", []) if r.get("url")}),
        source_titles={r["url"]: r.get("title", "") for r in final.get("search_results", []) if r.get("url")},
        roadmap=final["roadmap"],
    )
    path = out or root / f"roadmap_{slug(skill)}.json"
    progress.save(plan, path)
    user.rebuild(root, today, extra_plan=path)
    on_event("saved", "Saved", {"file": path.stem})
    return path, plan, final.get("issues") or []
