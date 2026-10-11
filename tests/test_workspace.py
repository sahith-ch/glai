"""Interactive quest environments: the right workspace per task, running code in it, and a safe server."""

import importlib.util
import tempfile
from pathlib import Path

from roadmap_agent import progress, server, workspace
from roadmap_agent.schemas import CodeFile, Milestone, Plan, Roadmap, StoredTests, Task


def task(tid, title, deliverable, description="Do it."):
    return Task(id=tid, title=title, description=description, estimated_hours=1, deliverable=deliverable)


tasks = [
    task("1.1", "Write greet.py that greets the user", "greet.py: prints Hello <name>"),
    task("1.2", "Top customers query", "top.sql: the 3 customers with the highest total"),
    task("1.3", "Landing page", "index.html with a header and a signup form"),
    task("1.4", "Plot monthly sales", "sales_plot.py: saves a matplotlib bar chart of sales.csv"),
    task("1.5", "Find large log files", "find_logs.sh: uses find and grep in the shell"),
]
tasks[1].tests = StoredTests(checks=[], test_code="", reference_files=[],
                             data_files=[CodeFile(name="orders.csv", content="customer,total\nana,30\nbo,50\ncy,10\n")])
root = Path(tempfile.mkdtemp())
plan = Plan(profile={"skill": "Data", "weekly_hours": 5, "days_per_week": 5, "start_date": "2026-10-12"},
            roadmap=Roadmap(skill="Data", summary="", total_weeks=1,
                            milestones=[Milestone(title="Basics", objective="o", week_start=1, week_end=1,
                                                  tasks=tasks, success_criteria=[])]))
progress.save(plan, root / "roadmap_data.json")

# --- the environment follows the deliverable
kinds = {t.id: workspace.kind_for(t) for t in tasks}
print("kinds:", kinds)
assert kinds == {"1.1": "python", "1.2": "sql", "1.3": "web", "1.4": "notebook", "1.5": "terminal"}

# --- opening seeds the deliverable stubs and the task's sample data, never a solution
py = workspace.open_workspace(root, "roadmap_data", "1.1")
assert py["kind"] == "python" and py["entry"] == "greet.py"
assert [f["name"] for f in py["files"]] == ["greet.py"] and "Hello" not in py["files"][0]["content"].split("Deliverable")[0]
web = workspace.open_workspace(root, "roadmap_data", "1.3")
assert [f["name"] for f in web["files"] if f["role"] == "deliverable"] == ["index.html", "script.js", "style.css"]
sql = workspace.open_workspace(root, "roadmap_data", "1.2")
assert {(f["name"], f["role"]) for f in sql["files"]} == {("top.sql", "deliverable"), ("orders.csv", "data")}

# --- saving keeps the learner's work; anything outside the folder is refused
workspace.save_files(root, [{"name": "greet.py", "content": "name = input()\nprint(f'Hello {name}')\n"}],
                     roadmap="roadmap_data", task_id="1.1")
for bad in ("../escape.py", "/abs.py", "a/../../b.py", "C:/x.py"):
    try:
        workspace.save_files(root, [{"name": bad, "content": "x"}], roadmap="roadmap_data", task_id="1.1")
        raise AssertionError(f"{bad} was accepted")
    except workspace.WorkspaceError:
        pass

# --- running: a script with stdin
out = workspace.run(root, {"mode": "script", "file": "greet.py", "stdin": "Ana\n"}, "roadmap_data", "1.1")
assert out["stdout"].strip() == "Hello Ana" and out["exit_code"] == 0, out

# --- SQL on the sample data; the learner's own .sql file is the query, not setup
workspace.save_files(root, [{"name": "top.sql", "content": "SELECT customer FROM orders ORDER BY CAST(total AS INT) DESC LIMIT 2;"}],
                     roadmap="roadmap_data", task_id="1.2")
res = workspace.run(root, {"mode": "sql", "query": "SELECT customer FROM orders ORDER BY CAST(total AS INT) DESC LIMIT 2;"},
                    "roadmap_data", "1.2")
assert res["error"] is None and res["results"][0]["rows"] == [["bo"], ["ana"]], res
assert res["schema"][0]["name"] == "orders" and res["schema"][0]["rows"] == 3
bad = workspace.run(root, {"mode": "sql", "query": "SELECT nope FROM orders;"}, "roadmap_data", "1.2")
assert bad["error"] and "nope" in bad["error"]

# --- notebook cells share state, report values and stop at the first error
workspace.open_workspace(root, "roadmap_data", "1.4")
cells = workspace.run(root, {"mode": "cells", "cells": ["x = 20", "print(x + 1)", "x * 2", "1 / 0", "print('never')"]},
                      "roadmap_data", "1.4")
got = cells["cells"]
assert [c["stdout"].strip() for c in got[:2]] == ["", "21"] and got[2]["value"] == "40", got
assert "ZeroDivisionError" in got[3]["error"] and len(got) == 4
if importlib.util.find_spec("matplotlib"):
    fig = workspace.run(root, {"mode": "cells", "cells": ["import matplotlib.pyplot as plt\nplt.plot([1, 2])\nplt.show()"]},
                        "roadmap_data", "1.4")
    assert fig["cells"][0]["figures"], "plots come back as images"

# --- a terminal in the task folder
workspace.open_workspace(root, "roadmap_data", "1.5")
sh = workspace.run(root, {"mode": "shell", "command": "echo hello && python -c \"print(6*7)\""}, "roadmap_data", "1.5")
assert "hello" in sh["stdout"] and "42" in sh["stdout"], sh

# --- only the app's own pages may call the server
assert server._origin_ok({"Origin": "http://localhost:1420"})
assert not server._origin_ok({"Origin": "https://evil.example"})
assert not server._origin_ok({"Sec-Fetch-Site": "cross-site"})
assert server._origin_ok({})  # curl / the CLI
code, body = server.learner_api(root, "POST", "/api/workspace/open", {}, {"roadmap": "roadmap_data", "task_id": "9.9"})
assert code == 400 and "No task" in body["error"]

print("ALL OK")
