"""Interactive quest environments: a workspace per task, shaped like the work the task asks for.

Learners solve a quest inside the app instead of reading instructions and working elsewhere. Each task gets
a folder (workspaces/<roadmap>/<task>/, or workspaces/arena/<challenge>/) seeded with its deliverable files
and the sample data its tests use, and an environment chosen from what the deliverable is:

    sql       .sql deliverable                -> query editor + results table on SQLite, schema browser
    web       .html / .css / .js deliverable  -> editor + live preview (rendered in the browser, sandboxed)
    notebook  plots, data analysis, ML         -> cells (the .py deliverable in `# %%` cells) with output + figures
    terminal  shell, command line, git, CLI    -> a terminal in the task folder
    python    anything else                    -> editor + run with stdin / args, and a terminal

Code runs on this machine in the task folder with a time limit - the same trust model as grading
(evaluate.py: "no sandbox beyond that: meant for your own code"). The server only accepts these requests
from the app's own origins (server._origin_ok), so a web page cannot use it to run commands.

Submitting grades the workspace folder with the task's tests, like any other submission.
"""

import base64
import csv
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import arena, dashboard, progress
from . import evaluate as ev
from .schemas import Task

ROOT_DIR = "workspaces"
KINDS = ("python", "sql", "web", "notebook", "terminal")
MAX_FILES = 40
MAX_FILE_BYTES = 200_000
MAX_OUTPUT = 20_000
SCRIPT_TIMEOUT = 20
CELLS_TIMEOUT = 90
SHELL_TIMEOUT = 30
MAX_ROWS = 500
_NAME = re.compile(r"^[\w.-]+(?:/[\w.-]+)*$")
_DELIVERABLE = re.compile(r"[\w-]+\.(?:py|ipynb|sql|html|css|js|sh)\b", re.I)
_TEXT = {".py", ".sql", ".html", ".htm", ".css", ".js", ".json", ".csv", ".txt", ".md", ".sh", ".tsv", ".xml", ".yaml", ".yml"}

_NOTEBOOK_WORDS = ("matplotlib", "seaborn", "plot", "chart", "histogram", "scikit", "sklearn", "regression",
                   "classifier", "train ", "model", "dataframe", "pandas", "numpy", "exploratory", "eda", "notebook")
_SHELL_WORDS = ("bash", "shell", "command line", "command-line", "terminal", "git ", "cli ", "grep", "chmod",
                "pipe ", "environment variable", "cron", ".sh")


class WorkspaceError(Exception):
    pass


# ---------------------------------------------------------------- which environment


def deliverables(task: Task) -> list[str]:
    """Every file the deliverable names, web and shell files included (evaluate.required_files is code only)."""
    names = _DELIVERABLE.findall(f"{task.deliverable} {task.title}")
    return list(dict.fromkeys(n for n in names if not n.lower().startswith("test")))


def kind_for(task: Task) -> str:
    """The environment a task needs, from what it asks the learner to hand in."""
    names = [n.lower() for n in deliverables(task)]
    text = f"{task.title} {task.description} {task.deliverable}".lower()
    if any(n.endswith(".sql") for n in names):
        return "sql"
    if any(n.endswith((".html", ".css", ".js")) for n in names):
        return "web"
    if any(n.endswith(".sh") for n in names) or any(w in text for w in _SHELL_WORDS):
        return "terminal"
    if any(n.endswith(".ipynb") for n in names) or any(w in text for w in _NOTEBOOK_WORDS):
        return "notebook"
    return "python"


_DEFAULT_FILES = {
    "python": ["solution.py"],
    "sql": ["query.sql"],
    "web": ["index.html", "style.css", "script.js"],
    "notebook": ["analysis.py"],
    "terminal": ["commands.sh"],
}


def _starter(name: str, task: Task) -> str:
    """An almost empty file that says what it is for. Never a partial solution."""
    title, deliverable = task.title.replace("\n", " "), task.deliverable.replace("\n", " ")
    ext = Path(name).suffix.lower()
    if ext == ".py":
        return f"# {title}\n# Deliverable: {deliverable}\n\n# %%\n\n"
    if ext == ".sql":
        return f"-- {title}\n-- Deliverable: {deliverable}\n\n"
    if ext in (".html", ".htm"):
        return ("<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n"
                f"  <title>{title}</title>\n  <link rel=\"stylesheet\" href=\"style.css\">\n</head>\n<body>\n\n"
                "  <script src=\"script.js\"></script>\n</body>\n</html>\n")
    if ext in (".css", ".js"):
        return f"/* {title} */\n\n"
    if ext == ".sh":
        return f"#!/usr/bin/env bash\n# {title}\n# Deliverable: {deliverable}\n\n"
    return ""


# ---------------------------------------------------------------- where


def _target(root: Path, roadmap: str | None, task_id: str | None, challenge: str | None) -> tuple[Task, Path, str]:
    """(task, workspace folder, context) for a roadmap quest or an arena challenge."""
    if challenge:
        c = arena.find(arena.load(root), challenge)
        return c.task, root / ROOT_DIR / "arena" / _safe(c.id), f"Skill: {c.skill}"
    file = next((p for p in dashboard.roadmap_files(root) if p.stem == roadmap), None)
    if not file or not task_id:
        raise WorkspaceError("Which roadmap and task?")
    plan = progress.load(file)
    try:
        _, t = progress.find(plan, task_id)
    except Exception as e:
        raise WorkspaceError(f"No task {task_id} in {roadmap}.") from e
    return t, root / ROOT_DIR / _safe(file.stem) / _safe(t.id), f"Skill: {plan.roadmap.skill}"


def _safe(part: str) -> str:
    cleaned = re.sub(r"[^\w.-]", "_", part).strip(".")
    if not cleaned:
        raise WorkspaceError("Bad workspace name.")
    return cleaned


def _path(folder: Path, name: str) -> Path:
    """A file inside the workspace, refusing anything that would land outside it."""
    name = name.replace("\\", "/").strip()
    if not _NAME.match(name) or any(part in ("", ".", "..") for part in name.split("/")):
        raise WorkspaceError(f"Not a valid file name: {name!r}")
    path = (folder / name).resolve()
    if folder.resolve() not in path.parents:
        raise WorkspaceError(f"Not a valid file name: {name!r}")
    return path


# ---------------------------------------------------------------- open / save


def open_workspace(root: Path, roadmap: str | None = None, task_id: str | None = None, challenge: str | None = None) -> dict:
    """Create the task's workspace if needed (deliverable stubs + sample data) and describe it."""
    task, folder, _ = _target(root, roadmap, task_id, challenge)
    kind = kind_for(task)
    folder.mkdir(parents=True, exist_ok=True)
    names = deliverables(task) or _DEFAULT_FILES[kind]
    if kind == "web":  # a page needs its stylesheet and script even when the deliverable names only the page
        names = list(dict.fromkeys(names + [n for n in _DEFAULT_FILES["web"] if not any(x.endswith(Path(n).suffix) for x in names)]))
    for name in names:
        path = _path(folder, name)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(_starter(name, task), encoding="utf-8")
    data = [f.name for f in (task.tests.data_files if task.tests else [])]
    for f in task.tests.data_files if task.tests else []:
        path = _path(folder, f.name)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f.content, encoding="utf-8")
    return describe(task, folder, kind, names, data)


def describe(task: Task, folder: Path, kind: str, deliverable_names: list[str], data: list[str]) -> dict:
    files = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and not set(p.relative_to(folder).parts) & ev.SKIP_DIRS):
        name = path.relative_to(folder).as_posix()
        text = path.suffix.lower() in _TEXT and path.stat().st_size <= MAX_FILE_BYTES
        files.append({
            "name": name,
            "role": "deliverable" if name in deliverable_names else "data" if name in data else "other",
            "content": path.read_text(encoding="utf-8", errors="replace") if text else None,
            "size": path.stat().st_size,
        })
    files.sort(key=lambda f: ({"deliverable": 0, "other": 1, "data": 2}[f["role"]], f["name"]))
    return {
        "kind": kind,
        "path": str(folder.resolve()),
        "task": {"id": task.id, "title": task.title, "description": task.description, "deliverable": task.deliverable,
                 "status": task.status},
        "entry": next((n for n in deliverable_names if n.endswith(".py")), None),
        "files": files,
        "tests_ready": task.tests is not None,
        "shell": shell_name(),
    }


def save_files(root: Path, files: list[dict], deleted: list[str] | None = None, roadmap: str | None = None,
               task_id: str | None = None, challenge: str | None = None) -> dict:
    """Write the learner's files. Data files may be edited too: grading writes its own copies anyway."""
    task, folder, _ = _target(root, roadmap, task_id, challenge)
    if not folder.exists():
        raise WorkspaceError("Open the workspace first.")
    if len(files) > MAX_FILES:
        raise WorkspaceError(f"At most {MAX_FILES} files.")
    for f in files:
        content = str(f.get("content", ""))
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise WorkspaceError(f"{f.get('name')} is larger than {MAX_FILE_BYTES // 1000} KB.")
        path = _path(folder, str(f.get("name", "")))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    for name in deleted or []:
        path = _path(folder, name)
        if path.is_file():
            path.unlink()
    return {"saved": len(files), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}


# ---------------------------------------------------------------- run


def run(root: Path, request: dict, roadmap: str | None = None, task_id: str | None = None,
        challenge: str | None = None) -> dict:
    """Run something in the workspace: a script, SQL, notebook cells or a shell command."""
    task, folder, _ = _target(root, roadmap, task_id, challenge)
    if not folder.exists():
        raise WorkspaceError("Open the workspace first.")
    mode = request.get("mode")
    if mode == "script":
        return run_script(folder, str(request.get("file", "")), str(request.get("stdin", "")), request.get("args") or [])
    if mode == "sql":
        return run_sql(folder, str(request.get("query", "")), exclude=deliverables(task) or _DEFAULT_FILES["sql"])
    if mode == "cells":
        return run_cells(folder, [str(c) for c in request.get("cells") or []])
    if mode == "shell":
        return run_shell(folder, str(request.get("command", "")))
    raise WorkspaceError(f"Unknown run mode {mode!r}.")


def _env() -> dict:
    return {**os.environ, "MPLBACKEND": "Agg", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}


def _clip(text: str) -> str:
    return text if len(text) <= MAX_OUTPUT else text[:MAX_OUTPUT] + f"\n... ({len(text) - MAX_OUTPUT} more characters)"


def _process(cmd, folder: Path, stdin: str, timeout: int, shell: bool = False) -> dict:
    started, wall = time.monotonic(), time.time()
    proc = subprocess.Popen(cmd, cwd=folder, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", env=_env(), shell=shell)
    try:
        out, err = proc.communicate(stdin, timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        ev._kill_tree(proc)
        out, err, timed_out = "", f"Stopped after {timeout}s (an infinite loop, or waiting for input?)", True
    return {"stdout": _clip(out or ""), "stderr": _clip(err or ""), "exit_code": proc.returncode, "timed_out": timed_out,
            "duration_ms": round((time.monotonic() - started) * 1000), "images": _new_images(folder, wall)}


def _new_images(folder: Path, since: float) -> list[dict]:
    """Image files the run wrote (plots saved to disk), so the learner sees them without leaving the app."""
    cutoff = since - 1
    out = []
    for path in sorted(folder.rglob("*.png")):
        if path.stat().st_mtime >= cutoff and path.stat().st_size < 3_000_000:
            out.append({"name": path.relative_to(folder).as_posix(),
                        "png": base64.b64encode(path.read_bytes()).decode("ascii")})
    return out[:6]


def run_script(folder: Path, file: str, stdin: str = "", args: list | None = None) -> dict:
    path = _path(folder, file)
    if not path.is_file() or path.suffix != ".py":
        raise WorkspaceError(f"No Python file {file!r} in the workspace.")
    return _process([sys.executable, path.relative_to(folder.resolve()).as_posix(), *map(str, args or [])],
                    folder, stdin, SCRIPT_TIMEOUT)


def _bash() -> str | None:
    """Git Bash on Windows (never WSL's bash.exe, which runs in another filesystem), bash elsewhere."""
    found = shutil.which("bash")
    if os.name != "nt":
        return found
    git = shutil.which("git")
    candidates = [Path(git).parent.parent / "bin" / "bash.exe"] if git else []
    if found and "system32" not in found.lower():
        candidates.append(Path(found))
    return next((str(c) for c in candidates if c.exists()), None)


def shell_name() -> str:
    return "bash" if _bash() else ("cmd" if os.name == "nt" else "sh")


def run_shell(folder: Path, command: str) -> dict:
    """One command in the task folder: bash where available (tasks are written for it), else the system shell."""
    command = command.strip()
    if not command:
        raise WorkspaceError("Type a command.")
    # `python` means the interpreter running GLAI, so learners don't depend on PATH
    python = Path(sys.executable).as_posix()
    command = re.sub(r"(?<![\w/.-])(python3?|py)(?=\s|$)", lambda _: f'"{python}"', command)
    bash = _bash()
    if bash:
        return _process([bash, "-c", command], folder, "", SHELL_TIMEOUT)
    return _process(command, folder, "", SHELL_TIMEOUT, shell=True)


def _tables_from_csv(con: sqlite3.Connection, folder: Path) -> None:
    for path in sorted(folder.glob("*.csv")):
        with path.open(encoding="utf-8", errors="replace", newline="") as fh:
            rows = list(csv.reader(fh))
        if not rows:
            continue
        cols = [re.sub(r"\W", "_", c.strip()) or f"col{i + 1}" for i, c in enumerate(rows[0])]
        table = re.sub(r"\W", "_", path.stem)
        con.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(f"{chr(34)}{c}{chr(34)}" for c in cols)})')
        con.executemany(f'INSERT INTO "{table}" VALUES ({", ".join("?" * len(cols))})',
                        [(r + [None] * len(cols))[: len(cols)] for r in rows[1:]])


def _schema(con: sqlite3.Connection) -> list[dict]:
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return [{"name": t, "columns": [{"name": c[1], "type": c[2]} for c in con.execute(f'PRAGMA table_info("{t}")')],
             "rows": con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]} for t in tables]


def run_sql(folder: Path, query: str, exclude: list[str] | None = None) -> dict:
    """Run a query on SQLite loaded with the workspace's sample data.

    Setup is the task's data: every .csv as a table, and every .sql file that only creates or fills tables
    (a schema / seed file). The learner's query runs after it, on a fresh in-memory database each time.
    """
    con = sqlite3.connect(":memory:")
    started = time.monotonic()
    try:
        _tables_from_csv(con, folder)
        for path in sorted(folder.glob("*.sql")):
            if path.name in (exclude or []):  # the learner's own query is what runs below, not setup
                continue
            script = path.read_text(encoding="utf-8", errors="replace")
            statements = ev_statements(script)
            if statements and all(re.match(r"^\s*(?:--[^\n]*\n\s*)*(CREATE|INSERT|PRAGMA|BEGIN|COMMIT|DROP)\b", s, re.I)
                                  for s in statements):
                con.executescript(script)
        results, error = [], None
        for stmt in ev_statements(query):
            try:
                cur = con.execute(stmt)
            except sqlite3.Error as e:
                error = f"{e} (in: {stmt[:160]})"
                break
            if cur.description is not None:
                rows = cur.fetchmany(MAX_ROWS + 1)
                results.append({"statement": stmt[:400], "columns": [d[0] for d in cur.description],
                                "rows": [list(r) for r in rows[:MAX_ROWS]], "truncated": len(rows) > MAX_ROWS})
            else:
                results.append({"statement": stmt[:400], "columns": [], "rows": [], "changed": cur.rowcount})
        return {"results": results, "error": error, "schema": _schema(con),
                "duration_ms": round((time.monotonic() - started) * 1000)}
    finally:
        con.close()


def ev_statements(script: str) -> list[str]:
    out, buf = [], ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


# The cells runner: one process, shared globals, stdout and figures captured per cell, stop at the first error.
_CELLS_RUNNER = r'''
import base64, contextlib, io, json, sys, traceback
cells = json.loads(sys.stdin.read())
results, scope = [], {"__name__": "__main__"}
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except Exception:
    plt = None
def figures():
    if plt is None:
        return []
    out = []
    for num in plt.get_fignums():
        buf = io.BytesIO()
        plt.figure(num).savefig(buf, format="png", bbox_inches="tight", dpi=90)
        out.append(base64.b64encode(buf.getvalue()).decode("ascii"))
    plt.close("all")
    return out
if plt is not None:
    plt.show = lambda *a, **k: None
for i, code in enumerate(cells):
    out = io.StringIO()
    entry = {"index": i, "stdout": "", "error": None, "figures": [], "value": None}
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            compiled = compile(code, f"<cell {i + 1}>", "exec")
            exec(compiled, scope)
            last = code.strip().splitlines()[-1] if code.strip() else ""
            if last and not last.startswith((" ", "\t", "#")) and not any(last.startswith(k) for k in ("import ", "from ", "def ", "class ", "for ", "while ", "if ", "with ", "return", "print(")) and "=" not in last.split("(")[0]:
                try:
                    value = eval(compile(last, f"<cell {i + 1}>", "eval"), scope)
                    if value is not None:
                        entry["value"] = repr(value)[:4000]
                except Exception:
                    pass
    except Exception:
        entry["error"] = traceback.format_exc(limit=3)[-3000:]
    entry["stdout"] = out.getvalue()[-8000:]
    entry["figures"] = figures()
    results.append(entry)
    if entry["error"]:
        break
print("\n@@GLAI_CELLS@@" + json.dumps(results))
'''


def run_cells(folder: Path, cells: list[str]) -> dict:
    """Run notebook cells in order in one fresh Python process, in the workspace folder."""
    if not cells:
        raise WorkspaceError("No cells to run.")
    started = time.monotonic()
    with tempfile.NamedTemporaryFile("w", suffix="_glai_cells.py", delete=False, encoding="utf-8") as fh:
        fh.write(_CELLS_RUNNER)
        runner = fh.name
    try:
        proc = subprocess.Popen([sys.executable, runner], cwd=folder, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=_env())
        try:
            out, err = proc.communicate(json.dumps(cells), timeout=CELLS_TIMEOUT)
        except subprocess.TimeoutExpired:
            ev._kill_tree(proc)
            return {"cells": [], "error": f"Stopped after {CELLS_TIMEOUT}s (an infinite loop?)", "duration_ms": CELLS_TIMEOUT * 1000}
    finally:
        os.unlink(runner)
    marker = (out or "").rfind("@@GLAI_CELLS@@")
    if marker < 0:
        return {"cells": [], "error": _clip((err or out or "The cells could not run.").strip()),
                "duration_ms": round((time.monotonic() - started) * 1000)}
    return {"cells": json.loads(out[marker + len("@@GLAI_CELLS@@"):]), "error": None,
            "duration_ms": round((time.monotonic() - started) * 1000)}
