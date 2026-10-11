import { useEffect, useRef, useState } from "react";
import { PageHead } from "../components/RoadmapHeader.jsx";
import { apiFetch } from "../auth.js";

const STEPS = [
  ["plan_queries", "Planning web searches"],
  ["search", "Searching the web"],
  ["synthesize", "Summarising the research"],
  ["outline", "Outlining milestones"],
  ["schedule", "Scheduling over your weeks"],
  ["write_tasks", "Writing the first milestone's tasks"],
  ["saved", "Saving"],
];
const today = () => new Date().toISOString().slice(0, 10);

function Progress({ job, elapsed }) {
  const seen = new Set(job.events.map((e) => e.kind));
  const searches = job.events.filter((e) => e.kind === "search").length;
  const queries = job.events.find((e) => e.kind === "plan_queries")?.data.queries || [];
  const active = STEPS.find(([k]) => !seen.has(k))?.[0];
  return (
    <div className="card job">
      <div className="job-head">
        <b>{job.status === "done" ? "Your roadmap is ready" : job.status === "error" ? "Something went wrong" : `Building “${job.skill}”`}</b>
        <span className="muted small">{elapsed}s</span>
      </div>
      <ol className="steps">
        {STEPS.map(([k, label]) => {
          const done = seen.has(k) && (k !== "search" || seen.has("synthesize"));
          const now = job.status === "running" && (k === active || (k === "search" && seen.has("search") && !seen.has("synthesize")));
          return (
            <li key={k} className={done ? "done" : now ? "now" : ""}>
              <span className="step-dot">{done ? "✓" : ""}</span>
              {label}
              {k === "search" && searches > 0 && <span className="muted small"> · {searches}/{queries.length || "…"}</span>}
            </li>
          );
        })}
      </ol>
      {job.error && <p className="error small">{job.error}</p>}
    </div>
  );
}

export default function NewRoadmap({ onCreated }) {
  const [form, setForm] = useState({
    skill: "", level: "beginner", weekly_hours: 10, days_per_week: 7, target_weeks: "", goal: "", known_skills: "", start: today(),
  });
  const [error, setError] = useState(null);
  const [job, setJob] = useState(null);
  const [elapsed, setElapsed] = useState(0);
  const startedAt = useRef(0);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  useEffect(() => {
    if (!job || job.status !== "running") return undefined;
    const timer = setInterval(async () => {
      setElapsed(Math.round((Date.now() - startedAt.current) / 1000));
      const r = await apiFetch(`/api/jobs/${job.id}`);
      if (r.ok) {
        const next = await r.json();
        setJob(next);
        if (next.status === "done") onCreated(next.file);
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [job?.id, job?.status]);

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    const r = await apiFetch("/api/roadmaps", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(form),
    });
    const body = await r.json();
    if (!r.ok) return setError(body.error || "Couldn't start.");
    startedAt.current = Date.now();
    setElapsed(0);
    setJob({ id: body.job, status: "running", skill: form.skill, events: [] });
  };

  const busy = job?.status === "running";
  return (
    <>
      <PageHead eyebrow="New roadmap" title="What do you want to learn?">
        <p className="lede">We research it on the web, plan milestones around your time, and write specific tasks for the first one. Takes about two minutes.</p>
      </PageHead>

      <div className="new-layout">
        <form className="card form" onSubmit={submit}>
          <label className="full">
            <span className="label-text">Skill or course</span>
            <input value={form.skill} onChange={set("skill")} placeholder="e.g. Data Analysis with Python" required disabled={busy} autoFocus />
          </label>
          <label>
            <span className="label-text">Level</span>
            <select value={form.level} onChange={set("level")} disabled={busy}>
              <option value="beginner">Beginner</option>
              <option value="intermediate">Intermediate</option>
              <option value="advanced">Advanced</option>
            </select>
          </label>
          <label>
            <span className="label-text">Hours per week</span>
            <input type="number" min="1" max="60" value={form.weekly_hours} onChange={set("weekly_hours")} disabled={busy} />
          </label>
          <label>
            <span className="label-text">Study days per week</span>
            <select value={form.days_per_week} onChange={set("days_per_week")} disabled={busy}>
              <option value="7">7 (every day)</option>
              <option value="6">6 (Mon–Sat)</option>
              <option value="5">5 (Mon–Fri)</option>
            </select>
          </label>
          <label>
            <span className="label-text">Target weeks <span className="muted">(optional)</span></span>
            <input type="number" min="1" max="104" value={form.target_weeks} onChange={set("target_weeks")} placeholder="e.g. 12" disabled={busy} />
          </label>
          <label className="full">
            <span className="label-text">Goal <span className="muted">(optional)</span></span>
            <input value={form.goal} onChange={set("goal")} placeholder="e.g. land a junior data analyst job" disabled={busy} />
          </label>
          <label className="full">
            <span className="label-text">What you already know <span className="muted">(optional, comma-separated)</span></span>
            <input value={form.known_skills} onChange={set("known_skills")} placeholder="e.g. Excel pivot tables, basic SQL" disabled={busy} />
          </label>
          <label>
            <span className="label-text">Start date</span>
            <input type="date" value={form.start} onChange={set("start")} disabled={busy} />
          </label>
          <div className="form-foot full">
            {error && <span className="error small">{error}</span>}
            <button type="submit" className="primary" disabled={busy || !form.skill.trim()}>
              {busy ? "Building…" : "Create roadmap"}
            </button>
          </div>
        </form>

        {job ? (
          <Progress job={job} elapsed={elapsed} />
        ) : (
          <div className="card hint">
            <b>Tips</b>
            <ul className="small muted">
              <li>Be honest about hours — the schedule is built from them.</li>
              <li>Listing what you already know skips those topics and adds a short skills check.</li>
              <li>Later milestones are written as you get close, using what you found hard.</li>
            </ul>
          </div>
        )}
      </div>
    </>
  );
}
