import { useState } from "react";
import { fmtDay, pct } from "../format.js";
import { useFetch } from "../useFetch.js";
import { PageHead } from "../components/RoadmapHeader.jsx";

// one hue, light -> dark (sequential); empty days stay neutral grey
const LEVELS = [
  { max: 0, cls: "l0" },
  { max: 1, cls: "l1" },
  { max: 2, cls: "l2" },
  { max: 3, cls: "l3" },
  { max: Infinity, cls: "l4" },
];
const level = (h) => LEVELS.find((l) => h <= l.max).cls;

function Heatmap({ activity }) {
  const [hover, setHover] = useState(null);
  const weeks = [];
  activity.forEach((d, i) => {
    (weeks[Math.floor(i / 7)] ||= []).push(d);
  });
  const total = activity.reduce((s, d) => s + d.hours, 0);
  const active = activity.filter((d) => d.hours > 0 || d.tasks > 0).length;
  return (
    <div className="card">
      <div className="heatmap" onMouseLeave={() => setHover(null)}>
        <div className="hm-days muted"><span>Mon</span><span /><span>Wed</span><span /><span>Fri</span><span /><span /></div>
        {weeks.map((w, i) => (
          <div key={i} className="hm-week">
            {w.map((d) => (
              <span
                key={d.date}
                className={`hm-cell ${level(d.hours)}`}
                onMouseEnter={() => setHover(d)}
                onFocus={() => setHover(d)}
                tabIndex={0}
                aria-label={`${fmtDay(d.date)}: ${d.hours} hours, ${d.tasks} tasks`}
              />
            ))}
          </div>
        ))}
      </div>
      <div className="hm-foot small">
        <span className="muted">
          {hover
            ? `${fmtDay(hover.date)} · ${hover.hours}h · ${hover.tasks} task${hover.tasks === 1 ? "" : "s"} done`
            : `${Math.round(total * 10) / 10}h over ${active} active days in the last 12 weeks`}
        </span>
        <span className="hm-key muted">
          Less {LEVELS.map((l) => <i key={l.cls} className={`hm-cell ${l.cls}`} />)} More
        </span>
      </div>
    </div>
  );
}

function Bars({ rows }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return (
    <div className="bars">
      {rows.map((r) => (
        <div key={r.label} className="bar-row">
          <span className="bar-label">{r.label}</span>
          <span className="bar-track"><i style={{ width: `${(r.value / max) * 100}%` }} /></span>
          <span className="bar-value">{r.value}</span>
        </div>
      ))}
    </div>
  );
}

export default function Stats({ me, refreshKey }) {
  const { data: act } = useFetch("/api/stats", refreshKey);
  if (!me) return <p className="muted">Loading…</p>;
  const st = me.stats;
  const tiles = [
    ["Points", st.points, "accent"],
    ["Hours logged", st.hours_logged],
    ["Tasks done", st.tasks_done],
    ["First-try success", st.first_try_rate == null ? "–" : pct(st.first_try_rate)],
    ["Current streak", `${st.current_streak} d`],
    ["Best streak", `${st.longest_streak} d`],
    ["Submissions passed", st.submissions ? `${st.submissions_passed} / ${st.submissions}` : "–"],
    ["Code review", st.avg_code_score == null ? "–" : Math.round(st.avg_code_score)],
  ];
  const diff = ["easy", "medium", "hard"].map((k) => ({ label: k[0].toUpperCase() + k.slice(1), value: st.points_by_difficulty?.[k] || 0 }));

  return (
    <>
      <PageHead eyebrow="Stats" title="How it's going">
        <p className="lede">
          {st.failures} real failures, {st.skips} skipped days and {st.helper_tasks} helper tasks so far —
          the plan adapts to all of them.
        </p>
      </PageHead>

      <div className="tiles">
        {tiles.map(([k, v, cls]) => (
          <div key={k} className={`tile ${cls || ""}`}><b>{v}</b><span>{k}</span></div>
        ))}
      </div>

      <section>
        <h2>Activity</h2>
        {act ? <Heatmap activity={act.activity} /> : <p className="muted small">Loading…</p>}
      </section>

      <div className="two-col">
        <section>
          <h2>Points by difficulty</h2>
          <div className="card">
            {st.points ? <Bars rows={diff} /> : <p className="muted small">Finish tasks to earn points — harder ones earn more.</p>}
          </div>
        </section>
        <section>
          <h2>Roadmaps</h2>
          <div className="card">
            {st.roadmaps.length ? (
              <div className="bars">
                {st.roadmaps.map((r) => (
                  <div key={r.file} className="bar-row">
                    <span className="bar-label">{r.skill}</span>
                    <span className="bar-track"><i style={{ width: pct(r.progress) }} /></span>
                    <span className="bar-value">{pct(r.progress)}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted small">No roadmaps yet.</p>
            )}
          </div>
        </section>
      </div>
    </>
  );
}
