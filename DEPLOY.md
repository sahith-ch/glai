# Deploying GLAI

Three pieces:

| Piece | What | Where |
|---|---|---|
| Database + sign-in | Supabase project **glai** (`lzoyhpbxwuowpbrmnpdm`, Mumbai) — tables `profiles`, `documents`, `rank_snapshots`, all with row-level security | already live |
| Backend | `roadmap_agent/api.py` (FastAPI) in the `Dockerfile` — the learner API at `/api`, the hiring API at `/hire/api` | any Docker host |
| Hiring site | `web/` (static Vite build) | Vercel |

## How it fits together

- Learners sign up / sign in with **Supabase Auth** (email + password). Apps send the access token as
  `Authorization: Bearer …`; the backend verifies it and then reads and writes Postgres **as that learner**,
  so row-level security in the database is what keeps learners apart.
- Everything GLAI used to keep as files lives in `documents` (one row per file: roadmaps, arena, Leveller's saved
  progress, calendar settings). The profile is its own row in `profiles`; public profiles are what recruiters
  search, rank and see.
- `rank_snapshots` keeps one row per learner per day, so rank history works.
- **Running code is off on the shared server** (`GLAI_CODE_EXECUTION=off`): grading, test generation, workspaces and
  the terminal would let anyone run anything on it. They keep working when you run GLAI on your own computer.
  A sandboxed runner is the next step for grading in the cloud.

## Backend

```bash
docker build -t glai-backend .
docker run -p 8080:8080 --env-file .env.production glai-backend   # try it locally
```

Environment (see `.env.production.example`): `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` (both public values),
`GLAI_ALLOWED_ORIGINS` (the hiring site's URL), `GLAI_PUBLIC_URL` (this backend's URL), and the same model / search
keys as your local `.env` (`LLM_PROVIDER`, `LLM_MODEL`, `GROQ_API_KEY`, `TAVILY_API_KEY`).

Health check: `GET /api/health`. API docs: `/api/docs`.

### On Render (free)

```bash
# fill in .env.deploy (Docker Hub user + access token, Render API key), then:
python deploy/render.py
```

Builds the image, pushes it to a **private** Docker Hub repository, creates (or updates) the `glai-backend` web
service on Render's free plan in Singapore with the environment above, and waits until it is live. On the free
plan (512 MB) the hiring search runs on keywords (`GLAI_SEMANTIC_SEARCH=off`; the semantic models need ~370 MB),
and the service sleeps after 15 idle minutes — the first request after that takes about a minute.

(`deploy/hf_space.py` does the same for a Hugging Face Space, which now needs a PRO subscription for Docker.)

## Hiring site

```bash
cd web
VITE_API_BASE=https://<your-backend>/hire npm run build
vercel deploy --prod
```

`VITE_API_BASE` is read at build time; set it in the Vercel project settings for later deploys.

## Desktop learners → GLAI Hire

Learners work in Leveller (or `python main.py web`), where grading runs on their own computer. The **GLAI Hire**
card on Leveller's Progression screen signs them in (or creates an account); local GLAI then pushes their
progress to that account by itself — straight after each graded submission, and whenever a roadmap, Arena or
Leveller's saved state changes. Their public profile, skill graph and rank update on the hiring site, and their
global rank in Leveller is among everyone on GLAI Hire.

- Session: `cloud_account.json` next to the roadmaps (a Supabase refresh token, readable only by the user;
  never pushed, never in the Docker image).
- Code: `roadmap_agent/cloud.py`; routes `GET /api/cloud`, `POST /api/cloud/{signin,signup,sync,signout}`.
- Supabase project defaults are built in (public values); `SUPABASE_URL` / `SUPABASE_PUBLISHABLE_KEY` override them.
- For a demo, turn off **Confirm email** (Supabase → Authentication → Providers → Email) so new accounts can sign in
  straight away; Supabase's built-in mailer only delivers to the project's team.

## Moving your local data into your account

```bash
python main.py upload --email you@example.com      # roadmaps, Arena, Leveller progress, profile
```

The Google Calendar connection is not copied (it belongs to the local sign-in address): connect it again from the
deployed app.
