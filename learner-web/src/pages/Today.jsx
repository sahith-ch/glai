import { fmtDay, pct } from "../format.js";
import { navigate } from "../router.jsx";
import { useFetch } from "../useFetch.js";
import { PageHead } from "../components/RoadmapHeader.jsx";
import { TaskRow } from "../components/Tasks.jsx";

function SkillGroup({ g, onOpen }) {
  const hasWork = g.tasks.length > 0 || g.done_today.length > 0;
  return (
    <section className="quest-group">
      <div className="quest-head">
        <button className="quest-skill" onClick={() => onOpen(g.file)}>{g.skill}</button>
        <span className="muted small">
          {g.study_today ? `~${g.hours_today}h today` : `Rest day · next quest ${fmtDay(g.next_day)}`}
          {g.milestone ? ` · ${g.milestone}` : ""}
        </span>
        <span className="quest-progress">
          <span className="bar"><i style={{ width: pct(g.progress) }} /></span>
          <span className="mono small muted">{pct(g.progress)}</span>
        </span>
      </div>
      {hasWork ? (
        <ul className="task-list">
          {g.done_today.map((t) => <TaskRow key={`done-${t.id}`} t={t} roadmap={g.file} />)}
          {g.tasks.map((t) => <TaskRow key={t.id} t={t} roadmap={g.file} open={t.id === g.next_task && g.done_today.length === 0} />)}
        </ul>
      ) : (
        <p className="muted small rest">
          {g.study_today ? "Nothing left for today — today's time for this skill is used up." : "No quests today for this skill."}
        </p>
      )}
    </section>
  );
}

export default function Today({ refreshKey, choose }) {
  const { data, error } = useFetch("/api/today", refreshKey);
  if (error) return <p className="empty">Couldn't load today's quests ({error.message}).</p>;
  if (!data) return <p className="muted">Loading…</p>;

  const open = (file) => {
    choose(file);
    navigate("/overview");
  };
  const left = data.quests;
  const summary = left
    ? `${left} quest${left === 1 ? "" : "s"} across ${data.skills_today} skill${data.skills_today === 1 ? "" : "s"} · about ${data.hours_today}h`
    : data.done ? "All done for today." : "No quests today.";

  return (
    <>
      <PageHead eyebrow={fmtDay(data.today)} title="Today's quests">
        <p className="lede">
          {summary}
          {data.done > 0 && left > 0 && ` · ${data.done} finished already`}
        </p>
      </PageHead>
      {data.groups.length === 0 ? (
        <p className="muted">No roadmaps yet — create one to get daily quests.</p>
      ) : (
        // skills with work today first, rest days after
        [...data.groups]
          .sort((a, b) => (b.tasks.length > 0) - (a.tasks.length > 0))
          .map((g) => <SkillGroup key={g.file} g={g} onOpen={open} />)
      )}
    </>
  );
}
