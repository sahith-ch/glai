"""Leveller's saved state round-trips through GLAI, atomically, under safe names only."""

import tempfile
from pathlib import Path

from roadmap_agent import server

root = Path(tempfile.mkdtemp())
code, body = server.learner_api(root, "GET", "/api/state/leveller", {}, {})
assert code == 200 and body == {"data": None}, body

state = {"version": 1, "progression": {"events": [{"seq": 1, "type": "xp_awarded"}]}, "name": "Ana ✓"}
code, body = server.learner_api(root, "POST", "/api/state/leveller", {}, {"data": state})
assert code == 200 and body["saved"], body
code, body = server.learner_api(root, "GET", "/api/state/leveller", {}, {})
assert body["data"] == state
assert not list((root / "leveller").glob("*.tmp")), "no temp files left behind"

for bad in ("../roadmap_x", "a/b", "", "UPPER", "x" * 60):
    code, body = server.learner_api(root, "POST", f"/api/state/{bad}", {}, {"data": {}})
    assert code in (400, 404), (bad, code, body)
assert not (root / "roadmap_x.json").exists()
code, body = server.learner_api(root, "POST", "/api/state/leveller", {}, {})
assert code == 400

print("ALL OK")
