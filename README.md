# GLAI

GLAI turns "I want to learn X" into a plan of real tasks, grades the work you hand in, and builds a profile of
skills backed by that work. Recruiters search those profiles on **GLAI Hire** and see the proof behind every
skill: the task, the tests it passed and the review of the code.

- **Hiring site:** https://glai-hire.vercel.app
- **Desktop app:** [Leveller](https://github.com/sahith-ch/v1), where learners do the work

## How it works

```
 Learner (Leveller, desktop)                         Recruiter (GLAI Hire, web)
 ───────────────────────────                         ──────────────────────────
 1. "Learn SQL, 10 h/week"                            search "data analyst"
 2. GLAI researches it and plans milestones                  │
    and tasks around the learner's week                      ▼
 3. Solve a task in its workspace                     ranked candidates, each match
    (editor, SQL lab, notebook, terminal)             linked to the task that proved it
 4. GLAI grades it on the learner's computer:                ▲
    generated tests + code review                            │
 5. Passing work earns the skill, points and rank            │
 6. Progress syncs to the learner's account ──► Supabase ─────┘
```

- **Roadmaps adapt.** Failing a task adds helper tasks or splits it; later milestones are written once you get
  close, shaped by what you found hard.
- **Tests are checked before they count.** Each generated test must pass against a reference solution, or it
  isn't graded.
- **Skills come from evidence.** A skill appears on your graph only when passing work shows it, and the hiring
  search only matches skills that do.

## Run it locally

Requires Python 3.11+ and Node 18+.

```bash
git clone https://github.com/sahith-ch/glai.git && cd glai
pip install -r requirements.txt
cp .env.example .env            # add a Groq key (free) and a Tavily key (web research)

# build the two web apps once
(cd learner-web && npm install && npm run build)
(cd web && npm install && npm run build)

python main.py new "SQL" --hours 10 --goal "junior data analyst job"   # ~2 minutes
python main.py web              # learner app + API on http://127.0.0.1:8001
```

Then run [Leveller](https://github.com/sahith-ch/v1) (`npm install && npm run dev`). It reads your roadmaps from
GLAI on port 8001, and grading runs on your machine.

Other commands: `today`, `status`, `next`, `tests`, `submit`, `done`, `skip`, `fail`, `profile`, `hire`
(local copy of the hiring site on port 8000), `upload`. Run `python main.py <command> --help` for options.

### Models

Any provider LangChain supports, set in `.env`: Groq (free tier, the default), Anthropic, OpenAI, or Ollama for
fully local runs. Semantic search on the hiring site uses small local models (`fastembed`) and falls back to
keyword matching without them.

## Getting on GLAI Hire

In Leveller, open **Progression → Get found on GLAI Hire** and sign in or create an account. From then on, local
GLAI pushes your progress to your account after every graded submission, and your global rank is among everyone
on GLAI Hire. The session is stored in `cloud_account.json` next to your roadmaps and is never committed.

## Project layout

```
roadmap_agent/   the agent and everything behind the API
  graph.py         LangGraph pipeline: research → milestones → schedule → tasks
  evaluate.py      test generation, validation against a reference solution, grading, code review
  workspace.py     per-task environments (code, SQL, web, notebook, terminal)
  user.py          skill graph and stats from verified work
  ranking.py       points, tiers, global and per-area ranks
  hire.py          recruiter search: role and skill parsing, evidence per match
  server.py        local HTTP API for the learner app, Leveller and the hiring site
  api.py           the cloud backend (FastAPI)
  supa.py          storage on Supabase that behaves like a folder, so the same code runs in both places
  cloud.py         desktop sign-in and sync to GLAI Hire
web/             GLAI Hire (React + Vite)
learner-web/     learner dashboard (React + Vite), served by `main.py web`
candidates/      six labelled demo profiles for the hiring site
tests/           python tests/run_all.py
deploy/          Render and Hugging Face deploy scripts
```

## Cloud

| Piece | Where |
|---|---|
| Database + sign-in | Supabase: Postgres, Auth, row-level security on every table |
| Backend | `roadmap_agent/api.py` in Docker on Render (`python deploy/render.py`) |
| Hiring site | `web/` on Vercel |

Each request runs as the signed-in learner, so the database itself keeps learners apart. Running code is
switched off on the shared server (`GLAI_CODE_EXECUTION=off`): grading happens on the learner's computer until
there's a sandboxed runner. Full setup is in [DEPLOY.md](DEPLOY.md).

## Tests

```bash
python tests/run_all.py
```

Tests run offline: model calls, Supabase and the network are faked where they appear.

## Limitations

- Grading runs on the learner's own machine, so a determined user could fake a result. The planned fix is
  grading in a cloud sandbox, or re-running the tests on the server before a skill counts as verified.
- The free Render plan sleeps after 15 idle minutes; the first request after that takes about a minute.
- Profiles are public by default once a learner signs in to GLAI Hire.
