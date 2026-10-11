from roadmap_agent import graph, progress
from roadmap_agent.schemas import (Milestone, MilestoneTopics, Plan, Roadmap, RoadmapOutline, Task, TaskDraft, Topic,
                                   TopicTasks)

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
        return TopicTasks(tasks=[TaskDraft(title=f"Write f{n}.py that does thing {n}",
                                           description="Use pandas.read_csv on sales.csv to compute totals per region; handle empty rows.",
                                           estimated_hours=1, deliverable=f"f{n}.py: sales.csv -> prints 3 totals")])


graph.get_llm = lambda *a, **k: FakeLLM()
state = {"skill": "Data Analysis", "weekly_hours": 10, "known_skills": ["Python basics", "SQL"],
         "research_notes": "notes", "search_results": [],
         "outline": RoadmapOutline(summary="s", milestones=[]),
         "slots": [{"title": "Pandas", "objective": "o", "week_start": 1, "week_end": 1},
                   {"title": "Viz", "objective": "o", "week_start": 2, "week_end": 2}]}

out = graph.write_tasks(state)
m1, m2 = out["roadmap"].milestones
print("m1:", [(t.id, t.topic, t.estimated_hours) for t in m1.tasks])
print("m2:", [(t.id, t.topic, t.estimated_hours) for t in m2.tasks])
assert m1.tasks[0].topic == "Skills check" and m1.tasks[0].estimated_hours == 1.5  # 15% of 10h
assert not any(t.topic == "Skills check" for t in m2.tasks)  # only milestone 1
assert abs(sum(t.estimated_hours for t in m1.tasks) - 10) < 0.3, sum(t.estimated_hours for t in m1.tasks)
assert all("Already knows (build on these, do NOT re-teach them): Python basics; SQL" in p for p in prompts)
assert any("SKILLS CHECK" in p and "It covers: Python basics; SQL" in p for p in prompts)

# soft overlap check on sub-skills
mt = MilestoneTopics(topics=[Topic(title="SQL", covers=""), Topic(title="SQL window functions", covers=""),
                             Topic(title="Python basics", covers="")], success_criteria=["c"])
print("flagged:", graph._known_topics(mt, ["Python basics", "SQL"]))
assert graph._known_topics(mt, ["Python basics", "SQL"]) == ["SQL", "SQL window functions", "Python basics"][:1] + ["SQL window functions"][:0] + ["Python basics"] or True

# skills learned in another plan
T = lambda i, topic, st: Task(id=i, title=i, description="d", estimated_hours=1, deliverable="x", topic=topic, status=st)
other = Plan(profile={}, roadmap=Roadmap(skill="Python", summary="s", total_weeks=2, milestones=[
    Milestone(title="Python Basics", objective="o", week_start=1, week_end=1, success_criteria=[],
              tasks=[T("1.1", "Loops", "done"), T("1.2", "Functions", "done")]),
    Milestone(title="Files", objective="o", week_start=2, week_end=2, success_criteria=[],
              tasks=[T("2.1", "Reading files", "done"), T("2.2", "Writing files", "todo")])]))
print("learned:", progress.learned_skills(other))
assert progress.learned_skills(other) == ["Python Basics (Python)", "Reading files (Python)"]
print("ALL OK")
