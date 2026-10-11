"""The learner app writes the next milestone after a submission, like the CLI does on every command."""

import tempfile
from datetime import date
from pathlib import Path

from roadmap_agent import graph, progress, submissions
from roadmap_agent.schemas import (
    Check, MilestoneTopics, Plan, RoadmapOutline, StoredTests, TaskDraft, Topic, TopicTasks,
)

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
                                           description="Use pandas.read_csv on sales.csv to compute totals per region.",
                                           estimated_hours=5, deliverable=f"f{n}.py: sales.csv -> prints 3 totals")])


graph.get_llm = lambda *a, **k: FakeLLM()
slots = [{"title": t, "objective": "o", "week_start": w, "week_end": w} for t, w in [("Pandas", 1), ("Viz", 2), ("Models", 3)]]
state = {"skill": "DA", "weekly_hours": 10, "research_notes": "n", "search_results": [{"url": "https://u", "title": "T"}],
         "outline": RoadmapOutline(summary="s", milestones=[]), "slots": slots}
rm = graph.write_tasks(state)["roadmap"]
plan = Plan(profile={"skill": "DA", "weekly_hours": 10, "days_per_week": 5, "start_date": "2026-10-12"},
            sources=["https://u"], source_titles={"https://u": "T"}, roadmap=rm)
MON = date(2026, 10, 12)
path = Path(tempfile.mkdtemp()) / "roadmap_da.json"

m1 = rm.milestones[0]
m1.tasks[0].fail_count = 1  # the learner struggled with this one
for t in m1.tasks[:-2]:
    progress.mark_done(plan, t.id, MON)
assert progress.milestones_to_write(plan) == [1]  # two tasks left: the next milestone is due

# a model failure is reported through on_event, never raised: the submission already counted
real_fill = graph.fill_milestone
def failing_fill(plan, index):
    raise ConnectionError("no network")
graph.fill_milestone = failing_fill
events = []
assert submissions.write_ahead(plan, path, lambda k, m, d: events.append(k)) == []
assert events == ["write_next", "write_failed"] and not rm.milestones[1].tasks_written
graph.fill_milestone = real_fill

before = len(prompts)
written = submissions.write_ahead(plan, path, lambda k, m, d: events.append(k))
print("written:", written)
assert written == ["Viz"] and rm.milestones[1].tasks_written and rm.milestones[1].tasks
assert any("Found hard so far" in p and m1.tasks[0].title in p for p in prompts[before:]), "struggles shape the next milestone"
assert submissions.write_ahead(plan, path) == []  # already written: nothing more to do

# the retry endpoint saves what it wrote, and says so when nothing could be written
for t in m1.tasks:
    if t.status != "done":
        progress.mark_done(plan, t.id, MON)
for t in rm.milestones[1].tasks[:-2]:
    progress.mark_done(plan, t.id, MON)
progress.save(plan, path)
assert submissions.write_due(path, MON)["written"] == ["Models"]
assert progress.load(path).roadmap.milestones[2].tasks_written

# tests_view names each graded test, so another app can map failures onto checks
t = m1.tasks[0]
t.tests = StoredTests(checks=[Check(test="test_totals", verifies="prints totals", quote="totals")],
                      test_code="", reference_files=[], deselected=[])
assert submissions.tests_view(t)["checks"] == [{"test": "test_totals", "verifies": "prints totals", "quote": "totals"}]

print("ALL OK")
