import { fmtDay } from "../format.js";
import { Link } from "../router.jsx";
import { PageHead, RoadmapTiles } from "../components/RoadmapHeader.jsx";
import { Milestone, TaskRow } from "../components/Tasks.jsx";

export default function Overview({ r, me }) {
  const tasks = r.milestones.flatMap((m) => m.tasks);
  const todays = r.today_tasks.map((id) => tasks.find((t) => t.id === id)).filter(Boolean);
  const current = r.milestones.find((m) => m.current);
  return (
    <>
      <PageHead eyebrow={me?.name ? `${me.name}'s roadmap` : "Your roadmap"} title={r.skill}>
        <p className="lede">{r.summary}</p>
        <div className="meta">
          <span>{r.profile.current_level}</span>
          <span>{r.profile.weekly_hours}h/week · {r.profile.days_per_week} days (~{r.daily_hours}h/day)</span>
          {r.profile.goal && <span>Goal: {r.profile.goal}</span>}
        </div>
      </PageHead>

      <RoadmapTiles r={r} me={me} />

      <section>
        <h2>{r.is_today ? "Today" : `Next study day · ${fmtDay(r.study_day)}`}</h2>
        {todays.length ? (
          <ul className="task-list">{todays.map((t) => <TaskRow key={t.id} t={t} open={t.id === r.next_task} />)}</ul>
        ) : (
          <p className="muted">Nothing scheduled — today's study time is used up, or the roadmap is complete.</p>
        )}
      </section>

      {current && (
        <section>
          <div className="section-head">
            <h2>Current milestone</h2>
            <Link to="/roadmap" className="text-link">Full roadmap</Link>
          </div>
          <Milestone m={current} open />
        </section>
      )}
    </>
  );
}
