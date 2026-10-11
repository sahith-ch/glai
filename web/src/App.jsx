import { useEffect } from "react";
import { Link, useLocation } from "./router.jsx";
import Search from "./Search.jsx";
import Candidate from "./Candidate.jsx";
import Leaderboard from "./Leaderboard.jsx";

export default function App() {
  const { path, search } = useLocation();
  const params = new URLSearchParams(search);
  const query = params.get("q") || "";
  const candidate = path.match(/^\/c\/(.+)$/);
  const page = candidate ? "candidate" : path === "/leaderboard" ? "leaderboard" : "search";

  useEffect(() => window.scrollTo(0, 0), [path]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="wrap topbar-inner">
          <Link to="/" className="brand" aria-label="GLAI Hire home">glai<span>hire</span></Link>
          <nav className="topnav" aria-label="Main">
            <Link to="/" className={page !== "leaderboard" ? "on" : ""}>Find talent</Link>
            <Link to="/leaderboard" className={page === "leaderboard" ? "on" : ""}>Leaderboard</Link>
          </nav>
        </div>
      </header>

      <main>
        {page === "candidate" ? <Candidate id={decodeURIComponent(candidate[1])} query={query} />
          : page === "leaderboard" ? <Leaderboard area={params.get("area")} />
          : <Search query={query} />}
      </main>

      <footer className="footer">
        <div className="wrap footer-inner">
          <div>
            <Link to="/" className="brand">glai<span>hire</span></Link>
            <p>Profiles are built from the work people finish in GLAI: every task is tested, and the code is reviewed.
              No self-reported skills, no résumé keywords.</p>
          </div>
          <div className="footer-links">
            <span className="footer-title">Talent</span>
            <Link to="/">Find talent</Link>
            <Link to="/leaderboard">Leaderboard</Link>
          </div>
          <div className="footer-links">
            <span className="footer-title">Popular roles</span>
            <Link to="/?q=data%20analyst">Data analysts</Link>
            <Link to="/?q=frontend%20developer">Frontend developers</Link>
            <Link to="/?q=backend%20developer">Backend developers</Link>
          </div>
        </div>
        <div className="wrap footer-base">© {new Date().getFullYear()} GLAI · Demo profiles are labelled and are not real people.</div>
      </footer>
    </div>
  );
}
