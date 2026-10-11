"""The learner's profile: interests, claimed skills, a skill graph and stats.

Interests and claimed skills are what the user tells us. The skill graph and stats are rebuilt
from every roadmap and its history on each change, so they can't drift out of sync:

    claimed skill --prerequisite--> milestone 1 --prerequisite--> milestone 2 ...
    sub-skill (a milestone's topic) --part_of--> milestone
    sub-skill A --prerequisite--> sub-skill B   when a task in B depends on a task in A
"""

import re
from datetime import date, datetime, timedelta
from pathlib import Path

from . import progress
from .schemas import (
    Plan,
    RoadmapStats,
    SkillEdge,
    SkillGraph,
    SkillNode,
    Task,
    TaskEvidence,
    UserProfile,
    UserStats,
)

PROFILE_FILE = "user_profile.json"
CHECK_TOPIC = "Skills check"


def profile_path(folder: Path) -> Path:
    return folder / PROFILE_FILE


def load(folder: Path) -> UserProfile:
    path = profile_path(folder)
    if path.exists():
        return UserProfile.model_validate_json(path.read_text(encoding="utf-8"))
    now = datetime.now().isoformat(timespec="seconds")
    return UserProfile(created=now, updated=now)


def save(user: UserProfile, folder: Path) -> None:
    user.updated = datetime.now().isoformat(timespec="seconds")
    profile_path(folder).write_text(user.model_dump_json(indent=2), encoding="utf-8")


def plans_in(folder: Path, extra: Path | None = None) -> list[tuple[Path, Plan]]:
    paths = sorted(folder.glob("roadmap_*.json"))
    if extra and extra.resolve() not in {p.resolve() for p in paths}:
        paths.append(extra)
    out = []
    for p in paths:
        try:
            out.append((p, progress.load(p)))
        except Exception:  # an old or broken file shouldn't break the profile
            continue
    return out


def split_list(text: str) -> list[str]:
    return list(dict.fromkeys(s.strip() for s in (text or "").split(",") if s.strip()))


def node_id(name: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", "-", name.lower()).strip("-")


# ---------------------------------------------------------------- skill graph


def _topic(t: Task, tasks: list[Task]) -> str:
    """A task's sub-skill; helpers from older plans (no topic) inherit it from the task they were added for."""
    if t.topic:
        return t.topic
    owners = [x for x in tasks if x.topic and re.fullmatch(re.escape(x.id) + r"([rs]\d+)+", t.id)]
    return max(owners, key=lambda x: len(x.id)).topic if owners else ""


# Deliverable file types that show which language a skills check exercises.
_CHECK_FILE = re.compile(r"[\w-]+\.(sql|py|ipynb|cpp|cc|java|js|ts|tsx|jsx|sh)\b", re.I)
_FILE_SKILL = {"sql": "sql", "py": "python", "ipynb": "python", "cpp": "c++", "cc": "c++", "java": "java",
               "js": "javascript", "jsx": "react", "ts": "typescript", "tsx": "typescript", "sh": "linux"}


def _names(skill: str) -> list[str]:
    """A claimed skill and the parts of a compound one ("Node.js and Express.js" -> both)."""
    name = skill.lower().strip()
    return [name] + [p.strip() for p in re.split(r"\band\b|/|,", name) if p.strip() and p.strip() != name]


def _named(skill: str, text: str) -> bool:
    return any(re.search(rf"(?<![\w+#.]){re.escape(n)}(?![\w+#])", text) for n in _names(skill))


def skills_tested(t: Task, claimed: list[str]) -> list[str]:
    """The claimed skills a skills-check task exercises.

    Strong evidence: the skill is named in the title, or the deliverable is that skill's kind of file
    (select_check.sql -> SQL). A skill only mentioned in the description ("runs on MySQL/PostgreSQL" in a
    check graded on SQLite) counts only when nothing is named strongly - mentioning a product is not using it.
    """
    files = {_FILE_SKILL[ext.lower()] for ext in _CHECK_FILE.findall(f"{t.deliverable} {t.title}")}
    title = t.title.lower()
    strong = [s for s in claimed if _named(s, title) or any(n in files for n in _names(s))]
    if strong:
        return strong
    text = f"{t.description} {t.deliverable}".lower()
    return [s for s in claimed if _named(s, text)]


def _builds_on(plan: Plan, claimed: list[SkillNode]) -> list[SkillNode]:
    """The claimed skills a roadmap actually rests on: ones the learner listed as known for THIS roadmap, and,
    when the roadmap has a skills check, that the check tests. A roadmap created without known skills rests on
    none - SQL is not a prerequisite of a Python course just because the learner also knows SQL."""
    known = {s.lower() for s in plan.profile.get("known_skills") or []}
    mine = [c for c in claimed if c.name.lower() in known]
    checks = [t for _, t in progress.all_tasks(plan) if t.topic == CHECK_TOPIC]
    if not checks or not mine:
        return mine
    tested = {s for t in checks for s in skills_tested(t, [c.name for c in mine])}
    return [c for c in mine if c.name in tested]


class _Acc:
    """Per-node running totals while merging tasks from several roadmaps."""

    def __init__(self):
        self.core_h = 0.0
        self.core_done_h = 0.0
        self.attempted = False
        self.last_core_done: str | None = None


def _evidence(t: Task, done_on: dict[str, str], roadmap: str) -> TaskEvidence:
    last = t.evaluations[-1] if t.evaluations else None
    url = last.source if last and last.source.startswith(("http://", "https://", "git@")) else None
    return TaskEvidence(
        id=t.id, title=t.title, description=t.description, deliverable=t.deliverable,
        difficulty=progress.difficulty(t), points=progress.earned(t), completed_on=done_on.get(t.id), kind=t.kind,
        tests=f"{last.tests_passed}/{last.tests_total}" if last else None,
        review_score=last.review.score if last and last.review else None, source=url, roadmap=roadmap,
    )


def _add(
    node: SkillNode, acc: _Acc, tasks: list[Task], done_on: dict[str, str], tried: set[str], roadmap: str = ""
) -> None:
    core = [t for t in tasks if t.kind == "core"]
    acc.core_h += sum(t.estimated_hours for t in core)
    acc.core_done_h += sum(t.estimated_hours for t in core if t.status == "done")
    acc.attempted |= any(t.id in tried for t in tasks)
    core_dates = [d for d in [acc.last_core_done, *(done_on.get(t.id) for t in core if t.status == "done")] if d]
    acc.last_core_done = max(core_dates) if core_dates else None
    node.tasks_total += len(core)
    node.tasks_done += sum(t.status == "done" for t in core)
    node.hours = round(node.hours + sum(t.estimated_hours for t in tasks if t.status == "done"), 1)
    node.failures += sum(t.fail_count for t in tasks)
    node.points += sum(progress.earned(t) for t in tasks)
    node.helpers_used += sum(t.kind != "core" for t in tasks)
    node.tasks += [_evidence(t, done_on, roadmap) for t in tasks if t.status == "done"]
    dates = [d for d in [node.last_practiced, *(done_on.get(t.id) for t in tasks)] if d]
    node.last_practiced = max(dates) if dates else None


def build_graph(user: UserProfile, plans: list[tuple[Path, Plan]]) -> SkillGraph:
    nodes: dict[str, SkillNode] = {}
    accs: dict[str, _Acc] = {}
    edges: set[tuple[str, str, str]] = set()
    checks: list[tuple[Task, str | None, str]] = []  # every skills-check task: (task, done on, roadmap)

    def node(name: str, kind: str, roadmap: str = "") -> SkillNode:
        nid = node_id(name)
        if nid not in nodes:
            nodes[nid] = SkillNode(id=nid, name=name, kind=kind, status="not_started")
            accs[nid] = _Acc()
        if roadmap and roadmap not in nodes[nid].roadmaps:
            nodes[nid].roadmaps.append(roadmap)
        return nodes[nid]

    claimed = [node(s, "claimed") for s in user.claimed_skills]
    planned: set[str] = set()

    for _, plan in plans:
        skill = plan.roadmap.skill
        done_on = {h["task"]: h["at"][:10] for h in plan.history if h.get("event") == "done"}
        tried = {h["task"] for h in plan.history if h.get("event") in ("attempt", "failed")}
        prev = None
        base = _builds_on(plan, claimed)
        for m in plan.roadmap.milestones:
            mn = node(m.title, "skill", skill)
            for c in [prev] if prev else base:  # what this roadmap builds on feeds its first milestone
                edges.add((c.id, mn.id, "prerequisite"))
            prev = mn
            if not m.tasks_written:
                planned.add(mn.id)
                continue

            topic_of = {t.id: _topic(t, m.tasks) for t in m.tasks}
            checks += [(t, done_on.get(t.id), skill) for t in m.tasks if topic_of[t.id] == CHECK_TOPIC]
            regular = [t for t in m.tasks if topic_of[t.id] != CHECK_TOPIC]
            _add(mn, accs[mn.id], regular, done_on, tried, skill)

            by_topic: dict[str, list[Task]] = {}
            for t in regular:
                if topic_of[t.id]:
                    by_topic.setdefault(topic_of[t.id], []).append(t)
            for topic, tasks in by_topic.items():
                sn = node(topic, "subskill", skill)
                sn.parent = sn.parent or m.title
                _add(sn, accs[sn.id], tasks, done_on, tried, skill)
                edges.add((sn.id, mn.id, "part_of"))
            for t in regular:  # cross-topic task dependencies become sub-skill prerequisites
                for dep in t.depends_on:
                    a, b = topic_of.get(dep, ""), topic_of[t.id]
                    if a and b and a != b and CHECK_TOPIC not in (a, b):
                        edges.add((node_id(a), node_id(b), "prerequisite"))

    for nid, n in nodes.items():
        acc = accs[nid]
        n.progress = round(acc.core_done_h / acc.core_h, 3) if acc.core_h else 0
        if n.kind == "claimed":
            n.status = "claimed"
        elif not n.tasks_total and nid in planned:
            n.status = "planned"
        elif n.tasks_total and n.tasks_done == n.tasks_total:
            n.status = "completed"
            n.acquired_on = acc.last_core_done
        elif n.tasks_done or acc.attempted:
            n.status = "in_progress"
        else:
            n.status = "not_started"
    # A skills check verifies (or questions) only the claimed skills it actually tests. Passing a SQL query
    # proves SQL, not every skill the learner listed - and an unfinished check elsewhere doesn't hold it back.
    names = [c.name for c in claimed]
    tested = [names if len(names) == 1 else skills_tested(t, names) for t, _, _ in checks]
    for c in claimed:
        mine = [check for check, skills in zip(checks, tested) if c.name in skills]
        if not mine:
            continue
        results = ["failed" if t.fail_count else t.status for t, _, _ in mine]
        c.status = "needs_review" if "failed" in results else "verified" if all(r == "done" for r in results) else "claimed"
        c.tasks = [_evidence(t, {t.id: on} if on else {}, rm) for t, on, rm in mine if t.status == "done"]
        dates = [on for _, on, _ in mine if on]
        c.acquired_on = max(dates) if c.status == "verified" and dates else None

    return SkillGraph(
        nodes=list(nodes.values()),
        edges=[SkillEdge(src=a, dst=b, kind=k) for a, b, k in sorted(edges) if a in nodes and b in nodes],
    )


def acquired(graph: SkillGraph) -> SkillGraph:
    """Only what the learner has earned by completing tasks: finished sub-skills and skills,
    plus claimed skills once the skills check has verified them."""
    keep = {n.id for n in graph.nodes if n.status in ("completed", "verified")}
    return SkillGraph(
        nodes=[n for n in graph.nodes if n.id in keep],
        edges=[e for e in graph.edges if e.src in keep and e.dst in keep],
    )


def learning(graph: SkillGraph) -> SkillGraph:
    """The learner's own view: what they earned, plus what they are part-way through, with the finished
    tasks behind each. Unlike `acquired` (the recruiter-facing proof), an in-progress skill shows here as
    soon as one of its tasks is done or attempted. Never saved to the profile, never shown to recruiters."""
    keep = {n.id for n in graph.nodes if n.status in ("completed", "verified", "in_progress")}
    return SkillGraph(
        nodes=[n for n in graph.nodes if n.id in keep],
        edges=[e for e in graph.edges if e.src in keep and e.dst in keep],
    )


def learning_graph(folder: Path, user: UserProfile) -> SkillGraph:
    return learning(build_graph(user, plans_in(folder)))


# ---------------------------------------------------------------- stats


def _streaks(active: set[date], today: date, study_day) -> tuple[int, int]:
    """(current, longest) runs of activity; days off (non-study days, and today so far) don't break a run."""
    if not active:
        return 0, 0
    longest = run = 0
    d, end = min(active), max(max(active), today)
    while d <= end:
        if d in active:
            run += 1
            longest = max(longest, run)
        elif study_day(d) and d != today:
            run = 0
        d += timedelta(days=1)
    return run, longest


def build_stats(graph: SkillGraph, plans: list[tuple[Path, Plan]], today: date) -> UserStats:
    events = [h for _, p in plans for h in p.history]
    done_core = [t for _, p in plans for _, t in progress.all_tasks(p) if t.kind == "core" and t.status == "done"]
    active = {date.fromisoformat(h["at"][:10]) for h in events if h.get("event") in ("done", "attempt", "failed")}
    dpw = max([int(p.profile.get("days_per_week") or 7) for _, p in plans] or [7])
    current, longest = _streaks(active, today, lambda d: d.weekday() < dpw)

    roadmaps = []
    for path, plan in plans:
        pr = progress.projection(plan, today)
        total = pr["done_hours"] + pr["left_hours"]
        tasks = [t for _, t in progress.all_tasks(plan)]
        roadmaps.append(
            RoadmapStats(
                skill=plan.roadmap.skill,
                file=path.name,
                progress=round(pr["done_hours"] / total, 3) if total else 0,
                tasks_done=sum(t.status == "done" for t in tasks),
                tasks_total=len(tasks),
                projected_end=pr["projected_end"].isoformat(),
                planned_end=pr["planned_end"].isoformat(),
                slip_days=pr["slip_days"],
            )
        )

    skills = [n for n in graph.nodes if n.kind != "claimed"]
    latest = [t.evaluations[-1] for _, p in plans for _, t in progress.all_tasks(p) if t.evaluations]
    scores = [e.review.score for e in latest if e.review]
    by_diff: dict[str, int] = {}
    for _, p in plans:
        for _, t in progress.all_tasks(p):
            if t.status == "done":
                by_diff[progress.difficulty(t)] = by_diff.get(progress.difficulty(t), 0) + progress.earned(t)
    return UserStats(
        points=sum(by_diff.values()),
        points_by_difficulty={k: by_diff[k] for k in ("easy", "medium", "hard") if k in by_diff},
        submissions=sum(h.get("event") == "submitted" for h in events),
        submissions_passed=sum(h.get("event") == "submitted" and bool(h.get("passed")) for h in events),
        avg_code_score=round(sum(scores) / len(scores), 1) if scores else None,
        hours_logged=round(sum(h.get("spent", 0) for h in events), 1),
        tasks_done=sum(h.get("event") == "done" for h in events),
        failures=sum(h.get("event") == "failed" for h in events),
        skips=sum(h.get("event") == "skipped" for h in events),
        helper_tasks=sum(len(h.get("added", [])) for h in events if h.get("event") == "failed"),
        first_try_rate=round(sum(t.fail_count == 0 for t in done_core) / len(done_core), 3) if done_core else None,
        active_days=len(active),
        current_streak=current,
        longest_streak=longest,
        last_active=max(active).isoformat() if active else None,
        skills_completed=sum(n.status == "completed" for n in skills),
        skills_in_progress=sum(n.status == "in_progress" for n in skills),
        roadmaps=roadmaps,
    )


# ---------------------------------------------------------------- entry points


def rebuild(folder: Path, today: date, extra_plan: Path | None = None) -> UserProfile:
    """Recompute the skill graph and stats from every roadmap in `folder` and save the profile."""
    user = load(folder)
    if user.demo:  # hand-written demo data: keep skill_graph and stats exactly as in the file
        return user
    plans = plans_in(folder, extra_plan)
    from . import calendar_plan  # projections in the stats follow the learner's real week too

    calendar_plan.apply(folder, [(p.stem, plan) for p, plan in plans], today)
    full = build_graph(user, plans)  # includes planned / in-progress skills, used for stats
    user.stats = build_stats(full, plans, today)
    from . import arena  # practice challenges earn points but never skills

    a = arena.load(folder)
    user.stats.arena_points = arena.points_earned(a)
    user.stats.arena_solved = sum(c.task.status == "done" for c in a.challenges)
    user.stats.points += user.stats.arena_points
    user.skill_graph = acquired(full)
    save(user, folder)
    return user


def known_skills(user: UserProfile, exclude_roadmap: str | None = None) -> list[str]:
    """What a new roadmap can build on: claimed skills plus skills finished in other roadmaps."""
    out = list(user.claimed_skills)
    for n in user.skill_graph.nodes:
        if n.kind != "claimed" and n.status == "completed" and n.roadmaps != [exclude_roadmap]:
            out.append(n.name)
    return list(dict.fromkeys(out))
