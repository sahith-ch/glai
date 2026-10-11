"""Deploy the GLAI backend to a Hugging Face Space (Docker, free CPU tier with 16 GB RAM).

    hf auth login                                  # once: a token with "write" access
    python deploy/hf_space.py                      # creates/updates <you>/glai-backend and waits until it runs
    python deploy/hf_space.py --origins https://glai-hire.vercel.app   # after the hiring site has a URL

Uploads only the backend's code (Dockerfile, requirements.txt, main.py, roadmap_agent/) - never .env, roadmaps,
profiles or other local data. Keys from your local .env become private Space *secrets*; public settings
(Supabase URL + publishable key, the model name) become Space variables. The Space itself is public, so its
API is reachable by the apps - which also means the backend's source is visible on Hugging Face.
"""

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values
from huggingface_hub import HfApi

ROOT = Path(__file__).resolve().parent.parent
SUPABASE_URL = "https://lzoyhpbxwuowpbrmnpdm.supabase.co"
SUPABASE_KEY = "sb_publishable_cIfxHzNY1wAH3p8q0NIRWQ_DBJKb70g"  # publishable: safe to be public
SECRET_KEYS = ["GROQ_API_KEY", "TAVILY_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
               "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]
VARIABLE_KEYS = ["LLM_PROVIDER", "LLM_MODEL", "CLAUDE_EFFORT", "WRITE_AHEAD_MILESTONES"]

README = """---
title: GLAI backend
emoji: 🧭
colorFrom: green
colorTo: gray
sdk: docker
app_port: 8080
pinned: false
---

The GLAI backend: learner API at `/api` (Supabase sign-in), hiring API at `/hire/api`. Health: `/api/health`.
"""


def bundle() -> Path:
    """Only what the image needs, in a fresh folder."""
    out = Path(tempfile.mkdtemp(prefix="glai_space_"))
    for name in ("Dockerfile", "requirements.txt", "main.py", ".dockerignore"):
        shutil.copy2(ROOT / name, out / name)
    shutil.copytree(ROOT / "roadmap_agent", out / "roadmap_agent", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (out / "README.md").write_text(README, encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", default="glai-backend")
    ap.add_argument("--origins", default="", help="comma-separated web apps allowed to call the API")
    args = ap.parse_args()

    api = HfApi()
    user = api.whoami()["name"]
    repo = f"{user}/{args.space}"
    url = f"https://{user.lower()}-{args.space.lower().replace('_', '-')}.hf.space"
    env = {k: v for k, v in dotenv_values(ROOT / ".env").items() if v}

    print(f"Space {repo} -> {url}")
    api.create_repo(repo, repo_type="space", space_sdk="docker", exist_ok=True)
    variables = {"SUPABASE_URL": SUPABASE_URL, "SUPABASE_PUBLISHABLE_KEY": SUPABASE_KEY, "GLAI_CODE_EXECUTION": "off",
                 "GLAI_PUBLIC_URL": url, **{k: env[k] for k in VARIABLE_KEYS if k in env}}
    if args.origins:
        variables["GLAI_ALLOWED_ORIGINS"] = args.origins
    for k, v in variables.items():
        api.add_space_variable(repo, k, v)
    secrets = [k for k in SECRET_KEYS if k in env]
    for k in secrets:
        api.add_space_secret(repo, k, env[k])
    print(f"settings: {len(variables)} variables, secrets: {', '.join(secrets) or 'none'}")

    folder = bundle()
    api.upload_folder(repo_id=repo, repo_type="space", folder_path=str(folder),
                      commit_message="Deploy GLAI backend", delete_patterns=["*"])
    shutil.rmtree(folder, ignore_errors=True)
    print("uploaded; Hugging Face is building the image (several minutes the first time)")

    stage = None
    for _ in range(120):  # up to ~30 minutes
        stage = api.get_space_runtime(repo).stage
        if stage in ("RUNNING", "BUILD_ERROR", "RUNTIME_ERROR", "CONFIG_ERROR"):
            break
        time.sleep(15)
    print("stage:", stage)
    if stage != "RUNNING":
        print(f"See the build logs: https://huggingface.co/spaces/{repo}?logs=build")
        sys.exit(1)
    for _ in range(20):
        try:
            print("health:", httpx.get(f"{url}/api/health", timeout=20).json())
            break
        except Exception:
            time.sleep(10)
    print(f"\nBackend: {url}\nHiring API: {url}/hire/api\nDocs: {url}/api/docs")


if __name__ == "__main__":
    main()
