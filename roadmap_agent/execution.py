"""Whether this server may run learners' code.

Grading, test generation, workspaces and the terminal run code - the learner's, or a model's reference
solution - on the machine GLAI runs on. That is fine on your own computer and unsafe on a shared server,
where anyone could make it run anything. GLAI_CODE_EXECUTION=local (the default for `python main.py web`)
allows it; the deployed backend sets it to off until a sandboxed runner exists.
"""

import os

# routes that run code; refused when execution is off
CODE_ROUTES = ("/api/tests", "/api/submit", "/api/arena/tests", "/api/arena/submit")
CODE_PREFIXES = ("/api/workspace/",)


def allowed() -> bool:
    return os.environ.get("GLAI_CODE_EXECUTION", "local").strip().lower() == "local"


def runs_code(path: str) -> bool:
    return path in CODE_ROUTES or path.startswith(CODE_PREFIXES)


REFUSAL = {
    "error": "Running code is switched off on the shared server (it would let anyone run code on it). "
             "Grading, tests, workspaces and the terminal work when you run GLAI on your own computer.",
    "code": "execution_disabled",
}
