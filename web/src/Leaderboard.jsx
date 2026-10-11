import { Link, navigate } from "./router.jsx";
import { useFetch } from "./useFetch.js";
import { api } from "./api.js";
import { Avatar, DemoBadge, Icon, TierBadge, pct, score, topText } from "./ui.jsx";

export { TierBadge, topText }; // older imports

export default function Leaderboard({ area }) {
  const { data, error } = useFetch(api(`/api/leaderboard${area ? `?area=${encodeURIComponent(area)}` : ""}`));
  const pick = (a) => navigate(a ? `/leaderboard?area=${encodeURIComponent(a)}` : "/leaderboard");
  const entries = data?.entries || [];
  const podium = []; // one plain table reads better than a podium
  const rest = podium.length ? entries.slice(3) : entries;
  const pointsLabel = area ? "Area points" : "Points";

  return (
    <>
      <section className="intro intro-compact">
        <div className="wrap">
          <h1>{area ? `Leaderboard: ${area}` : "Leaderboard"}</h1>
          <p className="intro-sub">Ranked by points from finished, tested work. Harder tasks earn more; failed attempts and
            weak code reviews earn less.</p>
        </div>
      </section>

      <div className="wrap">
        <div className="tabs" role="tablist" aria-label="Ranking area">
          <button type="button" role="tab" aria-selected={!area} className={!area ? "on" : ""} onClick={() => pick(null)}>Overall</button>
          {(data?.areas || []).map((a) => (
            <button key={a} type="button" role="tab" aria-selected={area === a} className={area === a ? "on" : ""} onClick={() => pick(a)}>{a}</button>
          ))}
        </div>

        {error && <p className="notice">Couldn't load the leaderboard ({error.message}). The service may be waking up — try again in a minute.</p>}
        {!data && !error && <div className="podium">{[0, 1, 2].map((i) => <div key={i} className="podium-card skeleton" />)}</div>}
        {data && !entries.length && <div className="empty"><h3>No one ranked here yet</h3><p className="muted">Nobody has finished work in this area.</p></div>}

        {podium.length > 0 && (
          <div className="podium">
            {[podium[1], podium[0], podium[2]].map((e) => (
              <Link key={e.id} to={`/c/${encodeURIComponent(e.id)}`} className={`podium-card place-${e.rank}`}>
                <span className="medal">{e.rank}</span>
                <Avatar name={e.name} size="lg" />
                <span className="podium-name">{e.name}{e.demo && <DemoBadge />}</span>
                <span className="podium-headline">{e.headline || "Learner"}</span>
                <TierBadge tier={e.tier} />
                <span className="podium-points"><b>{e.points}</b> {pointsLabel.toLowerCase()}</span>
                <span className="muted small">{topText({ total: data.total, top_percent: e.top_percent })} · {e.tasks_done} tasks</span>
              </Link>
            ))}
          </div>
        )}

        {rest.length > 0 && (
          <div className="table-card" role="table" aria-label={area ? `Ranking in ${area}` : "Overall ranking"}>
            <div className="board-row board-head" role="row">
              <span role="columnheader">Rank</span><span role="columnheader">Candidate</span>
              <span role="columnheader">{pointsLabel}</span><span role="columnheader">Tasks</span>
              <span role="columnheader">First try</span><span role="columnheader">Review</span>
            </div>
            {rest.map((e) => (
              <Link key={e.id} to={`/c/${encodeURIComponent(e.id)}`} className={`board-row ${e.rank <= 3 ? "top" : ""}`} role="row">
                <span className="board-rank" role="cell">#{e.rank}<small>{topText({ total: data.total, top_percent: e.top_percent })}</small></span>
                <span className="board-who" role="cell">
                  <Avatar name={e.name} size="sm" />
                  <span>
                    <span className="board-name"><b>{e.name}</b><TierBadge tier={e.tier} />{e.demo && <DemoBadge />}</span>
                    <span className="muted small">{[e.headline, e.location].filter(Boolean).join(" · ")}</span>
                  </span>
                </span>
                <span className="board-num" role="cell">{e.points}</span>
                <span role="cell">{e.tasks_done}</span>
                <span role="cell">{pct(e.first_try_rate)}</span>
                <span role="cell">{score(e.avg_code_score)}</span>
              </Link>
            ))}
          </div>
        )}

        {data && (
          <div className="tiers-legend">
            <span className="muted small">Tiers by points:</span>
            {data.tiers.map((t) => <span key={t.tier} className="tier-step"><TierBadge tier={t.tier} /><span className="muted small">{t.min_points}+</span></span>)}
          </div>
        )}
      </div>
    </>
  );
}
