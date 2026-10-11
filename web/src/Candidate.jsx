import { useEffect, useMemo, useState } from "react";
import { Link } from "./router.jsx";
import { useFetch } from "./useFetch.js";
import { api } from "./api.js";
import SkillGraph, { buildGraph } from "./SkillGraph.jsx";
import SkillPanel from "./SkillPanel.jsx";
import { Avatar, DemoBadge, Icon, TierBadge, pct, score, topText } from "./ui.jsx";

const date = (d) => (d ? new Date(`${d}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "");

// how this person matched the recruiter's search, from the same ranking the results page used
function SearchMatch({ id, query }) {
  const { data } = useFetch(query ? api(`/api/search?q=${encodeURIComponent(query)}`) : null);
  const r = data?.results.find((x) => x.id === id);
  if (!query || !r || !r.evidence.length) return null;
  return (
    <section className="card match-card">
      <h2 className="card-title">How they match “{query}”</h2>
      <ul className="match-list">
        {r.evidence.map((e) => (
          <li key={e.term}>
            <Icon name="checkCircle" size={17} className="ok" />
            <div>
              <b>{e.term}</b>
              {e.kind === "role"
                ? <div className="muted small">{e.covered.length} of {e.covered.length + e.missing.length} skills: {e.covered.join(", ")}{e.missing.length ? ` · not yet: ${e.missing.join(", ")}` : ""}</div>
                : <div className="muted small">{e.skill}{e.proof ? ` · proved by “${e.proof.title}”${e.proof.tests ? ` · tests ${e.proof.tests}` : ""}${e.proof.review_score != null ? ` · review ${e.proof.review_score}/100` : ""}` : ""}</div>}
            </div>
          </li>
        ))}
        {r.missing_terms.map((t) => <li key={t} className="missing"><span className="miss-dot" /><div><b>{t}</b><div className="small">Not shown in their work yet</div></div></li>)}
      </ul>
    </section>
  );
}

function RankCard({ ranking }) {
  if (!ranking) return null;
  const o = ranking.overall;
  const next = o.next_tier;
  const progress = next ? o.points / (o.points + next.points_needed) : 1;
  return (
    <section className="card rank-card">
      <span className="card-kicker">Global rank</span>
      <div className="rank-big">#{o.rank}<span> of {o.total}</span></div>
      <div className="rank-line">{o.total === 1 ? "Only one ranked" : `Top ${o.top_percent}%`} <TierBadge tier={o.tier} /></div>
      {next && (
        <div className="tier-progress">
          <span className="bar"><i style={{ width: `${progress * 100}%` }} /></span>
          <span className="muted small">{next.points_needed} points to {next.tier}</span>
        </div>
      )}
      {ranking.areas.length > 0 && (
        <ul className="area-ranks">
          {ranking.areas.map((a) => (
            <li key={a.area}>
              <Link to={`/leaderboard?area=${encodeURIComponent(a.area)}`}>{a.area}</Link>
              <span><b>#{a.rank}</b><span className="muted"> / {a.total}</span></span>
              <span className="muted small">{topText(a)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Strongest({ graph, onSelect }) {
  const top = graph.nodes
    .filter((n) => n.status === "completed" || n.status === "verified")
    .sort((a, b) => b.points - a.points || b.tasks.length - a.tasks.length)
    .slice(0, 6);
  if (!top.length) return null;
  return (
    <section className="card">
      <h2 className="card-title">Strongest skills</h2>
      <div className="strongest">
        {top.map((n) => (
          <button key={n.id} type="button" className="strong-skill" onClick={() => onSelect(n)}>
            <span className="strong-name"><Icon name="checkCircle" size={16} className="ok" /> {n.name}</span>
            <span className="muted small">{n.kind === "claimed" ? "Verified prior skill" : `${n.points} pts · ${n.tasks.length} task${n.tasks.length === 1 ? "" : "s"}`}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function VerifiedWork({ graph, onSelect }) {
  const [all, setAll] = useState(false);
  const work = useMemo(() => {
    const byTask = new Map(); // one task can prove several skills: list it once, with every skill it proved
    for (const n of graph.nodes) for (const t of n.tasks || []) {
      if (!t.completed_on) continue;
      const key = `${t.title}|${t.completed_on}`;
      if (byTask.has(key)) byTask.get(key).skills.push(n);
      else byTask.set(key, { ...t, key, skills: [n] });
    }
    return [...byTask.values()].sort((a, b) => b.completed_on.localeCompare(a.completed_on));
  }, [graph]);
  if (!work.length) return null;
  const shown = all ? work : work.slice(0, 6);
  return (
    <section className="card">
      <h2 className="card-title">Verified work <span className="count">{work.length}</span></h2>
      <ol className="work">
        {shown.map((t) => (
          <li key={t.key}>
            <span className="work-dot" aria-hidden="true" />
            <div className="work-body">
              <div className="work-title">{t.title}</div>
              <div className="work-meta">
                {t.skills.map((s) => <button key={s.id} type="button" className="link-btn" onClick={() => onSelect(s)}>{s.name}</button>)}
                {t.difficulty && <span className={`diff diff-${t.difficulty}`}>{t.difficulty}</span>}
                {t.tests && <span><Icon name="check" size={13} /> tests {t.tests}</span>}
                {t.review_score != null && <span>review {t.review_score}/100</span>}
                {t.points != null && <span>{t.points} pts</span>}
                <span className="muted">{date(t.completed_on)}</span>
              </div>
            </div>
          </li>
        ))}
      </ol>
      {work.length > 6 && <button type="button" className="btn btn-ghost btn-block" onClick={() => setAll(!all)}>
        {all ? "Show less" : `Show all ${work.length} tasks`}</button>}
    </section>
  );
}

export default function Candidate({ id, query }) {
  const { data: c, error } = useFetch(api(`/api/candidates/${encodeURIComponent(id)}`));
  const [selected, setSelected] = useState(null);
  const [copied, setCopied] = useState(false);

  // ?skill=<id> preselects a skill, and clicking one updates it, so a skill's evidence can be linked directly
  useEffect(() => {
    const want = new URLSearchParams(window.location.search).get("skill");
    setSelected(c && want ? buildGraph(c.learning_graph || c.skill_graph).nodes.find((n) => n.id === want) || null : null);
  }, [c]);
  const select = (node, scroll = false) => {
    setSelected(node);
    const params = new URLSearchParams(window.location.search);
    params.set("skill", node.id);
    window.history.replaceState({}, "", `${window.location.pathname}?${params}`);
    if (scroll) document.getElementById("skills")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const copy = () => {
    navigator.clipboard?.writeText(`${window.location.origin}/c/${encodeURIComponent(id)}`).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    }, () => {});
  };

  const crumbs = (
    <nav className="crumbs" aria-label="Breadcrumb">
      <Link to={query ? `/?q=${encodeURIComponent(query)}` : "/"}><Icon name="back" size={15} /> {query ? `Results for “${query}”` : "Find talent"}</Link>
    </nav>
  );
  if (error) return <div className="wrap">{crumbs}<div className="empty"><h3>{error.message === "Not found" ? "This profile doesn't exist" : "Couldn't load this profile"}</h3>
    <p className="muted">{error.message === "Not found" ? "It may have been made private." : `${error.message} — the service may be waking up.`}</p></div></div>;
  if (!c) return <div className="wrap">{crumbs}<div className="card skeleton profile-skeleton" /></div>;

  const st = c.stats;
  // the same graph the learner sees: earned skills, and those in progress (marked as not yet earned)
  const graph = c.learning_graph || c.skill_graph;
  const contact = c.contact && (c.contact.includes(":") ? c.contact : `mailto:${c.contact}`);
  const facts = [
    ["Points", st.points],
    ["Tasks completed", st.tasks_done],
    ["First-try pass rate", pct(st.first_try_rate)],
    ["Avg. code review", st.avg_code_score == null ? "–" : `${score(st.avg_code_score)}/100`],
    ["Skills earned", st.skills_completed ?? "–"],
    ["Active days", st.active_days],
    ["Longest streak", st.longest_streak != null ? `${st.longest_streak} days` : "–"],
  ];

  return (
    <div className="wrap profile">
      {crumbs}
      <section className="card profile-head">
        <div className="profile-body">
          <Avatar name={c.name || c.id} size="xl" />
          <div className="profile-id">
            <h1>{c.name || c.id}{c.ranking && <TierBadge tier={c.ranking.overall.tier} />}{c.demo && <DemoBadge />}</h1>
            {c.headline && <p className="profile-headline">{c.headline}</p>}
            <div className="profile-meta">
              {c.location && <span><Icon name="pin" size={15} /> {c.location}</span>}
              {c.ranking && <span><Icon name="trophy" size={15} /> #{c.ranking.overall.rank} of {c.ranking.overall.total} globally</span>}
              <span><Icon name="checkCircle" size={15} /> {st.tasks_done} tasks verified</span>
              {st.last_active && <span><Icon name="calendar" size={15} /> Active {date(st.last_active)}</span>}
            </div>
          </div>
          <div className="profile-actions">
            {contact && <a className="btn btn-primary" href={contact}><Icon name="mail" size={17} /> Contact</a>}
            <button type="button" className="btn btn-outline" onClick={copy}><Icon name={copied ? "check" : "link"} size={17} /> {copied ? "Copied" : "Copy link"}</button>
          </div>
        </div>
        {c.interests?.length > 0 && <div className="profile-interests"><span className="muted small">Interests</span>{c.interests.map((i) => <span key={i} className="chip">{i}</span>)}</div>}
      </section>

      <div className="profile-grid">
        <div className="profile-main">
          <SearchMatch id={c.id} query={query} />
          <Strongest graph={c.skill_graph} onSelect={(n) => select(n, true)} />
          <VerifiedWork graph={c.skill_graph} onSelect={(n) => select(n, true)} />
          {st.roadmaps?.length > 0 && (
            <section className="card">
              <h2 className="card-title">Learning now</h2>
              <div className="learning">
                {st.roadmaps.map((r) => (
                  <div key={r.file} className="learning-row">
                    <div><span className="learning-title">{r.skill}</span>
                      <span className="muted small">{r.tasks_done} of {r.tasks_total} tasks</span></div>
                    <span className="bar"><i style={{ width: pct(r.progress) }} /></span>
                    <b>{pct(r.progress)}</b>
                  </div>
                ))}
              </div>
            </section>
          )}
        </div>

        <aside className="profile-side">
          <RankCard ranking={c.ranking} />
          <section className="card">
            <span className="card-kicker">Track record</span>
            <dl className="facts">{facts.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
          </section>
          {contact && (
            <section className="card cta-card">
              <h3>Interested in {(c.name || "this person").split(" ")[0]}?</h3>
              <p className="small">Reach out directly — their work above is what they can do today.</p>
              <a className="btn btn-primary btn-block" href={contact}><Icon name="mail" size={17} /> Contact</a>
            </section>
          )}
        </aside>
      </div>

      <section className="card skills-card" id="skills">
        <div className="section-head">
          <div><h2 className="card-title">Skill graph</h2>
            <p className="section-sub">Click a skill to see the tasks that earned it. Hollow skills are still in progress.</p></div>
        </div>
        <div className="skills-layout">
          <div className="graph-card"><SkillGraph skillGraph={graph} selectedId={selected?.id} onSelect={select} /></div>
          <SkillPanel node={selected} skillGraph={graph} />
        </div>
      </section>
    </div>
  );
}
