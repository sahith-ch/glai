import { useEffect, useMemo, useState } from "react";
import { Link, navigate } from "./router.jsx";
import { useFetch } from "./useFetch.js";
import { api } from "./api.js";
import { Avatar, DemoBadge, Icon, TierBadge, pct, score } from "./ui.jsx";

const POPULAR = ["data analyst", "frontend developer", "backend developer", "sql window functions", "python, pandas"];
const ROLES = [
  { q: "data analyst", title: "Data analyst", blurb: "SQL, spreadsheets, pandas, charts, statistics" },
  { q: "frontend developer", title: "Frontend developer", blurb: "HTML, CSS, JavaScript, React" },
  { q: "backend developer", title: "Backend developer", blurb: "APIs, databases, server code" },
  { q: "data scientist", title: "Data scientist", blurb: "Python, statistics, machine learning" },
];
const SORTS = {
  match: { label: "Best match", key: null },
  rank: { label: "Global rank", key: (r) => r.rank?.rank ?? 1e9 },
  review: { label: "Code review score", key: (r) => -(r.avg_code_score ?? -1) },
  first: { label: "First-try rate", key: (r) => -(r.first_try_rate ?? -1) },
  tasks: { label: "Tasks completed", key: (r) => -r.tasks_done },
};
const TIER_ORDER = ["Master", "Expert", "Specialist", "Apprentice", "Newbie"];

// open the profile with the best-matching skill already selected, so its tasks show right away
function profileLink(r, query) {
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  if (r.evidence?.[0]?.skill_id) params.set("skill", r.evidence[0].skill_id);
  const qs = params.toString();
  return `/c/${encodeURIComponent(r.id)}${qs ? `?${qs}` : ""}`;
}

/** How much of the search a candidate covers, 0-1: a single role by its skills, otherwise by terms matched. */
function coverage(r, terms) {
  if (!terms.length) return null;
  const e = r.evidence[0];
  if (terms.length === 1 && e?.kind === "role") {
    const total = e.covered.length + e.missing.length;
    return { value: total ? e.covered.length / total : 0, label: `${e.covered.length}/${total}`, note: "role skills" };
  }
  return { value: r.matched_terms.length / terms.length, label: `${r.matched_terms.length}/${terms.length}`, note: "matched" };
}

function SearchBox({ initial, big }) {
  const [text, setText] = useState(initial);
  useEffect(() => setText(initial), [initial]);
  const submit = (e) => {
    e.preventDefault();
    navigate(text.trim() ? `/?q=${encodeURIComponent(text.trim())}` : "/");
  };
  return (
    <form onSubmit={submit} className={`searchbox ${big ? "searchbox-big" : ""}`} role="search">
      <Icon name="search" size={big ? 22 : 19} className="searchbox-icon" />
      <input value={text} onChange={(e) => setText(e.target.value)} aria-label="Search by role or skills"
        placeholder="Search a role or skills — data analyst, sql joins, react" autoFocus={big} />
      <button type="submit" className="btn btn-primary">Search</button>
    </form>
  );
}

export default function Search({ query }) {
  return query ? <Results query={query} /> : <Landing />;
}

// ---------------------------------------------------------------- landing

function Landing() {
  const { data, error } = useFetch(api("/api/search?q="));
  const people = data?.results || [];
  const ranked = [...people].sort((a, b) => (a.rank?.rank ?? 1e9) - (b.rank?.rank ?? 1e9));
  const tasks = people.reduce((s, r) => s + r.tasks_done, 0);

  return (
    <>
      <section className="intro">
        <div className="wrap">
          <h1>Find people by the work they've done.</h1>
          <p className="intro-sub">Search by role or skill. Each result shows the finished tasks behind it — the tests
            they passed and how their code was reviewed.</p>
          <SearchBox initial="" big />
          <p className="try">Try{" "}
            {POPULAR.map((q, i) => <span key={q}>{i ? ", " : ""}<Link to={`/?q=${encodeURIComponent(q)}`}>{q}</Link></span>)}
          </p>
        </div>
      </section>

      <div className="wrap home">
        {error && <p className="notice">Couldn't reach the talent service ({error.message}). It may be waking up — try again in a minute.</p>}

        <section className="home-main">
          <div className="list-head">
            <h2>Candidates</h2>
            <span className="muted small">{data ? `${people.length} people · ${tasks} tested tasks` : ""}</span>
          </div>
          <div className="people">
            {!data && !error && [0, 1, 2, 3].map((i) => <div key={i} className="person skeleton" />)}
            {ranked.map((r) => <PersonRow key={r.id} r={r} />)}
          </div>
          <Link to="/leaderboard" className="more">See the full leaderboard →</Link>
        </section>

        <aside className="home-side">
          <section className="side-block">
            <h3>Search by role</h3>
            <ul className="role-list">
              {ROLES.map((r) => (
                <li key={r.q}><Link to={`/?q=${encodeURIComponent(r.q)}`}>{r.title}</Link><span className="muted small">{r.blurb}</span></li>
              ))}
            </ul>
          </section>
          <section className="side-block">
            <h3>How profiles are built</h3>
            <p>Learners work through roadmaps of real tasks — scripts, queries, small apps. Each submission is run
              against tests and its code is reviewed. A skill only appears here once passing work proves it.</p>
            <p className="muted small">Profiles marked Demo are samples, not real people.</p>
          </section>
        </aside>
      </div>
    </>
  );
}

function PersonRow({ r }) {
  return (
    <Link to={`/c/${encodeURIComponent(r.id)}`} className="person">
      <span className="person-rank">{r.rank ? r.rank.rank : "–"}</span>
      <Avatar name={r.name} size="md" />
      <span className="person-who">
        <span className="person-name">{r.name}{r.demo && <DemoBadge />}</span>
        <span className="muted small">{[r.headline, r.location].filter(Boolean).join(" · ")}</span>
        <span className="person-skills">{r.skills.slice(0, 4).join(" · ")}</span>
      </span>
      <span className="person-nums">
        {r.rank && <TierBadge tier={r.rank.tier} />}
        <span className="small"><b>{r.points}</b> pts · {r.tasks_done} tasks</span>
        <span className="muted small">review {score(r.avg_code_score)} · first try {pct(r.first_try_rate)}</span>
      </span>
    </Link>
  );
}

// ---------------------------------------------------------------- results

function Results({ query }) {
  const { data, error, loading } = useFetch(api(`/api/search?q=${encodeURIComponent(query)}`));
  const [sort, setSort] = useState("match");
  const [tiers, setTiers] = useState([]);
  const [places, setPlaces] = useState([]);
  const [fullOnly, setFullOnly] = useState(false);
  useEffect(() => { setTiers([]); setPlaces([]); setFullOnly(false); }, [query]);

  const all = data?.results || [];
  const terms = data?.terms || [];
  const parsed = data?.parsed || [];
  const results = useMemo(() => {
    let list = all.filter((r) =>
      (!tiers.length || tiers.includes(r.rank?.tier)) &&
      (!places.length || places.includes(r.location)) &&
      (!fullOnly || !r.missing_terms.length));
    const key = SORTS[sort].key;
    return key ? [...list].sort((a, b) => key(a) - key(b)) : list;
  }, [all, sort, tiers, places, fullOnly]);

  const count = (pick) => all.reduce((m, r) => { const k = pick(r); if (k) m[k] = (m[k] || 0) + 1; return m; }, {});
  const tierCounts = count((r) => r.rank?.tier);
  const placeCounts = count((r) => r.location);
  const toggle = (list, set, v) => set(list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
  const filtered = tiers.length || places.length || fullOnly;

  return (
    <>
      <section className="searchbar">
        <div className="wrap"><SearchBox initial={query} /></div>
      </section>

      <div className="wrap results-layout">
        <aside className="filters" aria-label="Filters">
          <div className="filters-head"><Icon name="filter" size={16} /> Filters
            {filtered && <button type="button" className="link-btn" onClick={() => { setTiers([]); setPlaces([]); setFullOnly(false); }}>Clear</button>}
          </div>
          {terms.length > 1 && (
            <label className="check"><input type="checkbox" checked={fullOnly} onChange={(e) => setFullOnly(e.target.checked)} />
              Matches every term</label>
          )}
          <fieldset>
            <legend>Tier</legend>
            {TIER_ORDER.filter((t) => tierCounts[t]).map((t) => (
              <label key={t} className="check"><input type="checkbox" checked={tiers.includes(t)} onChange={() => toggle(tiers, setTiers, t)} />
                <TierBadge tier={t} /><span className="check-count">{tierCounts[t]}</span></label>
            ))}
            {!Object.keys(tierCounts).length && <p className="muted small">—</p>}
          </fieldset>
          <fieldset>
            <legend>Location</legend>
            {Object.keys(placeCounts).sort().map((p) => (
              <label key={p} className="check"><input type="checkbox" checked={places.includes(p)} onChange={() => toggle(places, setPlaces, p)} />
                {p}<span className="check-count">{placeCounts[p]}</span></label>
            ))}
            {!Object.keys(placeCounts).length && <p className="muted small">—</p>}
          </fieldset>
          <p className="filters-note"><Icon name="checkCircle" size={15} /> Matches count only skills shown in tested, finished work.</p>
        </aside>

        <section className="results-main" aria-live="polite">
          <div className="results-head">
            <div>
              <h1 className="results-title">{data ? `${results.length} ${results.length === 1 ? "candidate" : "candidates"}` : "Searching…"}
                <span className="muted"> for “{query}”</span></h1>
              {parsed.length > 0 && (
                <div className="understood">
                  <span className="muted small">Looking for</span>
                  {parsed.map((t) => (
                    <span key={t.text} className={`term term-${t.kind}`} title={t.kind === "role" ? `Skills: ${t.skills.join(", ")}` : undefined}>
                      {t.text}{t.kind === "role" && <span className="term-note">role · {t.skills.length} skills</span>}
                    </span>
                  ))}
                </div>
              )}
            </div>
            <label className="sort">
              <span className="muted small">Sort by</span>
              <select value={sort} onChange={(e) => setSort(e.target.value)}>
                {Object.entries(SORTS).map(([k, s]) => <option key={k} value={k}>{s.label}</option>)}
              </select>
            </label>
          </div>

          {error && <p className="notice">Couldn't load candidates ({error.message}). The service may be waking up — try again in a minute.</p>}
          {loading && !data && <div className="result-list">{[0, 1, 2].map((i) => <div key={i} className="result skeleton" />)}</div>}

          <div className="result-list">
            {results.map((r) => <ResultCard key={r.id} r={r} query={query} terms={terms} />)}
          </div>

          {data && !results.length && (
            <div className="empty">
              <span className="empty-icon"><Icon name="search" size={26} /></span>
              <h3>{all.length ? "No one matches these filters" : "No one has proven this yet"}</h3>
              <p className="muted">{all.length ? "Clear a filter to see more people."
                : `Nobody has finished tested work matching “${query}”. Try a broader skill, a role, or one term at a time.`}</p>
              <div className="chips center">{POPULAR.slice(0, 3).map((q) => <Link key={q} to={`/?q=${encodeURIComponent(q)}`} className="chip">{q}</Link>)}</div>
            </div>
          )}
        </section>
      </div>
    </>
  );
}

function ResultCard({ r, query, terms }) {
  const cov = coverage(r, terms);
  const href = profileLink(r, query);
  return (
    <article className="result">
      <div className="result-head">
        <Avatar name={r.name} size="lg" />
        <div className="who">
          <Link to={href} className="result-name">{r.name}</Link>
          {r.rank && <TierBadge tier={r.rank.tier} />}{r.demo && <DemoBadge />}
          <div className="result-headline">{r.headline || "Learner"}</div>
          <div className="result-meta">
            {r.location && <span><Icon name="pin" size={14} /> {r.location}</span>}
            {r.rank && <span><Icon name="trophy" size={14} /> #{r.rank.rank} of {r.rank.total} globally</span>}
            <span><Icon name="checkCircle" size={14} /> {r.tasks_done} tasks verified</span>
          </div>
        </div>
        {cov && <div className="result-fit"><b>{cov.label}</b><span className="muted small">{cov.note}</span></div>}
      </div>

      {r.evidence.length > 0 && (
        <div className="why">
          <span className="why-title">Why they match</span>
          {r.evidence.map((e) => <Why key={e.term} e={e} />)}
          {r.missing_terms.map((t) => (
            <div key={t} className="why-row missing"><span className="why-term">{t}</span><span className="small">Not shown in their work yet</span></div>
          ))}
        </div>
      )}

      <div className="result-foot">
        <dl className="kpis">
          <div><dt>Points</dt><dd>{r.points}</dd></div>
          <div><dt>First try</dt><dd>{pct(r.first_try_rate)}</dd></div>
          <div><dt>Code review</dt><dd>{score(r.avg_code_score)}<small>/100</small></dd></div>
          <div><dt>Skills</dt><dd>{r.skills_acquired ?? r.skills.length}</dd></div>
        </dl>
        <Link to={href} className="btn btn-outline">View profile <Icon name="arrow" size={16} /></Link>
      </div>
    </article>
  );
}

function Why({ e }) {
  if (e.kind === "role") {
    const total = e.covered.length + e.missing.length;
    return (
      <div className="why-row">
        <span className="why-term">{e.term}</span>
        <div className="why-body">
          <div className="coverage"><span className="bar"><i style={{ width: `${(e.covered.length / total) * 100}%` }} /></span>
            <span className="small"><b>{e.covered.length}</b> of {total} skills</span></div>
          <div className="chips">
            {e.covered.map((s) => <span key={s} className="chip chip-ok"><Icon name="check" size={13} /> {s}</span>)}
            {e.missing.map((s) => <span key={s} className="chip chip-dim">{s}</span>)}
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="why-row">
      <span className="why-term">{e.term}</span>
      <div className="why-body">
        <span className="why-skill"><Icon name="checkCircle" size={15} className="ok" /> <b>{e.skill}</b>
          {e.how === "semantic" && <span className="chip chip-info">matched by meaning</span>}</span>
        {e.proof && (
          <span className="muted small proof">
            {e.task ? "Proved by" : "Shown by"} “{e.proof.title}”
            {e.proof.tests && <> · tests {e.proof.tests}</>}
            {e.proof.review_score != null && <> · review {e.proof.review_score}/100</>}
          </span>
        )}
      </div>
    </div>
  );
}
