"""Saved state for apps that use GLAI as their local backend (Leveller), kept next to the roadmaps.

Leveller's engines keep their records in memory; this is where they survive a reload: its activity and
evidence log, learner model, progression events and goals, as one JSON document per app under
leveller/<name>.json. GLAI never reads the contents - it stores what the app sends and gives it back.
"""

import json
import os
import re
import tempfile
from pathlib import Path

STATE_DIR = "leveller"
MAX_BYTES = 50_000_000
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class StateError(Exception):
    pass


def _file(root: Path, name: str) -> Path:
    if not _NAME.match(name):
        raise StateError(f"Not a valid state name: {name!r}")
    return root / STATE_DIR / f"{name}.json"


def load(root: Path, name: str) -> dict:
    path = _file(root, name)
    if not path.exists():
        return {"data": None}
    return {"data": json.loads(path.read_text(encoding="utf-8")), "saved": path.stat().st_mtime}


def save(root: Path, name: str, data) -> dict:
    """Write atomically: a crash mid-write leaves the previous state, never half of one."""
    path = _file(root, name)
    text = json.dumps(data, ensure_ascii=False)
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise StateError(f"State is larger than {MAX_BYTES // 1_000_000} MB.")
    if not isinstance(path, Path):  # database-backed storage (supa.Folder): a row write is already atomic
        path.write_text(text, encoding="utf-8")
        return {"saved": True, "bytes": len(text)}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return {"saved": True, "bytes": len(text)}
