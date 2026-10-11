import { fmtShort, pct } from "../format.js";

export function PageHead({ eyebrow, title, children }) {
  return (
    <header className="page-head">
      {eyebrow && <p className="eyebrow">{eyebrow}</p>}
      <h1>{title}</h1>
      {children}
    </header>
  );
}

export function RoadmapTiles({ r, me }) {
  const p = r.projection;
  const total = p.done_hours + p.left_hours;
  const tiles = [
    ["Progress", total ? pct(p.done_hours / total) : "0%", "accent"],
    ["Hours", `${Math.round(p.done_hours)} / ${Math.round(total)}`],
    ["Finishing", fmtShort(p.projected_end)],
    ["Schedule", p.slip_days > 0 ? `${p.slip_days}d behind` : "On track"],
    ["Points", me ? me.stats.points : "–"],
  ];
  return (
    <div className="tiles">
      {tiles.map(([k, v, cls]) => (
        <div key={k} className={`tile ${cls || ""}`}><b>{v}</b><span>{k}</span></div>
      ))}
    </div>
  );
}
