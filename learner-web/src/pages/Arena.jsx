import { useEffect, useState } from "react";
import { postJSON, useJob } from "../appContext.js";
import { useFetch } from "../useFetch.js";
import { PageHead } from "../components/RoadmapHeader.jsx";
import { TaskRow } from "../components/Tasks.jsx";

const LEVELS = ["easy", "medium", "hard"];

function NewChallenge({ roadmaps, points, onCreated }) {
  const [roadmap, setRoadmap] = useState(roadmaps[0]?.file || "");
  const [difficulty, setDifficulty] = useState("medium");
  const [jobId, setJobId] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (!roadmaps.some((r) => r.file === roadmap) && roadmaps.length) setRoadmap(roadmaps[0].file);
  }, [roadmaps]);
  const job = useJob(jobId, (res) => {
    setJobId(null);
    onCreated(res.id);
  });
  const busy = job?.status === "running" || (jobId && !job);
  const failed = job?.status === "error" ? job.error : null;

  const start = async () => {
    setError(null);
    try {
      setJobId((await postJSON("/api/arena/challenge", { roadmap, difficulty })).job);
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <div className="card arena-new">
      <label className="arena-field">
        <span className="small muted">Skill</span>
        <select value={roadmap} onChange={(e) => setRoadmap(e.target.value)} disabled={busy}>
          {roadmaps.map((r) => <option key={r.file} value={r.file}>{r.skill}</option>)}
        </select>
      </label>
      <div className="arena-field">
        <span className="small muted">Difficulty</span>
        <div className="segmented" role="radiogroup" aria-label="Difficulty">
          {LEVELS.map((l) => (
            <button key={l} type="button" role="radio" aria-checked={difficulty === l}
              className={difficulty === l ? "on" : ""} onClick={() => setDifficulty(l)} disabled={busy}>
              {l} <span className="mono">{points[l]}</span>
            </button>
          ))}
        </div>
      </div>
      <button className="primary" onClick={start} disabled={busy || !roadmap}>
        {busy ? "Writing challenge…" : "New challenge"}
      </button>
      {(error || failed) && <p className="error small full">{error || failed}</p>}
    </div>
  );
}

function QuestGroup({ g, difficulty }) {
  const milestones = g.milestones
    .map((m) => ({ ...m, tasks: m.tasks.filter((t) => difficulty === "all" || t.difficulty === difficulty) }))
    .filter((m) => m.tasks.length);
  return (
    <div className="arena-group">
      <div className="arena-group-head">
        <span className="ahead-skill">{g.skill}</span>
        <span className="mono small muted">
          {g.done}/{g.total} done{g.locked_milestones ? ` · ${g.locked_milestones} milestone${g.locked_milestones > 1 ? "s" : ""} unlock as you progress` : ""}
        </span>
      </div>
      {milestones.length ? (
        milestones.map((m) => (
          <div key={m.index} className="arena-milestone">
            <span className="muted small">Milestone {m.index} · {m.title}</span>
            <ul className="task-list">{m.tasks.map((t) => <TaskRow key={t.id} t={t} roadmap={g.file} />)}</ul>
          </div>
        ))
      ) : (
        <p className="muted small rest">{g.total && g.done === g.total ? "Every written quest is done." : "No quests match this filter."}</p>
      )}
    </div>
  );
}

export default function Arena({ refreshKey }) {
  const [key, setKey] = useState(0);
  const [fresh, setFresh] = useState(null);
  const [skill, setSkill] = useState("all");
  const [difficulty, setDifficulty] = useState("all");
  const { data, error } = useFetch("/api/arena", `${refreshKey}-${key}`);
  if (error) return <p className="empty">Couldn't load the arena ({error.message}).</p>;
  if (!data) return <p className="muted">Loading…</p>;

  const groups = data.quest_groups.filter((g) => skill === "all" || g.file === skill);
  const open = data.challenges.filter((c) => c.status !== "done");
  const solved = data.challenges.filter((c) => c.status === "done");
  const api = (c) => ({ tests: "/api/arena/tests", submit: "/api/arena/submit", body: { id: c.id } });
  const label = (c) => `${c.id} · ${c.skill}`;

  return (
    <>
      <PageHead eyebrow="Arena" title="Solve anything, anytime">
        <p className="lede">
          Every open quest from your roadmaps, ready now — no waiting for its day. Passing one counts exactly as if
          you'd done it on schedule.
        </p>
        <div className="meta">
          <span>{data.open_quests} open quest{data.open_quests === 1 ? "" : "s"}</span>
          <span>{data.solved} extra challenge{data.solved === 1 ? "" : "s"} solved · {data.points} pts</span>
        </div>
      </PageHead>

      {data.roadmaps.length === 0 ? (
        <p className="muted">Create a roadmap first — its quests show up here.</p>
      ) : (
        <>
          <div className="arena-filters">
            <select value={skill} onChange={(e) => setSkill(e.target.value)} aria-label="Filter by skill">
              <option value="all">All skills</option>
              {data.roadmaps.map((r) => <option key={r.file} value={r.file}>{r.skill}</option>)}
            </select>
            <div className="segmented" role="radiogroup" aria-label="Filter by difficulty">
              {["all", ...LEVELS].map((l) => (
                <button key={l} type="button" role="radio" aria-checked={difficulty === l}
                  className={difficulty === l ? "on" : ""} onClick={() => setDifficulty(l)}>{l}</button>
              ))}
            </div>
          </div>

          <section className="arena-quests">
            <h2>Roadmap quests</h2>
            {groups.map((g) => <QuestGroup key={g.file} g={g} difficulty={difficulty} />)}
          </section>

          <section>
            <h2>Extra challenges</h2>
            <p className="muted small section-note">
              Want more practice? Generate a fresh problem from what you've covered. Earns points, never changes your roadmap.
            </p>
            <NewChallenge roadmaps={data.roadmaps} points={data.points_by_difficulty}
              onCreated={(id) => { setFresh(id); setKey((k) => k + 1); }} />
            {open.length > 0 && (
              <ul className="task-list challenges">
                {open.map((c) => <TaskRow key={c.id} t={c} label={label(c)} submitApi={api(c)} open={c.id === fresh} />)}
              </ul>
            )}
            {solved.length > 0 && (
              <>
                <p className="muted small solved-label">Solved</p>
                <ul className="task-list">
                  {solved.map((c) => <TaskRow key={c.id} t={c} label={label(c)} submitApi={api(c)} />)}
                </ul>
              </>
            )}
          </section>
        </>
      )}
    </>
  );
}
