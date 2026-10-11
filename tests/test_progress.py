"""Scheduling, skip / short attempt / real failure, helper tasks, splitting, milestone gating. No LLM needed."""

from datetime import date

from roadmap_agent import progress
from roadmap_agent.schemas import Milestone, Plan, Roadmap, Task

MON = date(2026, 10, 5)
TUE, WED, FRI, NEXT_MON = date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 9), date(2026, 10, 12)


def T(i, deps=(), h=2):
    return Task(id=i, title=f"task {i}", description="d", estimated_hours=h, deliverable="x", depends_on=list(deps))


def fresh():
    m1 = Milestone(title="Pandas", objective="o", week_start=1, week_end=1, success_criteria=["c"],
                   tasks=[T("1.1"), T("1.2", ["1.1"], h=4), T("1.3"), T("1.4", ["1.2"])])
    m2 = Milestone(title="Viz", objective="o", week_start=2, week_end=2, success_criteria=["c"], tasks=[T("2.1")])
    return Plan(profile={"skill": "X", "weekly_hours": 10, "days_per_week": 5, "start_date": MON.isoformat()},
                roadmap=Roadmap(skill="X", summary="s", total_weeks=2, milestones=[m1, m2]))


def order(plan):
    return [t.id for t in plan.roadmap.milestones[0].tasks if t.status != "done"]


def test_progress():
    # schedule: 2h/day, 1.2 (4h) spans two days, the weekend is skipped
    plan = fresh()
    s = progress.schedule(plan, MON)
    assert s["1.1"] == (MON, MON) and s["1.2"] == (TUE, WED) and s["2.1"] == (NEXT_MON, NEXT_MON), s

    # skip: didn't start 1.1 on Monday -> Tuesday, everything after shifts; order unchanged
    progress.skip(plan, "1.1", MON)
    s = progress.schedule(plan, MON)
    assert s["1.1"] == (TUE, TUE) and s["1.2"][0] == WED, s
    assert order(plan) == ["1.1", "1.2", "1.3", "1.4"]

    # short attempt: 1h of 4h -> continue next study day, no helpers, 3h left
    progress.mark_done(plan, "1.1", TUE)
    action, added = progress.mark_failed(plan, "1.2", 1, TUE)
    _, t = progress.find(plan, "1.2")
    assert action == "continue" and not added and t.not_before == WED and progress.remaining_hours(t) == 3

    # real failure (4h total >= 75%): helper first, independent 1.3 ahead of the retry, dependent 1.4 behind
    action, added = progress.mark_failed(plan, "1.2", 3, WED)
    assert action == "helpers" and [a.id for a in added] == ["1.2r1"], (action, added)
    assert order(plan) == ["1.2r1", "1.3", "1.2", "1.4"], order(plan)
    assert progress.find(plan, "1.2")[1].spent_hours == 0  # the retry starts fresh

    # second real failure (helper not done) -> stale helper dropped, task split into chained steps
    action, added = progress.mark_failed(plan, "1.2", 4, WED)
    assert action == "split" and order(plan) == ["1.2s1", "1.2s2", "1.3", "1.2", "1.4"], order(plan)
    assert progress.find(plan, "1.2s2")[1].depends_on == ["1.2s1"]

    # finish everything in order; milestone 2 opens only after milestone 1; points are awarded
    for i in ["1.2s1", "1.2s2", "1.3", "1.2", "1.4"]:
        assert progress.next_task(plan).id == i, (progress.next_task(plan).id, i)
        progress.mark_done(plan, i, FRI)
    assert progress.next_task(plan).id == "2.1"
    assert all(t.points is not None for t in plan.roadmap.milestones[0].tasks)


if __name__ == "__main__":
    test_progress()
    print("ALL OK")
