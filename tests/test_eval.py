import tempfile
from pathlib import Path

from roadmap_agent import evaluate as ev
from roadmap_agent.schemas import Check, CodeFile, StoredTests, Task

TEST_CODE = '''
def test_classifies_negative(run_script):
    out = run_script("grade.py", stdin="-5\\n").stdout.lower()
    assert "negative" in out

def test_classifies_zero(run_script):
    assert "zero" in run_script("grade.py", stdin="0\\n").stdout.lower()

def test_reads_data_file(run_script, workdir):
    assert (workdir / "nums.csv").exists()
    assert "total=6" in run_script("grade.py", args=["sum"]).stdout.replace(" ", "")
'''
GOOD = '''import sys
if len(sys.argv) > 1 and sys.argv[1] == "sum":
    print("total =", sum(int(x) for x in open("nums.csv").read().split(",")))
else:
    n = int(input())
    print("negative" if n < 0 else "zero" if n == 0 else "positive")
'''
BUGGY = GOOD.replace('"zero" if n == 0 else ', "")  # never says zero

task = Task(id="1.3", title="Write grade.py that classifies numbers", description="d", estimated_hours=2,
            deliverable="grade.py: -5 -> negative, 0 -> zero")
assert ev.required_files(task) == ["grade.py"], ev.required_files(task)
task.tests = StoredTests(checks=[Check(test=n, verifies="v", quote="classifies numbers") for n in ("test_classifies_negative", "test_classifies_zero", "test_reads_data_file")], test_code=TEST_CODE, reference_files=[CodeFile(name="grade.py", content=GOOD)],
                         data_files=[CodeFile(name="nums.csv", content="1,2,3")], required_files=["grade.py"])
ev.review = lambda *a, **k: None  # no LLM in this test

# the reference passes its own tests
p, t, f, _ = ev._run_pytest(ev._files(task.tests, task.tests.reference_files), None, [])
assert (p, t) == (3, 3), (p, t, f)


def submit(files: dict, layout: str = "flat", **kw):
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        folder = root / "1.3" if layout == "task-folder" else root
        folder.mkdir(exist_ok=True)
        for n, c in files.items():
            (folder / n).write_text(c, encoding="utf-8")
        (root / ".git").mkdir()  # ignored
        return ev.evaluate(task, str(root), "ctx", llm=None, **kw)


e = submit({"grade.py": GOOD})
print("good:", e.passed, e.tests_passed, e.tests_total, e.failures)
assert e.passed and e.tests_passed == 3

e = submit({"grade.py": BUGGY})
print("buggy:", e.passed, e.tests_passed, e.tests_total, e.failures)
assert not e.passed and e.tests_passed == 2 and e.failures[0].startswith("test_classifies_zero")

e = submit({"other.py": GOOD})
print("missing:", e.failures)
assert not e.passed and "missing file" in e.failures[0]

e = submit({"grade.py": GOOD}, layout="task-folder")
print("task folder:", e.passed)
assert e.passed

task.tests.deselected = ["test_classifies_zero"]  # a test the reference failed is not used for grading
e = submit({"grade.py": BUGGY})
print("deselected:", e.passed, e.tests_passed, e.tests_total)
assert e.passed and e.tests_total == 2
task.tests.deselected = []

ev.RUN_TIMEOUT = 15
e = submit({"grade.py": "while True: pass\n"})
print("hang:", e.passed, e.failures[:1])
assert not e.passed
print("ALL OK")
