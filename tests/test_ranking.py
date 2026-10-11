"""Global ranking: by points earned from tested work, ties broken fairly, tiers and areas computed from evidence."""

from roadmap_agent import ranking
from roadmap_agent.schemas import SkillGraph, SkillNode, TaskEvidence, UserProfile, UserStats


def task(i, points, roadmap="r"):
    return TaskEvidence(id=i, title=f"T{i}", description="d", deliverable="x", difficulty="easy", points=points, roadmap=roadmap)


def person(name, points, tasks_done=1, first_try=0.5, review=70.0, nodes=()):
    return ranking.Learner(name.lower(), UserProfile(
        name=name, stats=UserStats(points=points, tasks_done=tasks_done, first_try_rate=first_try, avg_code_score=review),
        skill_graph=SkillGraph(nodes=list(nodes))))


def node(name, tasks, parent=None, status="completed", kind="subskill"):
    return SkillNode(id=name.lower(), name=name, kind=kind, status=status, parent=parent, tasks=tasks)


sql_tasks = [task("1.1", 20), task("1.2", 30)]
people = [
    person("Ada", 300, nodes=[node("SQL joins", sql_tasks, parent="SQL databases"),
                              node("SQL databases", sql_tasks, kind="skill")]),  # same tasks under parent: once
    person("Bo", 300, tasks_done=5, nodes=[node("Fetch API and async/await", [task("2.1", 40)], parent="JavaScript")]),
    person("Cy", 120, nodes=[node("Pandas groupby", [task("3.1", 60)]), node("Window functions in SQL", [task("3.2", 15)])]),
    person("Di", 120),  # identical to Cy on every tie-breaker except name: shares the rank
    person("Ed", 0),
]
r = ranking.rank_all(people)
order = sorted(r, key=lambda k: (r[k]["overall"]["rank"], k))
print({k: (r[k]["overall"]["rank"], r[k]["overall"]["tier"]) for k in order})

# ties on points go to more tasks done; true ties share a rank
assert r["bo"]["overall"]["rank"] == 1 and r["ada"]["overall"]["rank"] == 2
assert r["cy"]["overall"]["rank"] == r["di"]["overall"]["rank"] == 3 and r["ed"]["overall"]["rank"] == 5
assert r["bo"]["overall"]["top_percent"] == 20 and r["ed"]["overall"]["top_percent"] == 100

# tiers by points, and what the next one needs
assert [r[k]["overall"]["tier"] for k in ("ada", "cy", "ed")] == ["Specialist", "Apprentice", "Newbie"]
assert r["cy"]["overall"]["next_tier"] == {"tier": "Specialist", "points_needed": 130}
assert ranking.next_tier(1500) is None

# areas: tasks counted once, only learners with work there rank, "Fetch API" is not backend
ada_sql = next(a for a in r["ada"]["areas"] if a["area"] == "SQL & databases")
assert ada_sql["points"] == 50 and ada_sql["rank"] == 1 and ada_sql["total"] == 2
assert {a["area"] for a in r["bo"]["areas"]} == {"Web frontend"}
assert {a["area"] for a in r["cy"]["areas"]} == {"Data analysis", "SQL & databases"}
assert r["ed"]["areas"] == []

board = ranking.leaderboard(people, "SQL & databases")
assert [(e["name"], e["rank"], e["points"]) for e in board["entries"]] == [("Ada", 1, 50), ("Cy", 2, 15)]
try:
    ranking.leaderboard(people, "Underwater basket weaving")
    raise AssertionError("unknown area must be refused")
except ValueError:
    pass
print("ALL OK")
