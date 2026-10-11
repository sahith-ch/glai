"""Deploy the GLAI backend to Render (free web service, 512 MB) from a private Docker Hub image.

    python deploy/render.py                                   # build, push, create or update the service, wait
    python deploy/render.py --origins https://glai-hire.vercel.app   # after the hiring site has a URL

Credentials come from .env.deploy (DOCKERHUB_USER, DOCKERHUB_TOKEN, RENDER_API_KEY) and are never printed.
Model and search keys come from your local .env and become Render environment variables. The image holds only
the backend's code (see .dockerignore); the Docker Hub repository is made private, and Render pulls it with a
registry credential.

On the free plan the hiring search runs on keywords (GLAI_SEMANTIC_SEARCH=off): the semantic models need
~370 MB, more than 512 MB leaves. The service sleeps after 15 idle minutes; the first request then takes ~1 min.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
RENDER = "https://api.render.com/v1"
SERVICE = "glai-backend"
REGION = "singapore"  # closest Render region to India
SUPABASE_URL = "https://lzoyhpbxwuowpbrmnpdm.supabase.co"
SUPABASE_KEY = "sb_publishable_cIfxHzNY1wAH3p8q0NIRWQ_DBJKb70g"  # publishable: safe to be public
FROM_DOTENV = ["LLM_PROVIDER", "LLM_MODEL", "GROQ_API_KEY", "TAVILY_API_KEY", "ANTHROPIC_API_KEY",
               "OPENAI_API_KEY", "CLAUDE_EFFORT", "WRITE_AHEAD_MILESTONES", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]


def need(env: dict, key: str) -> str:
    if not env.get(key):
        sys.exit(f"Missing {key} in .env.deploy")
    return env[key]


def run(cmd: list[str], stdin: str | None = None) -> None:
    r = subprocess.run(cmd, input=stdin, text=True, capture_output=True)
    if r.returncode != 0:
        sys.exit(f"{' '.join(cmd[:3])} failed: {(r.stderr or r.stdout).strip()[-500:]}")


def make_private_repo(user: str, token: str, name: str) -> None:
    """Create the Docker Hub repository as private before the first push (a push would create it public)."""
    login = httpx.post("https://hub.docker.com/v2/users/login", json={"username": user, "password": token}, timeout=30)
    if login.status_code != 200:
        print("  (could not sign in to the Docker Hub API to pre-create a private repo; check its visibility after)")
        return
    jwt = login.json()["token"]
    r = httpx.post("https://hub.docker.com/v2/repositories/", headers={"Authorization": f"JWT {jwt}"}, timeout=30,
                   json={"namespace": user, "name": name, "is_private": True, "description": "GLAI backend"})
    if r.status_code in (200, 201):
        print(f"  created private repository {user}/{name}")
    elif r.status_code in (400, 409) and "exist" in r.text.lower():
        print(f"  repository {user}/{name} already exists")
    else:
        print(f"  (Docker Hub answered {r.status_code} creating the repo; check its visibility after)")


def render(method: str, path: str, key: str, **kw) -> httpx.Response:
    r = httpx.request(method, f"{RENDER}{path}", headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                      timeout=60, **kw)
    if r.status_code >= 400:
        sys.exit(f"Render {method} {path}: {r.status_code} {r.text[:400]}")
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--origins", default="", help="comma-separated web apps allowed to call the API")
    ap.add_argument("--skip-build", action="store_true")
    args = ap.parse_args()

    creds = dotenv_values(ROOT / ".env.deploy")
    user, token, key = need(creds, "DOCKERHUB_USER"), need(creds, "DOCKERHUB_TOKEN"), need(creds, "RENDER_API_KEY")
    local = {k: v for k, v in dotenv_values(ROOT / ".env").items() if v}
    image = f"docker.io/{user}/{SERVICE}:latest"

    # 1. image -> private Docker Hub repository
    print("Docker Hub")
    make_private_repo(user, token, SERVICE)
    run(["docker", "login", "-u", user, "--password-stdin"], stdin=token)
    if not args.skip_build:
        print("  building image")
        # keyword search on the free plan: the semantic models stay out of the image (~190 MB smaller push)
        run(["docker", "build", "--build-arg", "BAKE_SEARCH_MODELS=0", "-t", f"{SERVICE}:latest", str(ROOT)])
    run(["docker", "tag", f"{SERVICE}:latest", image])
    print(f"  pushing {image}")
    for attempt in range(1, 6):  # a big push over a flaky link: docker resumes, so retry
        r = subprocess.run(["docker", "push", image], text=True, capture_output=True)
        if r.returncode == 0:
            break
        print(f"  push attempt {attempt} failed ({(r.stderr or r.stdout).strip().splitlines()[-1][:120]}); retrying")
        time.sleep(10 * attempt)
    else:
        sys.exit("docker push kept failing - check the network connection and run again (uploaded layers are kept)")

    # 2. Render: workspace, registry credential, service
    print("Render")
    owner = render("GET", "/owners?limit=20", key).json()[0]["owner"]["id"]
    creds_list = render("GET", f"/registrycredentials?ownerId={owner}&limit=100", key).json()
    cred = next((c.get("id") or c.get("registryCredential", {}).get("id") for c in creds_list
                 if (c.get("name") or c.get("registryCredential", {}).get("name")) == f"{SERVICE}-dockerhub"), None)
    if not cred:
        cred = render("POST", "/registrycredentials", key, json={
            "name": f"{SERVICE}-dockerhub", "registry": "DOCKER", "username": user, "authToken": token,
            "ownerId": owner}).json()["id"]
    env = {"SUPABASE_URL": SUPABASE_URL, "SUPABASE_PUBLISHABLE_KEY": SUPABASE_KEY, "GLAI_CODE_EXECUTION": "off",
           "GLAI_SEMANTIC_SEARCH": "off", **{k: local[k] for k in FROM_DOTENV if k in local}}
    if args.origins:
        env["GLAI_ALLOWED_ORIGINS"] = args.origins

    found = [s["service"] for s in render("GET", f"/services?name={SERVICE}&ownerId={owner}&limit=20", key).json()]
    if found:
        sid = found[0]["id"]
        url = found[0]["serviceDetails"]["url"]
        print(f"  updating service {sid}")
        render("PATCH", f"/services/{sid}", key, json={"image": {"imagePath": image, "ownerId": owner,
                                                                  "registryCredentialId": cred}})
    else:
        print("  creating service (free plan, Singapore)")
        svc = render("POST", "/services", key, json={
            "type": "web_service", "name": SERVICE, "ownerId": owner, "autoDeployTrigger": "off",
            "image": {"imagePath": image, "ownerId": owner, "registryCredentialId": cred},
            "envVars": [{"key": k, "value": v} for k, v in env.items()],
            "serviceDetails": {"runtime": "image", "plan": "free", "region": REGION, "healthCheckPath": "/api/health"},
        }).json()
        svc = svc.get("service", svc)
        sid, url = svc["id"], svc["serviceDetails"]["url"]
    env["GLAI_PUBLIC_URL"] = url  # Google Calendar sign-in returns here
    render("PUT", f"/services/{sid}/env-vars", key, json=[{"key": k, "value": v} for k, v in env.items()])
    render("POST", f"/services/{sid}/deploys", key, json={})
    print(f"  deploying {url}")

    # 3. wait until it's live
    status = None
    for _ in range(80):  # ~20 minutes
        deploys = render("GET", f"/services/{sid}/deploys?limit=1", key).json()
        status = deploys[0]["deploy"]["status"] if deploys else None
        if status in ("live", "build_failed", "update_failed", "canceled", "deactivated", "pre_deploy_failed"):
            break
        time.sleep(15)
    print("  deploy:", status)
    if status != "live":
        sys.exit(f"See the logs: https://dashboard.render.com/web/{sid}/logs")
    for _ in range(12):
        try:
            print("  health:", httpx.get(f"{url}/api/health", timeout=60).json())
            break
        except Exception:
            time.sleep(10)
    print(f"\nBackend: {url}\nHiring API: {url}/hire/api\nDocs: {url}/api/docs")


if __name__ == "__main__":
    main()
