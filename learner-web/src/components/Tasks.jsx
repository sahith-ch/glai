import { useState } from "react";
import { pct, span } from "../format.js";
import Submit from "./Submit.jsx";
import { useApp } from "../appContext.js";

const STATUS_ICON = { done: "✓", failed: "↻", todo: "" };

export function TaskRow({ t, open: startOpen = false, roadmap = null, submitApi = null, label = null }) {
  const [open, setOpen] = useState(startOpen);
  const app = useApp();
  // with several roadmaps the CLI needs to know which one (it defaults to the newest)
  const plan = roadmap || app.roadmap ? ` --plan ${roadmap || app.roadmap}.json` : "";
  const chips = [
    `${t.hours}h`,
    `${t.difficulty} · ${t.status === "done" ? `+${t.points}` : t.points} pts`,
    t.kind === "remedial" ? "helper" : t.kind === "step" ? "step" : null,
    t.status === "failed" ? `retry · failed ${t.fail_count}×` : null,
    t.submission
      ? `tests ${t.submission.tests} ${t.submission.passed ? "passed" : "failed"}${t.submission.score != null ? ` · ${t.submission.score}/100` : ""}`
      : null,
  ].filter(Boolean);
  return (
    <li className={`task ${t.status}`}>
      <button className="task-head" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="check" aria-label={t.status}>{STATUS_ICON[t.status]}</span>
        <span className="task-main">
          <span className="task-title">{t.title}</span>
          <span className="muted small">
            {label || t.id}
            {t.status !== "done" && t.start ? ` · ${span(t.start, t.end)}` : ""}
            {t.topic ? ` · ${t.topic}` : ""}
          </span>
        </span>
        <span className="chips">{chips.map((c) => <span key={c} className="tag">{c}</span>)}</span>
      </button>
      {open && (
        <div className="task-body">
          <p>{t.description}</p>
          <p><span className="muted">Deliverable:</span> {t.deliverable}</p>
          {t.depends_on.length > 0 && <p className="muted small">After: {t.depends_on.join(", ")}</p>}
          {t.spent_hours > 0 && t.status !== "done" && (
            <p className="muted small">{t.spent_hours}h already spent on this attempt</p>
          )}
          <Submit t={t} roadmap={roadmap} api={submitApi} />
          {t.status !== "done" && !submitApi && (
            <p className="cmds small muted">
              No code to check? <code>python main.py done {t.id}{plan}</code> · Not starting today? <code>python main.py skip {t.id}{plan}</code>
            </p>
          )}
        </div>
      )}
    </li>
  );
}

export function Milestone({ m, open }) {
  const complete = m.tasks_written && m.total > 0 && m.done === m.total;
  return (
    <details className="milestone" open={open ?? m.current}>
      <summary>
        <span className={`num ${complete ? "complete" : ""} ${m.current ? "current" : ""}`}>{complete ? "✓" : m.index}</span>
        <span className="ms-main">
          <span className="ms-title">{m.title}</span>
          <span className="muted small">
            Week{m.week_start === m.week_end ? ` ${m.week_start}` : `s ${m.week_start}–${m.week_end}`} · ~{Math.round(m.hours)}h ·{" "}
            {m.tasks_written ? `${m.done}/${m.total} tasks` : "tasks written later"}
          </span>
        </span>
        <span className="bar"><i style={{ width: m.total ? pct(m.done / m.total) : 0 }} /></span>
      </summary>
      <div className="ms-body">
        <p className="muted small">{m.objective}</p>
        {m.tasks_written ? (
          <>
            <ul className="task-list">{m.tasks.map((t) => <TaskRow key={t.id} t={t} />)}</ul>
            {m.success_criteria.length > 0 && (
              <>
                <p className="small done-when">Done when</p>
                <ul className="criteria small muted">{m.success_criteria.map((c) => <li key={c}>{c}</li>)}</ul>
              </>
            )}
          </>
        ) : (
          <p className="small">
            Tasks are written when you get close to this milestone, based on how the earlier ones went.
            {m.scheduled && ` Scheduled ${span(m.scheduled[0], m.scheduled[1])}.`}
          </p>
        )}
      </div>
    </details>
  );
}
