"""Run every offline test script: python tests/run_all.py  (no API keys or network needed)."""

import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
failed = []
for test in sorted(Path(__file__).parent.glob("test_*.py")):
    r = subprocess.run([sys.executable, str(test)], cwd=root, capture_output=True, text=True,
                       env={**__import__("os").environ, "PYTHONPATH": str(root)})
    ok = r.returncode == 0 and "ALL OK" in r.stdout
    print(f"{'ok  ' if ok else 'FAIL'} {test.name}")
    if not ok:
        failed.append(test.name)
        print((r.stdout + r.stderr)[-1500:])
print(f"\n{len(failed)} failed" if failed else "\nall passed")
sys.exit(1 if failed else 0)
