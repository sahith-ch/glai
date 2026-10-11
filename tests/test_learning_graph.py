"""The learner's skill graph shows skills in progress with the tasks behind them; the earned graph stays proof only."""

import tempfile
from datetime import date
from pathlib import Path

from roadmap_agent import dashboard, progress, user
from roadmap_agent.schemas import Milestone, Plan, Roadmap, Task

root = Path(tempfile.mkdtemp())
tasks = [Task(id=f"1.{i}", title=f"Task {i}", description="d", estimated_hours=1, deliverable=f"t{i}.py", topic=topic)
         for i, topic in [(1, "Loops"), (2, "Loops"), (3, "Functions")]]
plan = Plan(profile={"skill": "Py", "weekly_hours": 5, "days_per_week": 5, "start_date": "2026-10-12"},
            roadmap=Roadmap(skill="Py", summary="", total_weeks=1,
                            milestones=[Milestone(title="Basics", objective="o", week_start=1, week_end=1,
                                                  tasks=tasks, success_criteria=[])]))
progress.mark_done(plan, "1.1", date(2026, 10, 12))
progress.save(plan, root / "roadmap_py.json")

me = dashboard.me_payload(root, date(2026, 10, 12))
earned = {n["name"] for n in me["skill_graph"]["nodes"]}
learning = {n["name"]: n for n in me["learning_graph"]["nodes"]}
print("earned:", earned, "| learning:", {k: (v["status"], [t["id"] for t in v["tasks"]]) for k, v in learning.items()})

assert earned == set(), "half a sub-skill is not proof: recruiters see nothing yet"
assert learning["Loops"]["status"] == "in_progress" and [t["id"] for t in learning["Loops"]["tasks"]] == ["1.1"]
assert learning["Basics"]["status"] == "in_progress"
assert "Functions" not in learning, "untouched sub-skills stay off the graph"
assert any(e["src"] == learning["Loops"]["id"] and e["kind"] == "part_of" for e in me["learning_graph"]["edges"])

# finishing the sub-skill moves it into the earned graph, with the same tasks behind it
progress.mark_done(plan, "1.2", date(2026, 10, 13))
progress.save(plan, root / "roadmap_py.json")
me = dashboard.me_payload(root, date(2026, 10, 13))
loops = next(n for n in me["skill_graph"]["nodes"] if n["name"] == "Loops")
assert loops["status"] == "completed" and [t["id"] for t in loops["tasks"]] == ["1.1", "1.2"]
assert user.load(root).skill_graph.nodes[0].name == "Loops"  # the saved profile holds proof only

print("ALL OK")
