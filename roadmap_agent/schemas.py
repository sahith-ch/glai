from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr


class SearchQueries(BaseModel):
    queries: list[str] = Field(description="4-6 distinct web search queries")


# Generation is split into small steps so a 3B local model can follow the rules:
# the LLM writes an outline, code allocates weeks/hours, then the LLM fills in
# tasks for one milestone at a time against an explicit hour budget.


class TaskDraft(BaseModel):
    """What the LLM writes. IDs and dependency links are assigned in code."""

    title: str
    description: str = Field(description="What to do, concretely")
    estimated_hours: float = Field(description="Realistic hours for a learner at this level")
    deliverable: str = Field(description="Tangible output proving the task is done")
    resources: list[str] = Field(
        default_factory=list,
        description="URLs taken ONLY from the research sources",
    )
    depends_on: list[int] = Field(
        default_factory=list,
        description="Numbers (1-based) of EARLIER tasks in this list that must be finished first; empty if none",
    )
    difficulty: Literal["easy", "medium", "hard"] = Field(
        default="medium",
        description="easy: one concept, guided steps; medium: combines a few concepts; "
        "hard: open-ended, multi-step or a mini project",
    )
    focus: Literal["deep", "practice", "light"] = Field(
        default="practice",
        description="What kind of time it needs: deep = long uninterrupted building or a project; "
        "practice = a focused exercise; light = reading, reviewing or a quick check that fits a short gap",
    )


# ---------------------------------------------------------------- evaluation


class CodeFile(BaseModel):
    name: str = Field(description="File name, e.g. control_flow_demo.py or sales.csv")
    content: str


class Check(BaseModel):
    test: str = Field(description="Name of the test function, e.g. test_invalid_age")
    verifies: str = Field(description="Plain-language sentence for the learner: what this test checks")
    quote: str = Field(description="Exact words copied from the task description or deliverable that require this")


class TaskTests(BaseModel):
    """What the LLM writes for a task: pytest tests, a reference solution that must pass them, sample data."""

    checks: list["Check"] = Field(description="One entry per test function")
    test_code: str = Field(description="Complete pytest file content")
    reference_files: list[CodeFile] = Field(description="A correct solution: the deliverable file(s)")
    wrong_files: list[CodeFile] = Field(
        default_factory=list,
        description="Same file names: a plausible INCORRECT solution making one typical mistake the task warns "
        "against (e.g. forgets the required ORDER BY or the error handling). At least one test must fail on it.",
    )
    data_files: list[CodeFile] = Field(default_factory=list, description="Small sample input files the task reads")


class StoredTests(TaskTests):
    deselected: list[str] = Field(default_factory=list)  # tests the reference solution failed: not used for grading
    required_files: list[str] = Field(default_factory=list)


class Review(BaseModel):
    score: int = Field(description="0-100 overall quality of the submission for this task")
    strengths: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list, description="Concrete improvements, most important first")


class Evaluation(BaseModel):
    at: str
    source: str  # folder path or repo URL (+ branch / subdir)
    passed: bool
    tests_passed: int
    tests_total: int
    failures: list[str] = Field(default_factory=list)  # short reason per failed test
    review: Review | None = None


class Task(BaseModel):
    id: str  # "2.3" = milestone 2, task 3; "2.3r1" remedial; "2.3s1" split step
    title: str
    description: str
    estimated_hours: float
    deliverable: str
    resources: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)  # task ids
    kind: Literal["core", "remedial", "step"] = "core"
    topic: str = ""  # sub-milestone this task belongs to
    difficulty: Literal["easy", "medium", "hard"] | None = None  # None on older plans: inferred (progress.difficulty)
    focus: Literal["deep", "practice", "light"] | None = None  # None on older plans: inferred (progress.focus)
    points: int | None = None  # awarded when the task is done (progress.award)
    # progress
    status: Literal["todo", "done", "failed"] = "todo"
    attempts: int = 0
    fail_count: int = 0  # real failures: tried for long enough and still couldn't do it
    spent_hours: float = 0  # on the current attempt
    not_before: date | None = None  # set when the learner didn't start it on its day
    tests: StoredTests | None = None  # generated on first submission / `tests` command
    evaluations: list[Evaluation] = Field(default_factory=list)


class MilestoneOutline(BaseModel):
    title: str = Field(description="Short topic name, no week numbers")
    objective: str = Field(description="What the learner can do after this milestone")
    estimated_weeks: int = Field(description="Realistic weeks for this milestone at the learner's pace")


class RoadmapOutline(BaseModel):
    summary: str
    prerequisites: list[str] = Field(default_factory=list)
    milestones: list[MilestoneOutline]


class Topic(BaseModel):
    title: str = Field(description="One narrow sub-skill of this milestone, e.g. 'if/elif/else conditions'")
    covers: str = Field(description="The exact concepts, functions or syntax this sub-skill includes")


class MilestoneTopics(BaseModel):
    """A milestone broken into sub-milestones; tasks are then written per topic."""

    topics: list[Topic] = Field(description="3-6 sub-skills in learning order; the LAST one is a mini project")
    success_criteria: list[str] = Field(description="How the learner knows they're ready to move on")


class TopicTasks(BaseModel):
    tasks: list[TaskDraft]


class RemedialTasks(BaseModel):
    tasks: list[TaskDraft] = Field(description="1-2 short catch-up tasks")


class SplitTasks(BaseModel):
    steps: list[TaskDraft] = Field(description="2-4 smaller steps that build up to the original deliverable")


class Milestone(BaseModel):
    title: str
    objective: str
    week_start: int
    week_end: int
    tasks: list[Task]
    success_criteria: list[str]
    tasks_written: bool = True  # False until the learner gets close to this milestone


class Roadmap(BaseModel):
    skill: str
    summary: str
    total_weeks: int
    prerequisites: list[str] = Field(default_factory=list)
    milestones: list[Milestone]


class Plan(BaseModel):
    """Roadmap plus everything needed to adapt it later (saved as JSON)."""

    profile: dict  # skill, current_level, weekly_hours, target_weeks, goal, start_date, days_per_week
    research_notes: str = ""
    sources: list[str] = Field(default_factory=list)  # URLs allowed as resources
    source_titles: dict[str, str] = Field(default_factory=dict)  # url -> page title, for later prompts
    roadmap: Roadmap
    history: list[dict] = Field(default_factory=list)
    # Hours available per day from the learner's calendar (calendar_plan.apply), when connected. Never saved:
    # it is a fact about this week, not about the plan.
    _capacity: dict | None = PrivateAttr(default=None)


# ---------------------------------------------------------------- user profile
# Interests and claimed skills are what the user tells us; the skill graph and stats are
# rebuilt from all roadmaps and their history (see user.rebuild), so they never drift.


class TaskEvidence(BaseModel):
    """A finished task, as shown to recruiters under the skill it earned."""

    id: str
    title: str
    description: str
    deliverable: str
    difficulty: str
    points: int
    completed_on: str | None = None
    kind: str = "core"  # core / remedial (helper) / step
    tests: str | None = None  # "3/3" from the latest code submission
    review_score: int | None = None
    source: str | None = None  # repo URL of the submission (local paths are not shared)
    roadmap: str = ""


class SkillNode(BaseModel):
    id: str  # normalized name, shared across roadmaps so the same skill merges
    name: str
    kind: Literal["claimed", "skill", "subskill"]  # claimed = told us; skill = a milestone; subskill = its topic
    status: Literal["claimed", "verified", "needs_review", "planned", "not_started", "in_progress", "completed"]
    progress: float = 0  # 0..1, share of core task hours done
    hours: float = 0  # hours of finished tasks
    tasks_done: int = 0
    tasks_total: int = 0
    failures: int = 0  # real failures (tried long enough and couldn't)
    helpers_used: int = 0  # catch-up / split-step tasks the plan had to add
    last_practiced: str | None = None  # ISO date
    acquired_on: str | None = None  # ISO date the last task needed to earn it was finished
    parent: str | None = None  # for a sub-skill: the skill (milestone) it belongs to
    roadmaps: list[str] = Field(default_factory=list)
    points: int = 0  # earned from the tasks that built this skill
    tasks: list["TaskEvidence"] = Field(default_factory=list)  # the finished tasks that earned it


class SkillEdge(BaseModel):
    src: str
    dst: str
    kind: Literal["prerequisite", "part_of"]  # src is a prerequisite of dst / src is part of dst


class SkillGraph(BaseModel):
    """Only skills the learner has acquired by completing tasks (see user.acquired)."""

    nodes: list[SkillNode] = Field(default_factory=list)
    edges: list[SkillEdge] = Field(default_factory=list)


class RoadmapStats(BaseModel):
    skill: str
    file: str
    progress: float  # share of planned hours done
    tasks_done: int
    tasks_total: int
    projected_end: str
    planned_end: str
    slip_days: int


class UserStats(BaseModel):
    hours_logged: float = 0
    tasks_done: int = 0
    failures: int = 0
    skips: int = 0
    helper_tasks: int = 0
    first_try_rate: float | None = None  # share of finished core tasks done without a real failure
    active_days: int = 0
    current_streak: int = 0  # consecutive study days with activity
    longest_streak: int = 0
    last_active: str | None = None
    skills_completed: int = 0
    skills_in_progress: int = 0
    submissions: int = 0
    submissions_passed: int = 0
    avg_code_score: float | None = None  # mean review score of each task's latest submission
    points: int = 0  # roadmap quests + arena challenges
    points_by_difficulty: dict[str, int] = Field(default_factory=dict)  # easy / medium / hard (quests)
    arena_points: int = 0
    arena_solved: int = 0
    roadmaps: list[RoadmapStats] = Field(default_factory=list)


class UserProfile(BaseModel):
    # demo = true freezes skill_graph and stats as written in the file (no rebuild from roadmaps)
    demo: bool = False
    name: str = ""
    headline: str = ""  # e.g. "Aspiring data analyst" - shown to recruiters
    location: str = ""
    contact: str = ""  # email or profile URL
    interests: list[str] = Field(default_factory=list)
    claimed_skills: list[str] = Field(default_factory=list)
    created: str = ""
    updated: str = ""
    skill_graph: SkillGraph = Field(default_factory=SkillGraph)
    stats: UserStats = Field(default_factory=UserStats)
