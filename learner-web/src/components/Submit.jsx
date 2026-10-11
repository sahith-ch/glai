import { useState } from "react";
import { postJSON, useApp, useJob } from "../appContext.js";

function EvaluationCard({ e, outcome }) {
  const r = e.review;
  return (
    <div className={`eval ${e.passed ? "pass" : "fail"}`}>
      <div className="eval-head">
        <b>{e.passed ? "Passed" : "Not passed yet"}</b>
        <span className="mono small">tests {e.tests_passed}/{e.tests_total}{r ? ` · review ${r.score}/100` : ""}</span>
      </div>
      {outcome && <Outcome outcome={outcome} passed={e.passed} />}
      {e.failures.length > 0 && (
        <ul className="failures small">{e.failures.slice(0, 8).map((f) => <li key={f}>{f}</li>)}</ul>
      )}
      {r && (
        <div className="review small">
          {[["Strengths", r.strengths], ["Issues", r.issues], ["Next steps", r.next_steps]].map(([title, items]) =>
            items.length ? (
              <div key={title}>
                <span className="review-title">{title}</span>
                <ul>{items.slice(0, 5).map((i) => <li key={i}>{i}</li>)}</ul>
              </div>
            ) : null,
          )}
        </div>
      )}
      <p className="muted small mono">{e.at.replace("T", " ")} · {e.source}</p>
    </div>
  );
}

function Outcome({ outcome, passed }) {
  return (
    <>
      <PlanChange outcome={outcome} passed={passed} />
      {outcome.written?.length > 0 && (
        <p className="outcome">Next milestone written: {outcome.written.join(", ")} — shaped by what you found hard so far.</p>
      )}
    </>
  );
}

function PlanChange({ outcome, passed }) {
  if (outcome.action === "done") {
    return <p className="outcome">Task marked done · +{outcome.points} pts (max {outcome.max_points})</p>;
  }
  if (outcome.action === "continue") {
    return <p className="outcome">Not a full attempt yet — keep going next study day, then resubmit.</p>;
  }
  if (outcome.action === "helpers" || outcome.action === "split") {
    return (
      <div className="outcome">
        <p>{outcome.action === "helpers" ? "Added tasks that build what this one needs — do these first:" : "Split into smaller steps:"}</p>
        <ul>{outcome.added.map((a) => <li key={a.id}><span className="mono">{a.id}</span> {a.title} · {a.hours}h</li>)}</ul>
      </div>
    );
  }
  return passed ? null : <p className="muted small">Fix the failures and resubmit. Add the hours you spent if you're stuck, and the plan will adapt.</p>;
}

const STEPS = { tests: "Writing tests and checking them against a reference solution", run: "Running your code and reviewing it", adapt: "Adapting your plan", write_next: "Writing your next milestone" };

function JobSteps({ job }) {
  return (
    <ol className="steps compact">
      {job.events.filter((e) => STEPS[e.kind]).map((e, i, arr) => {
        const now = job.status === "running" && i === arr.length - 1;
        return (
          <li key={e.kind} className={now ? "now" : "done"}>
            <span className="step-dot">{now ? "" : "✓"}</span>{STEPS[e.kind]}
          </li>
        );
      })}
      {job.status === "running" && !job.events.some((e) => STEPS[e.kind]) && (
        <li className="now"><span className="step-dot" />Starting</li>
      )}
    </ol>
  );
}

// `api` lets the arena reuse this for challenges: {tests, submit, body}
export default function Submit({ t, roadmap: own = null, api = null }) {
  const app = useApp();
  const roadmap = own || app.roadmap;
  const { refresh } = app;
  const endpoints = api || { tests: "/api/tests", submit: "/api/submit", body: { roadmap, task_id: t.id } };
  const [tests, setTests] = useState(t.tests);
  const [showChecks, setShowChecks] = useState(false);
  const [form, setForm] = useState({ source: "", spent: "", branch: "", subdir: "" });
  const [more, setMore] = useState(false);
  const [error, setError] = useState(null);
  const [testsJob, setTestsJob] = useState(null);
  const [submitJob, setSubmitJob] = useState(null);
  const [result, setResult] = useState(null);
  const [openHistory, setOpenHistory] = useState(null);

  const tj = useJob(testsJob, (view) => setTests(view));
  const sj = useJob(submitJob, (res) => {
    setResult(res);
    refresh(); // plan may have changed: done, helper tasks, points
  });

  const prepare = async () => {
    setError(null);
    try {
      setTestsJob((await postJSON(endpoints.tests, endpoints.body)).job);
    } catch (e) {
      setError(e.message);
    }
  };
  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    setResult(null);
    try {
      setSubmitJob((await postJSON(endpoints.submit, { ...endpoints.body, ...form })).job);
    } catch (err) {
      setError(err.message);
    }
  };
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const busy = sj?.status === "running";
  const failed = (tj?.status === "error" && tj.error) || (sj?.status === "error" && sj.error);

  return (
    <div className="submit">
      <div className="submit-head">
        <h4>Submit your work</h4>
        <button type="button" className="ghost small" onClick={() => (tests.ready ? setShowChecks(!showChecks) : prepare())}
          disabled={tj?.status === "running"}>
          {tests.ready ? (showChecks ? "Hide what's checked" : "What's checked") : tj?.status === "running" ? "Preparing tests…" : "Prepare tests"}
        </button>
      </div>
      <p className="muted small">
        Files: <span className="mono">{tests.required_files.join(", ") || "the deliverable file(s)"}</span>. {tests.format}
      </p>
      {tj?.status === "running" && <JobSteps job={tj} />}
      {tests.ready && showChecks && (
        <ul className="checks small">
          {tests.checks.map((c) => (
            <li key={c.verifies}>{c.verifies} <span className="muted">— task: “{c.quote}”</span></li>
          ))}
          {tests.dropped > 0 && <li className="muted">{tests.dropped} generated test(s) not graded (invented a requirement or failed against the reference).</li>}
        </ul>
      )}

      {t.status !== "done" && (
        <form className={`submit-form ${api ? "no-hours" : ""}`} onSubmit={submit}>
          <input value={form.source} onChange={set("source")} placeholder="Folder path or git repo URL" aria-label="Folder path or git repo URL" disabled={busy} required />
          {!api && (
            <input value={form.spent} onChange={set("spent")} type="number" min="0" step="0.5" placeholder="Hours spent" aria-label="Hours spent" disabled={busy} className="hours" />
          )}
          <button type="submit" className="primary" disabled={busy || !form.source.trim()}>{busy ? "Running…" : "Run tests"}</button>
          <button type="button" className="ghost small more" onClick={() => setMore(!more)}>{more ? "Fewer options" : "Branch / subfolder"}</button>
          {more && (
            <div className="more-fields">
              <input value={form.branch} onChange={set("branch")} placeholder="Git branch (optional)" aria-label="Git branch" disabled={busy} />
              <input value={form.subdir} onChange={set("subdir")} placeholder="Subfolder (optional)" aria-label="Subfolder" disabled={busy} />
            </div>
          )}
        </form>
      )}

      {sj?.status === "running" && <JobSteps job={sj} />}
      {(error || failed) && <p className="error small">{error || failed}</p>}
      {result && <EvaluationCard e={result.evaluation} outcome={result.outcome} />}

      {t.evaluations.length > 0 && !result && (
        <div className="history">
          <span className="muted small">Previous submissions</span>
          {t.evaluations.map((e, i) => (
            <div key={e.at + i}>
              <button type="button" className="history-row" onClick={() => setOpenHistory(openHistory === i ? null : i)}>
                <span className="mono small">{e.at.slice(0, 16).replace("T", " ")}</span>
                <span className={e.passed ? "pass-text" : "muted"}>{e.passed ? "passed" : "not passed"}</span>
                <span className="mono small muted">tests {e.tests_passed}/{e.tests_total}{e.review ? ` · ${e.review.score}/100` : ""}</span>
              </button>
              {openHistory === i && <EvaluationCard e={e} />}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
