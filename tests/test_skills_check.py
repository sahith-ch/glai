"""A skills check verifies the claimed skills it actually tests - not every skill the learner listed."""

from pathlib import Path

from roadmap_agent import user
from roadmap_agent.schemas import Milestone, Plan, Roadmap, Task, UserProfile


def check(i, title, description, deliverable, status="todo", fails=0):
    return Task(id=i, title=title, description=description, estimated_hours=1, deliverable=deliverable,
                topic="Skills check", status=status, fail_count=fails)


def plan(skill, tasks, done=()):
    return Plan(profile={"skill": skill, "weekly_hours": 5, "days_per_week": 5, "start_date": "2026-10-12"},
                roadmap=Roadmap(skill=skill, summary="", total_weeks=1, milestones=[
                    Milestone(title=f"{skill} basics", objective="o", week_start=1, week_end=1, tasks=tasks,
                              success_criteria=[])]),
                history=[{"at": "2026-10-12T10:00:00", "event": "done", "task": t} for t in done])


sql = plan("sql", [check("1.1", "Write select_check.sql that retrieves ordered rows",
                         "Query the employees table. When executed against MySQL/PostgreSQL it returns two rows.",
                         "select_check.sql: two rows", status="done")], done=["1.1"])
# Another roadmap's check is still open: it must not hold back SQL.
cuda = plan("cuda", [check("1.1", "Python warm-up", "Write a function", "warmup.py: prints 3")])
# A failed check questions only what it tested.
cpp = plan("dsa", [check("1.1", "Implement a linked list", "Use classes", "list.cpp: passes the tests", fails=1)])

prof = UserProfile(claimed_skills=["SQL", "PostgreSQL", "MySQL", "Python", "C++", "Docker"])
graph = user.build_graph(prof, [(Path("a.json"), sql), (Path("b.json"), cuda), (Path("c.json"), cpp)])
status = {n.name: n.status for n in graph.nodes if n.kind == "claimed"}
print(status)
assert status == {"SQL": "verified", "PostgreSQL": "claimed", "MySQL": "claimed", "Python": "claimed",
                  "C++": "needs_review", "Docker": "claimed"}, status

sql_node = next(n for n in graph.nodes if n.name == "SQL")
assert [t.id for t in sql_node.tasks] == ["1.1"] and sql_node.acquired_on == "2026-10-12"
assert {n.name for n in user.acquired(graph).nodes} == {"SQL"}, "only what was tested is proof"

# A mention is enough when the check names nothing more specific.
mention = check("1.1", "Warm-up", "Containerise the app with Docker", "Dockerfile builds", status="done")
assert user.skills_tested(mention, ["Docker", "Python"]) == ["Docker"]
# Compound skills match by their parts.
assert user.skills_tested(check("1", "Build an Express.js route", "d", "app.js"), ["Node.js and Express.js"]) \
    == ["Node.js and Express.js"]
# Edges: a claimed skill feeds only the roadmaps that build on it.
sql.profile["known_skills"] = ["SQL", "PostgreSQL", "Python"]  # listed as known when the SQL roadmap was made
python_course = plan("Data Analysis", [Task(id="1.1", title="Variables", description="d", estimated_hours=1,
                                            deliverable="v.py", topic="Variables")])  # made with no known skills
g = user.build_graph(prof, [(Path("a.json"), sql), (Path("d.json"), python_course)])
names = {n.id: n.name for n in g.nodes}
claimed_edges = {(names[e.src], names[e.dst]) for e in g.edges if names[e.src] in prof.claimed_skills}
print("claimed edges:", claimed_edges)
# SQL's check tests SQL only; the Python course was created without known skills, so nothing feeds it.
assert claimed_edges == {("SQL", "sql basics")}, claimed_edges

print("ALL OK")
