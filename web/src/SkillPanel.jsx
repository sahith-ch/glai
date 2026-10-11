// Side panel for the skill network: the tasks solved to earn the selected skill.

function tasksFor(node, skillGraph) {
  if (node.kind !== "area") return node.tasks || [];
  // an unfinished area: show the tasks behind the sub-skills acquired in it
  const seen = new Set();
  return skillGraph.nodes
    .filter((n) => n.parent === node.name)
    .flatMap((n) => n.tasks || [])
    .filter((t) => !seen.has(t.roadmap + t.id) && seen.add(t.roadmap + t.id));
}

export default function SkillPanel({ node, skillGraph, who = "this person" }) {
  if (!node) {
    return (
      <aside className="panel">
        <p className="muted">Click a skill to see the tasks {who} solved to earn it.</p>
        <p className="muted small legend">
          <span><i className="dot filled" /> acquired</span>
          <span><i className="dot ring" /> verified prior knowledge</span>
          <span><i className="dot half" /> in progress — not yet earned (the arc shows how much is done)</span>
          <span><i className="dot dashed" /> area in progress</span>
        </p>
      </aside>
    );
  }
  const tasks = tasksFor(node, skillGraph);
  const status =
    node.kind === "area" ? "In progress — sub-skills acquired so far"
    : node.status === "in_progress" ? `In progress, not yet earned — ${node.tasks_done} of ${node.tasks_total} tasks done · ${node.points} pts so far`
    : node.kind === "claimed" ? `Verified by the skills check${node.acquired_on ? ` on ${node.acquired_on}` : ""}`
    : `Acquired${node.acquired_on ? ` ${node.acquired_on}` : ""} · ${node.points} pts`;

  return (
    <aside className="panel">
      <h3>{node.name}</h3>
      <p className="status muted small">{status}</p>
      {!tasks.length && <p className="muted small">No task details recorded.</p>}
      <ol className="tasks">
        {tasks.map((t) => (
          <li key={t.roadmap + t.id}>
            <div className="task-title">{t.title}</div>
            <div className="muted small">
              {[t.difficulty, `${t.points} pts`, t.completed_on, t.kind !== "core" ? "catch-up task" : null].filter(Boolean).join(" · ")}
            </div>
            <p className="small">{t.description}</p>
            <p className="small"><span className="muted">Deliverable:</span> {t.deliverable}</p>
            {(t.tests || t.review_score != null || t.source) && (
              <div className="badges">
                {t.tests && <span className="tag">Tests {t.tests}</span>}
                {t.review_score != null && <span className="tag">Review {t.review_score}/100</span>}
                {t.source && <a href={t.source} target="_blank" rel="noreferrer">View code ↗</a>}
              </div>
            )}
          </li>
        ))}
      </ol>
    </aside>
  );
}
