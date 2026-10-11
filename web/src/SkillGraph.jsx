import { useMemo, useState } from "react";
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY } from "d3-force";

// Shared by the hiring site and the learner dashboard (copied to learner-web/src), and drawn the same way as
// the desktop app's graph: earned skills filled, in-progress skills hollow with an arc for how much is done.

const heightFor = (n) => Math.min(820, Math.max(460, n * 26)); // more skills get more room
const MIN_VIEW = [720, 420]; // small graphs keep normal scale instead of zooming until labels collide

// Skills from the profile, plus a hollow "area" node per parent skill that isn't on the graph, so its
// sub-skills form one cluster instead of floating loose. Areas are grouping only, not claims.
export function buildGraph(skillGraph) {
  const nodes = skillGraph.nodes.map((n) => ({ ...n }));
  const ids = new Set(nodes.map((n) => n.id));
  const byName = new Map(nodes.map((n) => [n.name, n]));
  const links = skillGraph.edges.filter((e) => ids.has(e.src) && ids.has(e.dst)).map((e) => ({ source: e.src, target: e.dst, kind: e.kind }));

  for (const n of skillGraph.nodes) {
    if (n.kind !== "subskill" || !n.parent) continue;
    let parent = byName.get(n.parent);
    if (!parent) {
      parent = { id: `area:${n.parent}`, name: n.parent, kind: "area", tasks: [], points: 0 };
      nodes.push(parent);
      byName.set(n.parent, parent);
    }
    if (!links.some((l) => l.source === n.id && l.target === parent.id)) {
      links.push({ source: n.id, target: parent.id, kind: "part_of" });
    }
  }
  return { nodes, links };
}

export const inProgress = (n) => n.kind !== "area" && n.kind !== "claimed" && n.status === "in_progress";

function radius(n) {
  if (n.kind === "skill") return 11;
  if (n.kind === "area") return 9;
  if (n.kind === "claimed") return 7;
  return 5 + Math.min(4, Math.sqrt(n.points || 0) / 3); // sub-skills grow a little with points earned
}

const labelOf = (n) => `${n.name}${n.kind === "area" ? " (in progress)" : ""}`;
const labelWidth = (n) => labelOf(n).length * 6.6; // ~12px system font

function layout(graph) {
  const nodes = graph.nodes.map((n) => ({ ...n }));
  const links = graph.links.map((l) => ({ ...l }));
  const linked = new Set(links.flatMap((l) => [l.source, l.target]));
  const crowd = Math.sqrt(12 / Math.max(12, nodes.length)); // big graphs: less repulsion, so labels stay readable
  forceSimulation(nodes)
    // labels sit to the right of each dot, so a long label needs its neighbours further away
    .force("link", forceLink(links).id((d) => d.id)
      .distance((l) => (l.kind === "part_of" ? Math.max(80, labelWidth(l.source) * 0.6 + 30) : 130)).strength(0.8))
    .force("charge", forceManyBody().strength(-420 * crowd))
    .force("collide", forceCollide((d) => radius(d) + 16 + Math.min(labelWidth(d) / 2, 120)))
    // unlinked skills (e.g. prior knowledge) would drift to the edges and shrink everything: pull them in
    .force("x", forceX(0).strength((d) => (linked.has(d.id) ? 0.05 : 0.18)))
    .force("y", forceY(0).strength((d) => (linked.has(d.id) ? 0.09 : 0.25)))
    .force("center", forceCenter(0, 0))
    .stop()
    .tick(500); // static layout: computed once, no animation
  // fit the view to dots AND their labels, so nothing is clipped
  const minX = Math.min(...nodes.map((n) => n.x - radius(n))) - 16;
  const maxX = Math.max(...nodes.map((n) => n.x + radius(n) + 6 + labelWidth(n))) + 16;
  const minY = Math.min(...nodes.map((n) => n.y - radius(n))) - 20;
  const maxY = Math.max(...nodes.map((n) => n.y + radius(n))) + 20;
  const width = Math.max(MIN_VIEW[0], maxX - minX);
  const height = Math.max(MIN_VIEW[1], maxY - minY);
  return { nodes, links, box: [minX - (width - (maxX - minX)) / 2, minY - (height - (maxY - minY)) / 2, width, height] };
}

export default function SkillGraph({ skillGraph, selectedId, onSelect }) {
  const { nodes, links, box } = useMemo(() => layout(buildGraph(skillGraph)), [skillGraph]);
  const [hover, setHover] = useState(null);

  const focus = hover || selectedId;
  const near = useMemo(() => {
    if (!focus) return null;
    const s = new Set([focus]);
    links.forEach((l) => {
      if (l.source.id === focus) s.add(l.target.id);
      if (l.target.id === focus) s.add(l.source.id);
    });
    return s;
  }, [focus, links]);

  if (!nodes.length) return <p className="muted">No skills yet.</p>;

  return (
    <svg className="graph" viewBox={box.join(" ")} height={heightFor(nodes.length)} role="group" aria-label="Skill network">
      <defs>
        <marker id="arrow" viewBox="0 0 8 8" refX="8" refY="4" markerWidth="6" markerHeight="6" orient="auto">
          <path d="M0,0 L8,4 L0,8 z" className="arrowhead" />
        </marker>
      </defs>
      {links.map((l, i) => {
        const dim = near && !(near.has(l.source.id) && near.has(l.target.id));
        const dx = l.target.x - l.source.x, dy = l.target.y - l.source.y, len = Math.hypot(dx, dy) || 1;
        const r = radius(l.target) + 3;
        return (
          <line
            key={i}
            x1={l.source.x} y1={l.source.y}
            x2={l.target.x - (dx / len) * r} y2={l.target.y - (dy / len) * r}
            className={`edge ${l.kind} ${dim ? "dim" : ""}`}
            markerEnd={l.kind === "prerequisite" ? "url(#arrow)" : undefined}
          />
        );
      })}
      {nodes.map((n) => {
        const dim = near && !near.has(n.id);
        const label = labelOf(n);
        const progress = inProgress(n);
        return (
          <g
            key={n.id}
            transform={`translate(${n.x},${n.y})`}
            className={`node ${n.kind} ${progress ? "progress" : ""} ${n.id === selectedId ? "selected" : ""} ${dim ? "dim" : ""}`}
            tabIndex={0}
            role="button"
            aria-label={progress ? `${label}: in progress, ${n.tasks_done} of ${n.tasks_total} tasks done` : `${label}: ${n.tasks?.length || 0} tasks`}
            aria-pressed={n.id === selectedId}
            onClick={() => onSelect(n)}
            onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), onSelect(n))}
            onMouseEnter={() => setHover(n.id)}
            onMouseLeave={() => setHover(null)}
          >
            <circle r={radius(n) + 10} className="hit" />
            <circle r={radius(n)} />
            {progress && n.tasks_total > 0 && (
              // how much of the skill is done, as an arc around the hollow node
              <circle r={radius(n)} className="done" pathLength={100}
                strokeDasharray={`${Math.round((n.tasks_done / n.tasks_total) * 100)} 100`} transform="rotate(-90)" />
            )}
            <text x={radius(n) + 6} y={4}>{label}</text>
          </g>
        );
      })}
    </svg>
  );
}
