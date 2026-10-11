"""Evaluation engine: run a task's tests against the learner's code and review it.

    tests      generated once per task by the LLM, together with a reference solution;
               every test is first run against that reference, and tests the reference fails are
               fed back for a fix, then dropped - so learners are only graded by tests known to be
               consistent with a working solution
    source     a local folder or a git repo (optional branch / subfolder); the deliverable files sit at
               the top level, or in a folder named after the task (e.g. 1.3/ or task-1.3/)
    run        submission copied to a temp folder, pytest run there with a time limit
               (no sandbox beyond that: meant for your own code)
    grade      pass/fail from the tests only; an LLM review adds a 0-100 quality score and feedback
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from .schemas import CodeFile, Evaluation, Review, StoredTests, Task, TaskTests

TEST_FILE = "test__glai_task.py"
RUN_TIMEOUT = 120  # seconds for the whole pytest run
MAX_FIX_ROUNDS = 2
MAX_CODE_CHARS = 6000  # per file, in the review prompt
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".ipynb_checkpoints"}
_FILE = re.compile(r"[\w-]+\.(?:py|ipynb|sql|html|css|js|sh)\b", re.I)
# `assert output == "..."` / `assert result.stdout.strip().lower() == "..."` - too strict for stdout checks
_STRICT_OUTPUT = re.compile(r"^\s*assert\s+[^\n]*\b(?:stdout|output|out)\b[^\n=]*==[^\n]*$", re.M)

# Fixtures every generated test file can use. Scripts run as real processes (top-level input()/print work).
CONFTEST = '''
import importlib.util, subprocess, sys
from pathlib import Path
import pytest

WORKDIR = Path(__file__).parent


@pytest.fixture
def workdir():
    """Folder holding the submission and the sample data files; scripts run with this as cwd."""
    return WORKDIR


@pytest.fixture
def run_script():
    """run_script("file.py", stdin="a\\nb\\n", args=["x"]) -> CompletedProcess with .stdout .stderr .returncode"""
    def run(name, stdin="", args=(), timeout=20):
        return subprocess.run([sys.executable, name, *map(str, args)], input=stdin, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout, cwd=WORKDIR)
    return run


@pytest.fixture
def load_module():
    """load_module("file.py") -> imported module (only for files without top-level input())."""
    def load(name):
        spec = importlib.util.spec_from_file_location(Path(name).stem, WORKDIR / name)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    return load


@pytest.fixture
def run_sql():
    """run_sql("query.sql", setup="CREATE TABLE ...; INSERT ...;") -> rows (list of tuples) of the LAST
    statement in the file that returns rows. Runs on a fresh in-memory SQLite database each call."""
    import sqlite3

    def run(name, setup=""):
        con = sqlite3.connect(":memory:")
        try:
            if setup:
                con.executescript(setup)
            rows = []
            for stmt in sqlite3_statements((WORKDIR / name).read_text(encoding="utf-8")):
                cur = con.execute(stmt)
                if cur.description is not None:
                    rows = cur.fetchall()
            con.commit()
            return rows
        finally:
            con.close()
    return run


def sqlite3_statements(script):
    """Split a SQL script into complete statements (handles ';' inside strings)."""
    import sqlite3
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
'''

HELPERS_DOC = """Fixtures available in the test file (do not redefine them, do not import conftest):
- run_script(name, stdin="", args=()) -> subprocess.CompletedProcess (.stdout, .stderr, .returncode);
  runs `python <name>` with the working folder as cwd. Use it for scripts with top-level code or input().
- load_module(name) -> the imported module. Use it ONLY to call functions of files without top-level input().
- workdir -> pathlib.Path of the working folder (submission files + your data_files live there).
- run_sql(name, setup="") -> list of row tuples from the LAST row-returning statement in the .sql file,
  run on a fresh in-memory SQLite database after `setup` (your CREATE TABLE / INSERT script with sample data).
  Use it for .sql deliverables. SQLite dialect: date('now', '-30 days'), no INTERVAL; compare values, not
  column names; sort rows yourself unless the task requires an ORDER BY. When the task requires an ORDER BY,
  insert the sample rows in a DIFFERENT order than required, so a query without the sort fails.
- For .html / .css / .js deliverables there is no browser: read the files with (workdir / name).read_text() and
  check what the task asks for structurally - parse HTML with html.parser.HTMLParser (elements, ids, classes,
  attributes, linked stylesheet/script), find CSS rules and properties with re, check that the JavaScript defines
  the required functions / event listeners. Never execute JavaScript.
- For .sh deliverables: run them with subprocess.run(["bash", name], cwd=workdir, ...) only if the task needs the
  output; otherwise check the commands the script contains.
"""


class EvaluationError(Exception):
    pass


# ---------------------------------------------------------------- source


def required_files(task: Task) -> list[str]:
    """Code files the deliverable names, e.g. ['control_flow_demo.py']."""
    names = _FILE.findall(f"{task.deliverable} {task.title}")
    return list(dict.fromkeys(n for n in names if not n.lower().startswith("test")))


def fetch(source: str, branch: str | None, subdir: str | None, dest: Path) -> str:
    """Copy a local folder or clone a git repo into dest. Returns a description of the source."""
    if re.search(r"^(https?://|git@)|\.git/?$", source):
        cmd = ["git", "clone", "--depth", "1", *(["--branch", branch] if branch else []), source, str(dest)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            raise EvaluationError(f"git clone failed: {r.stderr.strip()[-400:]}")
        label = source + (f"@{branch}" if branch else "")
    else:
        src = Path(source).expanduser()
        if not src.is_dir():
            raise EvaluationError(f"Folder not found: {src}")
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns(*SKIP_DIRS))
        label = str(src.resolve())
    if subdir:
        label += f" ({subdir})"
    return label


def locate(root: Path, task: Task, subdir: str | None) -> Path:
    """The folder holding the deliverable: --subdir, else a folder named after the task, else the root."""
    if subdir:
        folder = root / subdir
        if not folder.is_dir():
            raise EvaluationError(f"Subfolder not found in the submission: {subdir}")
        return folder
    for name in (task.id, f"task-{task.id}", f"task_{task.id}", task.id.replace(".", "_")):
        if (root / name).is_dir():
            return root / name
    return root


# ---------------------------------------------------------------- pytest


def _run_pytest(files: dict[str, str], code_dir: Path | None, deselect: list[str]) -> tuple[int, int, list[str], str]:
    """Run TEST_FILE in a fresh folder with the code + files. Returns (passed, total, failures, raw output)."""
    with tempfile.TemporaryDirectory(prefix="glai_eval_", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp) / "work"
        if code_dir is not None:
            shutil.copytree(code_dir, work, ignore=shutil.ignore_patterns(*SKIP_DIRS))
        else:
            work.mkdir()
        for name, content in files.items():  # our files win over same-named files in the submission
            (work / name).parent.mkdir(parents=True, exist_ok=True)
            (work / name).write_text(content, encoding="utf-8")
        report = Path(tmp) / "report.xml"
        cmd = [sys.executable, "-m", "pytest", TEST_FILE, "-q", "-p", "no:cacheprovider", f"--junitxml={report}",
               "-o", "junit_family=xunit1", *[a for d in deselect for a in ("--deselect", f"{TEST_FILE}::{d}")]]
        env = {**os.environ, "MPLBACKEND": "Agg", "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        proc = subprocess.Popen(cmd, cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", env=env)
        try:
            out = (proc.communicate(timeout=RUN_TIMEOUT)[0] or "")[-4000:]
        except subprocess.TimeoutExpired:
            _kill_tree(proc)  # the learner's script is a grandchild: killing pytest alone would leave it running
            return 0, 1, [f"tests timed out after {RUN_TIMEOUT}s (infinite loop or waiting for input?)"], ""
        if not report.exists():
            return 0, 1, [f"pytest could not run: {out[-600:]}"], out
        return (*_parse_junit(report), out)


def _kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.kill()
    try:
        proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def _parse_junit(report: Path) -> tuple[int, int, list[str]]:
    root = ET.parse(report).getroot()
    cases = root.iter("testcase")
    passed, total, failures = 0, 0, []
    for case in cases:
        if case.find("skipped") is not None:
            continue
        total += 1
        bad = case.find("failure") if case.find("failure") is not None else case.find("error")
        if bad is None:
            passed += 1
        else:
            msg = (bad.get("message") or bad.text or "").strip().splitlines()
            failures.append(f"{case.get('name')}: {(msg[0] if msg else 'failed')[:300]}")
    if total == 0:  # collection error: report it as one failure
        failures.append("the test file could not be collected (import or syntax error)")
        total = 1
    return passed, total, failures


def _files(tests: TaskTests, code: list[CodeFile] | None = None) -> dict[str, str]:
    files = {"conftest.py": CONFTEST, TEST_FILE: tests.test_code}
    files.update({f.name: f.content for f in tests.data_files})
    files.update({f.name: f.content for f in code or []})
    return files


def _failed_names(failures: list[str]) -> list[str]:
    return [f.split(":", 1)[0] for f in failures if not f.startswith(("the test file", "tests timed out", "pytest"))]


# ---------------------------------------------------------------- test generation


def ensure_tests(task: Task, context: str, llm) -> StoredTests:
    """Tests for this task, generated and validated against a reference solution on first use."""
    if task.tests is not None:
        return task.tests
    from .graph import ask  # LLM helpers live with the graph

    names = required_files(task)
    feedback = ""
    best: tuple[TaskTests, list[str], int] | None = None  # draft whose reference passed the most tests
    weak: TaskTests | None = None  # valid tests that didn't catch the wrong solution - better than nothing
    for _ in range(MAX_FIX_ROUNDS + 1):
        drafted = ask(
            llm,
            TaskTests,
            "You write automated tests that grade a learner's solution to ONE hands-on task.\n\n"
            f"{context}\n"
            f"Task: {task.title}\nWhat to do: {task.description}\nDeliverable: {task.deliverable}\n"
            f"File(s) the learner submits: {', '.join(names) or '(infer from the deliverable)'}\n\n"
            f"{HELPERS_DOC}\n"
            "Rules:\n"
            "- 3-8 pytest test functions checking the BEHAVIOUR the task and deliverable ask for.\n"
            "- Be tolerant about formatting: check that expected values/phrases are CONTAINED in the output "
            "(case-insensitive `in`), never compare the whole stdout with ==. Prompt text printed by input(\"...\") "
            "and extra prints also land in stdout. Never require exact wording the task didn't specify.\n"
            "- Deterministic, no network, no extra packages beyond what the task uses (numpy/pandas/matplotlib ok).\n"
            "- If the task reads input files, put small sample files in data_files using the exact names it reads.\n"
            "- For plots, check the image file is created and non-empty, not its pixels (plots run headless).\n"
            "- Test ONLY what the task description or deliverable explicitly asks for. Do not add requirements it "
            "doesn't state (stripping input, capitalisation, extra edge cases, ...).\n"
            "- reference_files: a correct, complete solution using the same file names. It MUST pass every test.\n"
            "- wrong_files: a plausible INCORRECT solution (same file names) with one typical mistake the task "
            "warns against. At least one test MUST fail on it - that's how we know the tests are strict enough.\n"
            "- checks: one entry per test function: its name, a plain sentence telling the learner what is verified, "
            "and quote = the exact words copied from the task description or deliverable that require it."
            f"{feedback}",
        )
        if drafted is None:
            feedback = "\n\nYour previous answer was not valid JSON. Answer with valid JSON only."
            continue
        strict = _STRICT_OUTPUT.findall(drafted.test_code)
        if strict:  # a correct solution that prints an input() prompt would fail these
            feedback = (
                "\n\nYour previous tests compared whole program output with ==, e.g. "
                f"`{strict[0].strip()[:120]}`. A correct solution that prints input() prompts would fail. "
                "Check that the expected text is contained instead: `assert 'expected' in out.lower()`."
            )
            continue
        untraced = _untraced(drafted, task)
        if untraced and set(untraced) >= set(_test_names(drafted.test_code)):
            feedback = (
                "\n\nNone of your tests could be traced to the task: every check needs a quote copied word for word "
                "from the task description or deliverable, and must test only what that text asks for."
            )
            continue
        passed, total, failures, out = _run_pytest(_files(drafted, drafted.reference_files), None, [])
        if passed == total:
            # valid tests - but are they strict enough? the plausible wrong solution must fail at least one
            if not drafted.wrong_files:
                weak = weak or drafted
                feedback = "\n\nAlso give wrong_files: a plausible incorrect solution your tests must catch."
                continue
            w_passed, w_total, _, _ = _run_pytest(_files(drafted, drafted.wrong_files), None, untraced)
            if w_passed < w_total:
                return _store(task, drafted, untraced, names)  # untraced tests: invented requirements, not graded
            weak = drafted
            feedback = (
                "\n\nYour tests are too weak: your own INCORRECT solution (wrong_files) passed every test. Add or "
                "tighten a test so that mistake fails - e.g. for ordering, insert sample rows in a different order "
                "than required so a solution without sorting fails."
            )
            continue
        if best is None or passed > best[2]:
            best = (drafted, failures + [f"{n}: untraced" for n in untraced], passed)
        feedback = (
            "\n\nYour previous tests FAILED against your own reference solution. Fix the tests or the reference:\n- "
            + "\n- ".join(failures[:8])
            + f"\n\npytest output (end):\n{out[-1500:]}"
        )
    if weak is not None:  # every round's tests were valid but not strict enough: use them anyway
        return _store(task, weak, _untraced(weak, task), names)
    if best is None or best[2] == 0:
        raise EvaluationError("Couldn't generate working tests for this task; try again later.")
    drafted, failures, _ = best  # keep only the tests the reference passes
    return _store(task, drafted, _failed_names(failures), names)


def _store(task: Task, t: TaskTests, deselected: list[str], names: list[str]) -> StoredTests:
    task.tests = StoredTests(**t.model_dump(), deselected=sorted(set(deselected)), required_files=names)
    return task.tests


def _test_names(code: str) -> list[str]:
    return re.findall(r"^def (test_\w+)", code, re.M)


def _norm(text: str) -> str:
    text = text.lower().replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip(" .,;:'\"`")


def _untraced(t: TaskTests, task: Task) -> list[str]:
    """Tests whose check doesn't quote the task text word for word: likely invented requirements."""
    source = _norm(f"{task.title} {task.description} {task.deliverable}")
    traced = {c.test for c in t.checks if len(_norm(c.quote)) >= 6 and _norm(c.quote) in source}
    return [n for n in _test_names(t.test_code) if n not in traced]


# ---------------------------------------------------------------- review


def review(task: Task, code_dir: Path, passed: int, total: int, failures: list[str], context: str, llm) -> Review | None:
    from .graph import ask

    files = []
    for p in sorted(code_dir.rglob("*")):
        if p.is_file() and p.suffix in {".py", ".ipynb", ".sql", ".md"} and not set(p.parts) & SKIP_DIRS:
            files.append(f"--- {p.relative_to(code_dir)} ---\n{p.read_text(encoding='utf-8', errors='replace')[:MAX_CODE_CHARS]}")
    if not files:
        return None
    return ask(
        llm,
        Review,
        "You are a supportive but honest programming mentor reviewing a learner's submission for ONE task.\n\n"
        f"{context}\n"
        f"Task: {task.title}\nWhat to do: {task.description}\nDeliverable: {task.deliverable}\n"
        f"Automated tests: {passed}/{total} passed."
        + (f" Failures:\n- " + "\n- ".join(failures[:8]) if failures else "")
        + "\n\nSubmitted code:\n" + "\n\n".join(files[:6]) + "\n\n"
        "Judge against the TASK TEXT. Automated tests can be imperfect: if a failure demands something the task "
        "doesn't ask for, say so and don't tell the learner to change correct code.\n"
        "Score 0-100 for how well this solves the task (correctness first, then clarity and use of the concepts the "
        "task teaches). List concrete strengths, issues (point at lines/functions), and next_steps, most important first.",
    )


# ---------------------------------------------------------------- entry point


def evaluate(task: Task, source: str, context: str, llm, branch: str | None = None, subdir: str | None = None) -> Evaluation:
    """Fetch the submission, make sure tests exist, run them, review the code. Does not change the plan."""
    tests = ensure_tests(task, context, llm)
    with tempfile.TemporaryDirectory(prefix="glai_src_", ignore_cleanup_errors=True) as tmp:
        root = Path(tmp) / "src"
        label = fetch(source, branch, subdir, root)
        code_dir = locate(root, task, subdir)
        missing = [n for n in tests.required_files if not (code_dir / n).exists()]
        if missing:
            return Evaluation(
                at=datetime.now().isoformat(timespec="seconds"), source=label, passed=False, tests_passed=0,
                tests_total=1, failures=[f"missing file(s): {', '.join(missing)} (expected in {code_dir.relative_to(root.parent)})"],
            )
        passed, total, failures, _ = _run_pytest(_files(tests), code_dir, tests.deselected)
        rev = review(task, code_dir, passed, total, failures, context, llm)
    return Evaluation(
        at=datetime.now().isoformat(timespec="seconds"), source=label, passed=passed == total and total > 0,
        tests_passed=passed, tests_total=total, failures=failures, review=rev,
    )
