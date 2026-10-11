"""Global ranking: where each learner stands among everyone GLAI knows (the local learner + candidates/).

The score is points - earned only by finished, tested tasks, weighted by difficulty, reduced for real
failures and adjusted by code review (progress.award) - so a rank is never self-reported. Ties go to more
tasks done, then a higher first-try rate, then a higher average code review.

Ranked twice:
    overall    all learners, by total points
    by area    broad areas (SQL, Python, Web frontend, ...): points from the tasks behind a learner's
               earned skills in that area, each task counted once. Only learners with work in the area rank.

Tiers name the overall level by points, Codeforces-style, so a tier means the same thing whoever else is in
the pool. (Elo-style ratings need rated contests; until those exist, points are the honest measure.)
"""

import math
import re
from dataclasses import dataclass

from .schemas import UserProfile

TIERS = [  # (minimum points, title)
    (1000, "Master"),
    (500, "Expert"),
    (250, "Specialist"),
    (100, "Apprentice"),
    (0, "Newbie"),
]

# Broad areas, and words in a skill's name (or its parent skill's) that put it there.
AREAS: dict[str, list[str]] = {
    "SQL & databases": ["sql", "database", "query", "queries", "joins", "postgres", "mysql"],
    "Python": ["python", "functions and scope", "control flow", "variables and data types", "error handling"],
    "Data analysis": ["pandas", "numpy", "csv", "dataframe", "cleaning", "wrangling", "excel", "pivot"],
    "Data visualisation": ["visualis", "visualiz", "matplotlib", "seaborn", "plotly", "dashboard", "chart"],
    "Statistics": ["statistic", "hypothesis", "probability"],
    "Machine learning": ["machine learning", "scikit", "classification", "regression", "neural", "pytorch", "model"],
    "Web frontend": ["html", "css", "javascript", "react", "dom", "responsive", "fetch api"],
    # not plain "api": a frontend "Fetch API" skill is calling an API, not building one
    "Backend & APIs": ["rest api", "fastapi", "flask", "django", "express", "backend", "pydantic", "endpoint", "server-side"],
}


def tier(points: int) -> str:
    return next(title for floor, title in TIERS if points >= floor)


def next_tier(points: int) -> dict | None:
    """The next tier up and the points still needed, or None at the top."""
    above = [(floor, title) for floor, title in TIERS if floor > points]
    if not above:
        return None
    floor, title = min(above)
    return {"tier": title, "points_needed": floor - points}


def _in_area(text: str, words: list[str]) -> bool:
    text = text.lower()
    return any(re.search(rf"(?<![a-z]){re.escape(w)}", text) for w in words)


def area_points(profile: UserProfile) -> dict[str, int]:
    """Points per area from the tasks behind earned skills; a task counts once per area."""
    earned = [n for n in profile.skill_graph.nodes if n.status in ("completed", "verified")]
    out = {}
    for area, words in AREAS.items():
        seen, total = set(), 0
        for n in earned:
            if not _in_area(f"{n.name} {n.parent or ''}", words):
                continue
            for t in n.tasks:
                key = (t.roadmap, t.id)
                if key not in seen:
                    seen.add(key)
                    total += t.points
        if total:
            out[area] = total
    return out


@dataclass
class Learner:
    id: str
    profile: UserProfile


def _order_key(p: UserProfile, points: int):
    s = p.stats
    return (-points, -s.tasks_done, -(s.first_try_rate or 0), -(s.avg_code_score or 0), (p.name or "").lower())


def _place(entries: list[tuple[str, UserProfile, int]]) -> dict[str, dict]:
    """Rank by points (ties broken as described above); equal points and tie-breakers share a rank."""
    ordered = sorted(entries, key=lambda e: _order_key(e[1], e[2]))
    total, out, rank, previous = len(ordered), {}, 0, None
    for i, (lid, p, points) in enumerate(ordered, 1):
        key = _order_key(p, points)[:-1]
        if key != previous:
            rank, previous = i, key
        out[lid] = {"rank": rank, "total": total, "points": points,
                    "top_percent": max(1, math.ceil(rank / total * 100))}
    return out


def rank_all(learners: list[Learner]) -> dict[str, dict]:
    """Every learner's overall and per-area standing."""
    overall = _place([(l.id, l.profile, l.profile.stats.points) for l in learners])
    per_area = {lid: area_points(l.profile) for lid, l in zip([l.id for l in learners], learners)}
    areas: dict[str, dict[str, dict]] = {}
    for area in AREAS:
        entries = [(l.id, l.profile, per_area[l.id][area]) for l in learners if per_area[l.id].get(area)]
        areas[area] = _place(entries)
    out = {}
    for l in learners:
        o = overall[l.id]
        out[l.id] = {
            "overall": {**o, "tier": tier(o["points"]), "next_tier": next_tier(o["points"])},
            "areas": sorted(({"area": a, **areas[a][l.id]} for a in AREAS if l.id in areas[a]),
                            key=lambda x: (x["rank"] / x["total"], -x["points"])),
        }
    return out


def leaderboard(learners: list[Learner], area: str | None = None, limit: int = 100) -> dict:
    """The ranked list, overall or for one area."""
    if area and area not in AREAS:
        raise ValueError(f"Unknown area {area!r}.")
    standings = rank_all(learners)
    rows = []
    for l in learners:
        s = standings[l.id]
        place = s["overall"] if not area else next((a for a in s["areas"] if a["area"] == area), None)
        if place is None:
            continue
        p = l.profile
        rows.append({"id": l.id, "name": p.name or l.id, "headline": p.headline, "location": p.location, "demo": p.demo,
                     "rank": place["rank"], "points": place["points"], "top_percent": place["top_percent"],
                     "tier": s["overall"]["tier"], "tasks_done": p.stats.tasks_done,
                     "first_try_rate": p.stats.first_try_rate, "avg_code_score": p.stats.avg_code_score})
    rows.sort(key=lambda r: (r["rank"], r["name"].lower()))
    return {"area": area, "areas": list(AREAS), "total": len(rows), "entries": rows[:limit],
            "tiers": [{"tier": t, "min_points": f} for f, t in sorted(TIERS)]}
