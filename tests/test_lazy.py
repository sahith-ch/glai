import os
from datetime import date

from roadmap_agent import graph, progress
from roadmap_agent.schemas import MilestoneTopics, Plan, RoadmapOutline, TaskDraft, Topic, TopicTasks

prompts = []


class FakeLLM:
    def with_structured_output(self, schema):
        self.schema = schema
        return self

    def invoke(self, prompt):
        prompts.append(prompt)
        n = len(prompts)
        if self.schema is MilestoneTopics:
            return MilestoneTopics(topics=[Topic(title=f"Topic {n}", covers="x"), Topic(title=f"Project {n}", covers="y")],
                                   success_criteria=["c"])
        return TopicTasks(tasks=[TaskDraft(title=f"Write f{n}.py that totals sales {n}",
                                           description="Use pandas.read_csv on sales.csv to compute totals per region; handle empty rows.",
                                           estimated_hours=5, deliverable=f"f{n}.py: sales.csv -> prints 3 totals")])


graph.get_llm = lambda *a, **k: FakeLLM()
slots = [{"title": t, "objective": "o", "week_start": w, "week_end": w} for t, w in [("Pandas", 1), ("Viz", 2), ("Models", 3)]]
state = {"skill": "DA", "weekly_hours": 10, "research_notes": "n", "search_results": [{"url": "https://u", "title": "T"}],
         "outline": RoadmapOutline(summary="s", milestones=[]), "slots": slots}

rm = graph.write_tasks(state)["roadmap"]
print("written:", [m.tasks_written for m in rm.milestones], "tasks:", [len(m.tasks) for m in rm.milestones])
assert [m.tasks_written for m in rm.milestones] == [True, False, False]

plan = Plan(profile={"skill": "DA", "weekly_hours": 10, "days_per_week": 5, "start_date": "2026-10-12"},
            sources=["https://u"], source_titles={"https://u": "T"}, roadmap=rm)
MON = date(2026, 10, 12)
s = progress.schedule(plan, MON)
print("schedule:", {k: (a.isoformat(), b.isoformat()) for k, (a, b) in s.items()})
assert "M2" in s and "M3" in s and s["M3"][1] == date(2026, 10, 30)  # 3 weeks x 10h ends Fri week 3
pr = progress.projection(plan, MON)
assert pr["left_hours"] == 30 and pr["slip_days"] <= 0, pr

# nothing to write while plenty of milestone 1 is left
assert progress.milestones_to_write(plan) == [] or len(rm.milestones[0].tasks) <= 2
m1 = rm.milestones[0]
for t in m1.tasks[:-2]:
    progress.mark_done(plan, t.id, MON)
print("to write with 2 left:", progress.milestones_to_write(plan))
assert progress.milestones_to_write(plan) == [1]

plan.roadmap.milestones[0].tasks[-1].fail_count = 1  # pretend the learner struggled with it
graph.fill_milestone(plan, 1)
m2 = plan.roadmap.milestones[1]
print("m2 now:", m2.tasks_written, [(t.id, t.topic, t.estimated_hours) for t in m2.tasks])
assert m2.tasks_written and m2.tasks and abs(sum(t.estimated_hours for t in m2.tasks) - 10) < 0.3
assert all(t.id.startswith("2.") for t in m2.tasks)
assert "Found hard so far" in prompts[-1] and m1.tasks[-1].title in prompts[-1]
assert "https://u" in prompts[-1]  # sources survive the save/reload path

for t in m1.tasks:
    progress.mark_done(plan, t.id, MON)
assert progress.current_milestone(plan) is m2 and progress.next_task(plan).id == "2.1"
print("ALL OK")
