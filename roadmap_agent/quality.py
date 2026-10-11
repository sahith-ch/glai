"""What a *specific* task looks like, shared by every prompt that writes tasks, plus a cheap check for vague ones."""

import re

MAX_TASK_HOURS = 4  # one or two study sessions; bigger tasks hide vagueness

SPECIFIC_RULES = f"""Every task must be SPECIFIC - something the learner can start immediately and check when finished:
- title: starts with an action verb and names what gets built.
  NEVER start with Understand / Learn / Study / Explore / Introduction / Overview / Review / Read / Watch.
- description: the exact steps - which functions, syntax or methods to use, what input or dataset, what the output looks like.
- deliverable: a named file or output plus a check that proves it works (input -> expected output).
- estimated_hours between 1 and {MAX_TASK_HOURS}; split anything bigger into several tasks.
- difficulty: easy (one concept, guided steps), medium (combines a few concepts), hard (open-ended,
  multi-step, or a mini project). Rate honestly: harder tasks earn the learner more points.

Pattern (fill it in for THIS sub-skill with your own file names and values - never reuse the placeholder words):
  title       "Write <file>.py that <does one concrete thing using the sub-skill>"
  description "Use <exact functions/syntax> on <specific input or dataset> to <produce what>; handle <edge case>."
  deliverable "<file>.py: <input> -> <expected output>, <second input> -> <expected output>"
BAD (vague): title "Understand <topic>", deliverable "A simple script"
"""

_VAGUE_START = re.compile(
    r"^\s*(understand|understanding|learn|learning|study|explore|familiari[sz]e|get familiar|introduction|intro\b|"
    r"overview|basics|review|read|watch|know|grasp|master)\b",
    re.I,
)
# a deliverable should name something tangible: a file, a notebook, a chart, a function ...
_CONCRETE = re.compile(
    r"\.\w{1,5}\b|notebook|script|file|function|class|chart|plot|graph|figure|table|csv|dataframe|report|"
    r"dashboard|repo|program|app|query|screenshot|output|prints?|returns?|->",
    re.I,
)
_VAGUE_DELIVERABLE = re.compile(r"\b(understanding|knowledge|familiarity|awareness)\b", re.I)


def vague_issues(tasks) -> list[str]:
    """Feedback for the LLM about tasks that aren't specific enough (works on TaskDraft or Task)."""
    vague_titles = [t.title for t in tasks if _VAGUE_START.search(t.title) or "<" in t.title]
    vague_deliv = [
        t.title for t in tasks if not _CONCRETE.search(t.deliverable) or _VAGUE_DELIVERABLE.search(t.deliverable)
    ]
    thin = [t.title for t in tasks if len(t.description) < 60]
    too_big = [t.title for t in tasks if t.estimated_hours > MAX_TASK_HOURS]

    issues = []
    if vague_titles:
        issues.append(f"Titles must start with an action verb and name what gets built, not 'Understand/Learn...': {vague_titles}")
    if vague_deliv:
        issues.append(f"Deliverables must name a file/output and a check that proves it works: {vague_deliv}")
    if thin:
        issues.append(f"Descriptions must give the exact steps, functions and data to use: {thin}")
    if too_big:
        issues.append(f"Tasks over {MAX_TASK_HOURS}h must be split into smaller specific tasks: {too_big}")
    return issues
