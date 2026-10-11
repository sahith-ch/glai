"""LangGraph agent: skill/course -> web research -> realistic roadmap.

Flow:
    plan_queries -> (fan-out) search x N -> synthesize -> outline -> validate -> schedule -> write_tasks
                                                            ^           |
                                                            +-- redo if checks fail
"""

import operator
import os
import re
from typing import Annotated, TypedDict

from langchain.chat_models import init_chat_model
from langchain_tavily import TavilySearch
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .quality import SPECIFIC_RULES, vague_issues
from .schemas import (
    Milestone,
    MilestoneTopics,
    Plan,
    Roadmap,
    RoadmapOutline,
    SearchQueries,
    Task,
    TaskDraft,
    Topic,
    TopicTasks,
)

MAX_REVISIONS = 2
WRITE_AHEAD = int(os.getenv("WRITE_AHEAD_MILESTONES", "1"))  # milestones whose tasks are written up front
MAX_SOURCE_CHARS = 600
MAX_SOURCES = 12  # keeps the synthesize prompt inside a small local context window


class State(TypedDict, total=False):
    # inputs
    skill: str
    current_level: str
    weekly_hours: int
    target_weeks: int | None
    goal: str
    known_skills: list[str]  # from --skills and from completed parts of other roadmaps
    slot_minutes: int | None  # the learner's usual free block, from their calendar (calendar_plan)
    # working state
    queries: list[str]
    search_results: Annotated[list[dict], operator.add]
    research_notes: str
    outline: RoadmapOutline
    slots: list[dict]
    roadmap: Roadmap
    issues: list[str]
    revisions: int


def get_llm(temperature: float = 0.2):
    provider = os.getenv("LLM_PROVIDER", "ollama")
    model = os.getenv("LLM_MODEL", "qwen2.5:3b")
    kwargs = {}
    if provider == "ollama":
        # Ollama's default context window silently truncates the research notes.
        kwargs["num_ctx"] = int(os.getenv("OLLAMA_NUM_CTX", "4096"))
        # small models sometimes loop forever repeating JSON; cap every answer
        kwargs["num_predict"] = int(os.getenv("OLLAMA_NUM_PREDICT", "2048"))
    elif provider == "anthropic":
        # Current Claude models think on every request and reject custom temperature; thinking tokens
        # count against max_tokens, so leave room. Effort is the depth control (Opus 5.5 default: medium).
        kwargs["max_tokens"] = 16000
        kwargs["output_config"] = {"effort": os.getenv("CLAUDE_EFFORT", "medium")}
        if os.getenv("CLAUDE_FALLBACKS", "1") != "0" and model.startswith(
            ("claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5")
        ):
            # if a safety classifier declines a request, the API reruns it on a fallback model in the same call
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["model_kwargs"] = {"fallbacks": "default"}
        return init_chat_model(model, model_provider=provider, **kwargs)
    else:
        kwargs["max_tokens"] = 4096
    if provider == "groq":
        # the free tier limits tokens per minute; many calls in a row hit 429s, so wait and retry instead of failing
        kwargs["max_retries"] = int(os.getenv("GROQ_MAX_RETRIES", "8"))
        kwargs["max_tokens"] = 8192  # gpt-oss reasons before answering, and reasoning counts against this
    if provider == "openai" and model.startswith(("gpt-5", "o1", "o3", "o4")):
        # OpenAI reasoning models only accept the default temperature
        return init_chat_model(model, model_provider=provider, **kwargs)
    return init_chat_model(model, model_provider=provider, temperature=temperature, **kwargs)


BAD_ANSWER = "Your previous answer was cut off or was not valid JSON. Answer with short, valid JSON only."


def structured(llm, schema):
    """`llm` constrained to return `schema`.

    Claude uses native structured outputs (output_config.format): current models reject the forced
    tool call that LangChain's default "function_calling" method relies on.
    """
    if getattr(llm, "_llm_type", "") == "anthropic-chat":
        return llm.with_structured_output(schema, method="json_schema")
    if getattr(llm, "_llm_type", "") == "groq-chat" and getattr(llm, "model_name", "").startswith("openai/gpt-oss"):
        # gpt-oss sometimes answers in prose instead of making the forced tool call (Groq 400 tool_use_failed)
        return llm.with_structured_output(schema, method="json_schema")
    return llm.with_structured_output(schema)


def ask(llm, schema, prompt: str):
    """Structured call that returns None instead of raising on a truncated or malformed answer."""
    try:
        return structured(llm, schema).invoke(prompt)
    except Exception as e:
        print(f"  (model gave an unusable answer: {type(e).__name__}; retrying)", flush=True)
        return None


def _learner_profile(state: State) -> str:
    weeks = state.get("target_weeks")
    return (
        f"Skill/course: {state['skill']}\n"
        f"Current level: {state.get('current_level', 'beginner')}\n"
        f"Time available: {state.get('weekly_hours', 8)} hours/week\n"
        f"Target duration: {f'{weeks} weeks' if weeks else 'whatever is realistic'}\n"
        f"Goal: {state.get('goal') or 'job-ready practical competence'}"
        + (
            f"\nAlready knows (build on these, do NOT re-teach them): {'; '.join(known)}"
            if (known := state.get("known_skills"))
            else ""
        )
        + (
            f"\nInterests (theme example data and projects around these where it fits naturally): {'; '.join(likes)}"
            if (likes := state.get("interests"))
            else ""
        )
        + (
            f"\nFound hard so far (needed extra help - add a little extra practice where new material relies on these): "
            f"{'; '.join(hard[-6:])}"
            if (hard := state.get("struggled"))
            else ""
        )
    )


# ---------------------------------------------------------------- nodes


def plan_queries(state: State) -> dict:
    result = ask(
        get_llm(),
        SearchQueries,
        "You are researching how people realistically learn a skill.\n"
        f"{_learner_profile(state)}\n\n"
        "Write 4-6 web search queries that together cover: the standard learning path / "
        "curriculum, how long it realistically takes, the best current resources, "
        "hands-on project ideas, and common mistakes. Make each query specific."
    )
    queries = [q for q in (result.queries if result else []) if q.strip()][:6] or [
        f"{state['skill']} learning roadmap",
        f"{state['skill']} beginner projects",
    ]
    return {"queries": queries}


def fan_out_searches(state: State) -> list[Send]:
    return [Send("search", {"query": q}) for q in state["queries"]]


def search(payload: dict) -> dict:
    tool = TavilySearch(max_results=int(os.getenv("TAVILY_MAX_RESULTS_PER_QUERY", "3")), search_depth="advanced")
    try:
        response = tool.invoke({"query": payload["query"]})
    except Exception as e:  # one failed query shouldn't kill the run
        print(f"  search failed for {payload['query']!r}: {e}")
        return {"search_results": []}
    results = [
        {
            "query": payload["query"],
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": (r.get("content") or "")[:MAX_SOURCE_CHARS],
        }
        for r in response.get("results", [])
    ]
    return {"search_results": results}


def synthesize(state: State) -> dict:
    # de-dupe by URL, parallel searches often overlap
    seen, sources = set(), []
    for r in state.get("search_results", []):
        if r["url"] and r["url"] not in seen:
            seen.add(r["url"])
            sources.append(r)
    sources = sources[:MAX_SOURCES]

    source_block = "\n\n".join(
        f"[{i}] {s['title']}\nURL: {s['url']}\n{s['content']}" for i, s in enumerate(sources, 1)
    )
    notes = get_llm().invoke(
        f"{_learner_profile(state)}\n\n"
        f"Research sources:\n{source_block or '(no sources found)'}\n\n"
        "Condense these sources into research notes for building a learning roadmap:\n"
        "1. Core topics in the order they should be learned\n"
        "2. Realistic time estimates the sources mention (quote numbers)\n"
        "3. Recommended resources, each with its exact URL from the sources above\n"
        "4. Hands-on project ideas, from simple to advanced\n"
        "5. Common pitfalls\n"
        "Only use information from the sources. Never invent URLs."
    ).text  # not .content: with thinking on (Claude), content is a list of thinking + text blocks
    return {"research_notes": notes}


def outline(state: State) -> dict:
    issues = state.get("issues") or []
    feedback = ""
    if issues:
        feedback = (
            "\n\nYour previous outline was rejected for these reasons. Fix ALL of them:\n- "
            + "\n- ".join(issues)
        )
    weeks = state.get("target_weeks")
    weeks_rule = (
        f"- The whole roadmap must fit in {weeks} weeks; milestone weeks should add up to about {weeks}.\n"
        if weeks
        else "- Give each milestone realistic weeks at the learner's pace.\n"
    )

    result = ask(
        get_llm(),
        RoadmapOutline,
        "You are an expert curriculum designer. Outline a REALISTIC learning roadmap.\n\n"
        f"{_learner_profile(state)}\n\n"
        f"Research notes:\n{state['research_notes']}\n\n"
        "Rules:\n"
        "- 4-7 milestones, in learning order, each ending with something the learner built.\n"
        "- Start from what the learner already knows: no milestones for skills they already have.\n"
        "- Do NOT put week numbers in titles.\n"
        f"{weeks_rule}"
        "- Only list milestones here; tasks are written later."
        f"{feedback}"
    )
    if result is None:  # validate() then reports 0 milestones and asks for a redo
        result = RoadmapOutline(summary="", milestones=[])
    return {"outline": result, "revisions": state.get("revisions", 0) + 1}


def validate(state: State) -> dict:
    """Deterministic checks on the outline, limited to things the LLM can actually fix."""
    ol: RoadmapOutline = state["outline"]
    issues: list[str] = []
    n = len(ol.milestones)
    if not 4 <= n <= 7:
        issues.append(f"You made {n} milestones; make 4-7 (merge or split topics).")
    return {"issues": issues}


def route_after_validate(state: State) -> str:
    if state.get("issues") and state.get("revisions", 0) <= MAX_REVISIONS:
        return "outline"
    return "schedule"


def _clean_title(title: str) -> str:
    return re.sub(r"^\s*(week|weeks)\s*[\d\s\-–]+[:.\-–]?\s*", "", title, flags=re.I) or title


def schedule(state: State) -> dict:
    """Allocate contiguous week ranges in code, scaled to target_weeks if one was given."""
    ol: RoadmapOutline = state["outline"]
    est = [max(1, m.estimated_weeks) for m in ol.milestones]
    target = state.get("target_weeks")
    scale = target / sum(est) if target and est else 1.0

    slots, cum, next_week = [], 0.0, 1
    for m, w in zip(ol.milestones, est):
        cum += w * scale
        # end week follows the cumulative total, so rounding errors don't pile up
        end = max(next_week, round(cum))
        slots.append({"title": _clean_title(m.title), "objective": m.objective, "week_start": next_week, "week_end": end})
        next_week = end + 1
    return {"slots": slots}


PROJECT_WEIGHT = 1.5  # the closing mini project gets more hours than a single sub-skill


def _rejected(issues: list[str]) -> str:
    return "\n\nYour previous answer was rejected. Fix ALL of these:\n- " + "\n- ".join(issues) if issues else ""


def _sources_block(state: State) -> str:
    seen, lines = set(), []
    for r in state.get("search_results", []):
        if r["url"] and r["url"] not in seen and len(lines) < MAX_SOURCES:
            seen.add(r["url"])
            lines.append(f"- {r['title']}: {r['url']}")
    return "\n".join(lines) or "(none)"


_STOP = {"and", "or", "the", "a", "an", "of", "with", "in", "to", "for", "using", "basic", "basics",
         "introduction", "intro", "fundamentals", "python"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9+#]+", text.lower()) if w not in _STOP and len(w) > 1}


def _known_topics(mt: MilestoneTopics, known: list[str]) -> list[str]:
    """Sub-skills that are essentially a skill the learner already listed (soft check, feeds a redraft)."""
    hits = []
    for t in mt.topics:
        tw = _words(t.title)
        if any((kw := _words(k)) and kw <= tw and len(tw - kw) <= 1 for k in known):
            hits.append(t.title)
    return hits


def _topic_issues(mt: MilestoneTopics, known: list[str]) -> list[str]:
    issues = []
    if hits := _known_topics(mt, known):
        issues.append(f"The learner already knows these, replace them with new sub-skills: {hits}")
    if not 3 <= len(mt.topics) <= 6:
        issues.append(f"You listed {len(mt.topics)} sub-skills; list 3-6, the last one a mini project.")
    if not mt.success_criteria:
        issues.append("Add 2-4 success_criteria.")
    return issues


_FILE = re.compile(r"[\w-]+\.(?:py|ipynb|csv|sql|xlsx|png|html)", re.I)


def _is_repeat(title: str, covered: list[str]) -> bool:
    """Same title, or builds a file an earlier task already built."""
    norm = _clean_title(title).lower()
    files = _file_stems(title)
    return any(norm == _clean_title(c).lower() or files & _file_stems(c) for c in covered)


def _file_stems(text: str) -> set[str]:
    """clean_data.py, clean_data_2.py and clean-data-v3.py all count as the same file."""
    return {re.sub(r"([_-]?(v|part)?\d{1,2})?\.\w+$", "", f.lower()) for f in _FILE.findall(text)}


def _tasks_issues(tt: TopicTasks, budget: float, covered: list[str]) -> list[str]:
    issues = []
    repeats = [t.title for t in tt.tasks if _is_repeat(t.title, covered)]
    if repeats:
        issues.append(f"These tasks repeat earlier ones, replace them with new material: {repeats}")
    if not 1 <= len(tt.tasks) <= 4:
        issues.append(f"You wrote {len(tt.tasks)} tasks; write 1-4.")
    issues += vague_issues(tt.tasks)
    total = sum(max(t.estimated_hours, 0) for t in tt.tasks)
    if not 0.7 * budget <= total <= 1.3 * budget:
        issues.append(f"Task hours add up to {total:g}h but this sub-skill has {budget:g}h.")
    return issues


def to_task(d: TaskDraft, task_id: str, local_ids: dict[int, str], pos: int, known_urls: set[str], **extra) -> Task:
    """Assign an id, map the LLM's 1-based backward links to task ids, drop invented URLs."""
    deps = sorted({local_ids[k] for k in d.depends_on if isinstance(k, int) and 1 <= k < pos and k in local_ids})
    return Task(
        id=task_id,
        **d.model_dump(exclude={"title", "depends_on", "resources"}),
        title=_clean_title(d.title),
        resources=[u for u in d.resources if u in known_urls],
        depends_on=deps,
        **extra,
    )


def _milestone_topics(state: State, llm, i: int, slot: dict, covered: list[str]) -> tuple[MilestoneTopics, list[str]]:
    issues: list[str] = []
    for _ in range(MAX_REVISIONS + 1):
        mt = ask(
            llm,
            MilestoneTopics,
            "You are an expert curriculum designer. Break ONE milestone of a learning roadmap into sub-skills.\n\n"
            f"{_learner_profile(state)}\n\n"
            f"Research notes:\n{state['research_notes']}\n\n"
            f"Milestone {i} of {len(state['slots'])}: {slot['title']}\n"
            f"Objective: {slot['objective']}\n"
            f"Already covered by earlier milestones (do NOT repeat): {'; '.join(covered[-15:]) or 'nothing yet'}\n\n"
            "Rules:\n"
            "- 3-6 narrow sub-skills that belong to THIS milestone's topic only, in learning order.\n"
            "- 'covers' names the exact concepts, functions or syntax, e.g. 'if, elif, else, comparison operators, and/or'.\n"
            "- Skip anything the learner already knows (listed above); build on it instead.\n"
            "- The LAST sub-skill is a mini project that combines the others."
            f"{_rejected(issues)}"
        )
        if mt is None:
            issues = [BAD_ANSWER]
            continue
        issues = _topic_issues(mt, state.get("known_skills") or [])
        if not issues:
            break
    if mt is None:
        mt = MilestoneTopics(topics=[], success_criteria=[slot["objective"]])
    if not mt.topics:  # never leave a milestone empty
        mt.topics = [Topic(title=slot["title"], covers=slot["objective"])]
    return mt, issues


_MODE_RULES = {
    "normal": "- Stay on this sub-skill only; don't jump ahead to later topics.\n",
    "project": "- This is the milestone's mini project: one realistic program or analysis that uses all "
    "the earlier sub-skills of this milestone.\n",
    "check": "- This is a quick SKILLS CHECK of what the learner says they already know: 1-2 short tasks "
    "(about 1h each) that prove they can do those skills at the level this roadmap builds on. "
    "If they can't, the plan adds catch-up work.\n",
}


def _slot_rule(state: State) -> str:
    """Size tasks to the learner's real free time, when their calendar says what it looks like."""
    minutes = state.get("slot_minutes")
    if not minutes:
        return ""
    return (f"- The learner's free time usually comes in blocks of about {minutes} minutes. Make most tasks "
            f"doable in one or two such blocks; split bigger work into steps. Set focus: deep for work that "
            f"needs a long uninterrupted block, light for reading or a quick check that fits a short gap.\n")


def _topic_tasks(state: State, llm, slot: dict, topic: Topic, budget: float, covered: list[str], mode: str = "normal"):
    issues: list[str] = []
    n = max(1, min(4, round(budget / 3)))
    for _ in range(MAX_REVISIONS + 1):
        tt = ask(
            llm,
            TopicTasks,
            "You write hands-on tasks for ONE sub-skill of a learning roadmap.\n\n"
            f"{_learner_profile(state)}\n"
            f"Milestone: {slot['title']} - {slot['objective']}\n"
            f"Sub-skill: {topic.title}\n"
            f"It covers: {topic.covers}\n"
            f"Hours for this sub-skill: {budget:g}\n"
            f"Already done earlier (do NOT repeat): {'; '.join(covered[-12:]) or 'nothing yet'}\n\n"
            "Possible resources (copy a URL only if it fits; otherwise leave resources empty):\n"
            f"{_sources_block(state)}\n\n"
            "Rules:\n"
            f"- {n} task(s) that practise exactly this sub-skill, hours adding up to about {budget:g}.\n"
            + _MODE_RULES[mode]
            + _slot_rule(state)
            + "- depends_on lists the numbers of earlier tasks in YOUR list that must be done first.\n\n"
            f"{SPECIFIC_RULES}"
            f"{_rejected(issues)}"
        )
        if tt is None:
            issues = [BAD_ANSWER]
            continue
        issues = _tasks_issues(tt, budget, covered)
        if not issues:
            break
    if tt is None or not tt.tasks:  # model never produced anything usable: a plain fallback task
        tt = TopicTasks(
            tasks=[
                TaskDraft(
                    title=f"Write {re.sub(r'[^a-z0-9]+', '_', topic.title.lower()).strip('_')}.py practising {topic.title}",
                    description=f"Write one small program that uses each of: {topic.covers}. Run it on made-up data.",
                    estimated_hours=budget,
                    deliverable="The program runs and prints a result for every concept listed.",
                )
            ]
        )
    drafts = [d for d in tt.tasks if d.title.strip()][:4]
    # drop repeats the model didn't fix (keep one task so the sub-skill isn't lost); hours are redistributed below
    fresh = [d for d in drafts if not _is_repeat(d.title, covered)]
    tt.tasks = fresh or drafts[:1]
    # hours always match the sub-skill budget, so the week schedule stays honest
    total = sum(max(d.estimated_hours, 0) for d in tt.tasks)
    for d in tt.tasks:
        d.estimated_hours = round(budget / len(tt.tasks) if total <= 0 else max(d.estimated_hours, 0) * budget / total, 1)
    return tt.tasks, [x for x in issues if not x.startswith("Task hours")]


def write_milestone(ctx: dict, llm, i: int, covered: list[str]) -> tuple[list[Task], list[str], list[str]]:
    """Sub-skills, then a few specific tasks per sub-skill, for milestone i (1-based).

    `ctx` is the graph state at creation time, or one rebuilt from a saved Plan later (see fill_milestone).
    Returns (tasks, success_criteria, unresolved issues). `covered` is extended with the new task titles.
    """
    slot = ctx["slots"][i - 1]
    weekly = ctx.get("weekly_hours", 8)
    known_urls = {r["url"] for r in ctx.get("search_results", [])}
    budget = (slot["week_end"] - slot["week_start"] + 1) * weekly
    leftover: list[str] = []

    mt, issues = _milestone_topics(ctx, llm, i, slot, covered)
    leftover += [f"'{slot['title']}': {x}" for x in issues]
    print(f"  milestone {i}/{len(ctx['slots'])} {slot['title']}: {len(mt.topics)} sub-skills", flush=True)

    weights = [1.0] * (len(mt.topics) - 1) + [PROJECT_WEIGHT if len(mt.topics) > 1 else 1.0]
    plan_topics = [(t, round(budget * w / sum(weights), 1), "normal") for t, w in zip(mt.topics, weights)]
    if len(mt.topics) > 1:
        plan_topics[-1] = (plan_topics[-1][0], plan_topics[-1][1], "project")
    known = ctx.get("known_skills") or []
    if i == 1 and known:  # trust claimed skills, but check them cheaply up front
        check_h = min(2.0, round(0.15 * budget, 1))
        scale = (budget - check_h) / budget
        plan_topics = [(Topic(title="Skills check", covers="; ".join(known)), check_h, "check")] + [
            (t, round(h * scale, 1), m) for t, h, m in plan_topics
        ]

    tasks: list[Task] = []
    topic_last: list[str] = []  # last task id of each sub-skill
    for topic, hours, mode in plan_topics:
        is_project = mode == "project"
        drafts, issues = _topic_tasks(ctx, llm, slot, topic, hours, covered, mode)
        leftover += [f"'{slot['title']} / {topic.title}': {x}" for x in issues]
        print(f"    - {topic.title}: {len(drafts)} task(s)", flush=True)

        local_ids: dict[int, str] = {}
        for pos, d in enumerate(drafts, 1):
            task_id = f"{i}.{len(tasks) + 1}"
            local_ids[pos] = task_id
            t = to_task(d, task_id, local_ids, pos, known_urls, topic=_clean_title(topic.title))
            if is_project and pos == 1:  # the mini project needs every earlier sub-skill
                t.depends_on = sorted(set(t.depends_on) | set(topic_last))
            tasks.append(t)
            covered.append(t.title)
        if drafts:
            topic_last.append(tasks[-1].id)
    return tasks, mt.success_criteria, leftover


def write_tasks(state: State) -> dict:
    """Write tasks for the first WRITE_AHEAD milestones now; the rest are written when the learner gets close."""
    ol: RoadmapOutline = state["outline"]
    llm = get_llm()
    milestones, leftover, covered = [], [], []
    for i, slot in enumerate(state["slots"], 1):
        if i <= WRITE_AHEAD:
            tasks, criteria, issues = write_milestone(state, llm, i, covered)
            leftover += issues
            milestones.append(Milestone(**slot, tasks=tasks, success_criteria=criteria))
        else:
            milestones.append(Milestone(**slot, tasks=[], success_criteria=[], tasks_written=False))

    roadmap = Roadmap(
        skill=state["skill"],
        summary=ol.summary,
        prerequisites=ol.prerequisites,
        total_weeks=milestones[-1].week_end if milestones else 0,
        milestones=milestones,
    )
    return {"roadmap": roadmap, "issues": (state.get("issues") or []) + leftover}


def fill_milestone(plan: Plan, index: int) -> list[str]:
    """Write tasks for an unwritten milestone of a saved plan (0-based index), using what the learner did so far."""
    m = plan.roadmap.milestones[index]
    done_titles = [t.title for x in plan.roadmap.milestones for t in x.tasks]
    struggled = [t.title for x in plan.roadmap.milestones for t in x.tasks if t.fail_count]
    ctx = {
        **plan.profile,
        "research_notes": plan.research_notes,
        "search_results": [{"url": u, "title": plan.source_titles.get(u, "")} for u in plan.sources],
        "slots": [
            {"title": x.title, "objective": x.objective, "week_start": x.week_start, "week_end": x.week_end}
            for x in plan.roadmap.milestones
        ],
        "struggled": struggled,
    }
    tasks, criteria, issues = write_milestone(ctx, get_llm(), index + 1, list(done_titles))
    m.tasks, m.success_criteria, m.tasks_written = tasks, criteria, True
    return issues


# ---------------------------------------------------------------- graph


def build_graph():
    g = StateGraph(State)
    g.add_node("plan_queries", plan_queries)
    g.add_node("search", search)
    g.add_node("synthesize", synthesize)
    g.add_node("outline", outline)
    g.add_node("validate", validate)
    g.add_node("schedule", schedule)
    g.add_node("write_tasks", write_tasks)

    g.add_edge(START, "plan_queries")
    g.add_conditional_edges("plan_queries", fan_out_searches, ["search"])
    g.add_edge("search", "synthesize")
    g.add_edge("synthesize", "outline")
    g.add_edge("outline", "validate")
    g.add_conditional_edges("validate", route_after_validate, ["outline", "schedule"])
    g.add_edge("schedule", "write_tasks")
    g.add_edge("write_tasks", END)
    return g.compile()


graph = build_graph()
