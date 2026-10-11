from datetime import date
from pathlib import Path

from roadmap_agent import user
from roadmap_agent.schemas import Milestone, Plan, Roadmap, Task, UserProfile


def T(i, topic, status="todo", kind="core", fails=0, h=2):
    return Task(id=i, title=i, description="d", estimated_hours=h, deliverable="x", topic=topic,
                status=status, kind=kind, fail_count=fails)


m1 = Milestone(title="Python basics", objective="o", week_start=1, week_end=1, success_criteria=[], tasks=[
    T("1.1", "Skills check", "done"),
    T("1.2", "Loops", "done"), T("1.3", "Loops", "done"),          # Loops fully done -> acquired
    T("1.4", "Functions", "done"), T("1.5", "Functions", "failed", fails=1),  # half done -> not acquired
    T("1.5r1", "", "done", kind="remedial"),                       # old-style helper with no topic
])
m2 = Milestone(title="Pandas", objective="o", week_start=2, week_end=2, success_criteria=[], tasks=[
    T("2.1", "Reading CSVs", "done", fails=1), T("2.2", "Reading CSVs", "done"),
])
m3 = Milestone(title="Plotting", objective="o", week_start=3, week_end=3, success_criteria=[], tasks=[],
               tasks_written=False)
hist = [{"at": f"2026-10-{d:02d}T10:00:00", "event": "done", "task": t, "spent": 2}
        for d, t in [(12, "1.1"), (12, "1.2"), (13, "1.3"), (13, "1.4"), (14, "1.5r1"), (15, "2.1"), (16, "2.2")]]
plan = Plan(profile={"skill": "DA", "weekly_hours": 10, "days_per_week": 5, "start_date": "2026-10-12"},
            roadmap=Roadmap(skill="DA", summary="s", total_weeks=3, milestones=[m1, m2, m3]), history=hist)

prof = UserProfile(claimed_skills=["Excel"])
full = user.build_graph(prof, [(Path("roadmap_da.json"), plan)])
g = user.acquired(full)
got = {n.name: (n.kind, n.status, n.acquired_on, n.parent) for n in g.nodes}
print(got)
assert got == {
    "Excel": ("claimed", "verified", "2026-10-12", None),           # skills check task 1.1 done
    "Loops": ("subskill", "completed", "2026-10-13", "Python basics"),
    "Pandas": ("skill", "completed", "2026-10-16", None),           # whole milestone done
    "Reading CSVs": ("subskill", "completed", "2026-10-16", "Pandas"),
}, got
assert "Functions" not in got and "Python basics" not in got and "Plotting" not in got
rc = next(n for n in g.nodes if n.name == "Reading CSVs")
assert rc.failures == 1  # struggled, but still earned
full_fn = next(n for n in full.nodes if n.name == "Functions")
assert full_fn.status == "in_progress" and full_fn.helpers_used == 1  # untopiced helper counted toward it
assert all(e.src in {n.id for n in g.nodes} and e.dst in {n.id for n in g.nodes} for e in g.edges)
stats = user.build_stats(full, [(Path("roadmap_da.json"), plan)], date(2026, 10, 16))
print("in progress:", stats.skills_in_progress, "| completed:", stats.skills_completed)
assert stats.skills_in_progress >= 1  # stats still see unfinished skills
print("ALL OK")
