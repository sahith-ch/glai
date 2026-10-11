import { useEffect, useState } from "react";
import { Link, navigate, useLocation } from "./router.jsx";
import { useFetch } from "./useFetch.js";
import { AppContext } from "./appContext.js";
import Today from "./pages/Today.jsx";
import Arena from "./pages/Arena.jsx";
import Overview from "./pages/Overview.jsx";
import Roadmap from "./pages/Roadmap.jsx";
import Skills from "./pages/Skills.jsx";
import Stats from "./pages/Stats.jsx";
import NewRoadmap from "./pages/NewRoadmap.jsx";
import Login from "./pages/Login.jsx";
import Profile from "./pages/Profile.jsx";
import { authEnabled, signOut, takeSessionFromUrl, useSession } from "./auth.js";

const ROADMAP_NAV = [
  ["/overview", "Overview"],
  ["/roadmap", "Roadmap"],
];
const GLOBAL_NAV = [
  ["/skills", "Skills"],
  ["/stats", "Stats"],
  ...(authEnabled ? [["/profile", "Profile"]] : []),
];

// remember the chosen roadmap per browser (falls back gracefully if storage is blocked)
const store = {
  get: () => { try { return localStorage.getItem("glai.roadmap"); } catch { return null; } },
  set: (v) => { try { localStorage.setItem("glai.roadmap", v); } catch { /* ignore */ } },
};

function Sidebar({ path, roadmaps, current, choose, me }) {
  return (
    <aside className="sidebar">
      <div className="logo">GLAI <span className="muted">Learn</span></div>

      <nav>
        <Link to="/" className={`nav-item ${path === "/" ? "active" : ""}`}>
          Today <span className="nav-note">all skills</span>
        </Link>
        <Link to="/arena" className={`nav-item ${path === "/arena" ? "active" : ""}`}>
          Arena <span className="nav-note">practice anytime</span>
        </Link>
      </nav>

      {roadmaps.length > 0 && (
        <div className="nav-group">
          <label className="switcher">
            <span className="muted small">This roadmap</span>
            <select value={current || ""} onChange={(e) => choose(e.target.value)}>
              {roadmaps.map((r) => <option key={r.file} value={r.file}>{r.skill}</option>)}
            </select>
          </label>
          <nav>
            {ROADMAP_NAV.map(([to, label]) => (
              <Link key={to} to={to} className={`nav-item ${path === to ? "active" : ""}`}>{label}</Link>
            ))}
          </nav>
        </div>
      )}

      <nav>
        {GLOBAL_NAV.map(([to, label]) => (
          <Link key={to} to={to} className={`nav-item ${path === to ? "active" : ""}`}>{label}</Link>
        ))}
      </nav>

      <Link to="/new" className={`new-btn ${path === "/new" ? "active" : ""}`}>New roadmap</Link>

      <div className="me">
        <span className="avatar">{(me?.name || "You").slice(0, 1).toUpperCase()}</span>
        <span>
          <b>{me?.name || "You"}</b>
          <span className="muted small">{me ? `${me.stats.points} pts · ${me.stats.current_streak} day streak` : "…"}</span>
        </span>
      </div>
      {authEnabled && <button type="button" className="text-btn signout" onClick={signOut}>Sign out</button>}
    </aside>
  );
}

// Deployed, the app needs a signed-in learner; locally (no Supabase configured) it opens straight away.
export default function App() {
  const session = useSession();
  const [checked, setChecked] = useState(!authEnabled);
  useEffect(() => { if (authEnabled) takeSessionFromUrl().finally(() => setChecked(true)); }, []);
  if (!checked) return null;
  if (authEnabled && !session) return <Login />;
  return <Learner key={session?.user?.id || "local"} />;
}

function Learner() {
  const { path, search } = useLocation();
  const [refreshKey, setRefreshKey] = useState(0);
  const { data: list, error } = useFetch("/api/roadmaps", refreshKey);
  const roadmaps = list?.roadmaps || [];
  const [current, setCurrent] = useState(() => new URLSearchParams(search).get("roadmap") || store.get());

  const choose = (file) => {
    setCurrent(file);
    store.set(file);
  };
  useEffect(() => {
    if (roadmaps.length && !roadmaps.some((r) => r.file === current)) choose(roadmaps[0].file);
  }, [roadmaps, current]);

  const valid = roadmaps.some((r) => r.file === current);
  const { data: r } = useFetch(valid ? `/api/roadmaps/${encodeURIComponent(current)}` : null, refreshKey);
  const { data: me } = useFetch("/api/me", refreshKey);

  const onCreated = (file) => {
    choose(file);
    setRefreshKey((k) => k + 1);
    setTimeout(() => navigate("/overview"), 1200); // let the "ready" state show for a moment
  };

  let page;
  if (authEnabled && me && !me.name) page = <Profile setup onSaved={() => setRefreshKey((k) => k + 1)} />;
  else if (path === "/profile" && authEnabled) page = <Profile onSaved={() => setRefreshKey((k) => k + 1)} />;
  else if (path === "/new") page = <NewRoadmap onCreated={onCreated} />;
  else if (path === "/stats") page = <Stats me={me} refreshKey={refreshKey} />;
  else if (path === "/skills") page = <Skills me={me} />;
  else if (path === "/arena") page = <Arena refreshKey={refreshKey} />;
  else if (path === "/" && roadmaps.length) page = <Today refreshKey={refreshKey} choose={choose} />;
  else if (error) page = authEnabled
    ? <p className="empty">Couldn't reach GLAI ({error.message}). The server may be waking up — refresh in a minute.</p>
    : <p className="empty">Couldn't reach the learner server. Run <code>python main.py web</code>.</p>;
  else if (list && !roadmaps.length) {
    page = (
      <div className="empty">
        <h1>Start your first roadmap</h1>
        <p className="lede">Tell us what you want to learn and how much time you have.</p>
        <Link to="/new" className="button primary">New roadmap</Link>
      </div>
    );
  } else if (!r) page = <p className="muted">Loading…</p>;
  else if (path === "/roadmap") page = <Roadmap r={r} />;
  else if (path === "/overview") page = <Overview r={r} me={me} />;
  else page = <Today refreshKey={refreshKey} choose={choose} />;

  const ctx = { roadmap: current, refresh: () => setRefreshKey((k) => k + 1) };
  return (
    <AppContext.Provider value={ctx}>
      <div className="shell">
        <Sidebar path={path} roadmaps={roadmaps} current={current} choose={choose} me={me} />
        <main className="content">{page}</main>
      </div>
    </AppContext.Provider>
  );
}
