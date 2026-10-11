"""Hiring search: roles expand into the skills they take, plain-English queries split, ranking follows evidence.
Runs on the demo candidates in keyword mode, so it needs no models."""

from datetime import date
from pathlib import Path

from roadmap_agent import cloud, hire, vector

vector.get = lambda: None  # keyword matching only: deterministic and offline
cloud.public_others = lambda root: None  # the local demo candidates, even when this machine is signed in to GLAI Hire

root = Path(__file__).resolve().parent.parent
cands = [c for c in hire.load_candidates(root, date(2026, 10, 12)) if c.id.startswith("demo_")]
assert len(cands) >= 6, "the demo candidates are the fixture"

# --- understanding the query
parsed = [(t.kind, t.text, t.skills[:2]) for t in hire.parse_query(
    "junior frontend developer with react, sql and python, data structures and algorithms, rust developer",
    known=["Data structures and algorithms"])]
print(parsed)
assert parsed[0][0] == "role" and parsed[0][2] == ["html", "css"], "a role expands into its skills; 'junior' is dropped"
assert ("skill", "react", ["react"]) in parsed and ("skill", "python", ["python"]) in parsed, "'with' / 'and' split"
assert ("skill", "data structures and algorithms", ["data structures and algorithms"]) in parsed, "a known skill stays whole"
assert ("skill", "rust developer", ["rust"]) in parsed, "an unlisted role is its skill"
known = ["SQL", "Pandas", "Machine learning", "Data visualisation"]
assert [t.text for t in hire.parse_query("sql pandas", known)] == ["sql", "pandas"], "plain spaces split known skills"
assert [t.text for t in hire.parse_query("data visualisation pandas", known)] == ["data visualisation", "pandas"]
assert [t.text for t in hire.parse_query("machine learning", known)] == ["machine learning"], "a skill phrase stays whole"
assert [t.text for t in hire.parse_query("kubernetes helm", known)] == ["kubernetes helm"], "unknown words stay as typed"


def names(query):
    return [r["name"].split()[0] for r in hire.search_payload(cands, query)["results"]]


assert names("sql pandas"), "a space-separated query finds people in keyword mode"


# --- roles are matched on EARNED skills, not on the headline
assert names("frontend developer") == ["Meera"], names("frontend developer")
assert names("backend")[0] == "Rahul", names("backend")
front = hire.search_payload(cands, "frontend developer")["results"][0]["evidence"][0]
assert front["kind"] == "role" and len(front["covered"]) == 6 and not front["missing"]
analysts = names("data analyst")
# (keyword mode: Ananya's "Matplotlib charts" counts as data visualisation only by meaning, so she joins with the models)
assert {"Arjun", "Vikram"} <= set(analysts) and "Meera" not in analysts and "Rahul" not in analysts, analysts

# --- several skills: people with all of them first
both = hire.search_payload(cands, "sql developer with python")["results"]
assert both[0]["matched_terms"] == ["sql developer", "python"] and both[0]["fit"] == 2.0
assert [r["name"].split()[0] for r in both if r["fit"] == 2.0] == ["Rahul", "Ananya"], "both skills outrank one"

# --- evidence carries the proof behind the match
top = hire.search_payload(cands, "sql joins")["results"][0]["evidence"][0]
assert top["skill"] == "SQL joins" and top["proof"] and top["proof"]["tests"], top

# --- no query lists everyone, strongest first
everyone = hire.search_payload(cands, "")["results"]
assert len(everyone) == len(cands) and everyone[0]["points"] >= everyone[-1]["points"]
print("ALL OK")
