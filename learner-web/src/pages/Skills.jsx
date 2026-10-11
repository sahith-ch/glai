import { useState } from "react";
import { PageHead } from "../components/RoadmapHeader.jsx";
import SkillGraph from "../SkillGraph.jsx";
import SkillPanel from "../SkillPanel.jsx";

export default function Skills({ me }) {
  const [selected, setSelected] = useState(null);
  const graph = me ? (me.learning_graph || me.skill_graph) : null; // earned skills and those in progress
  const nodes = graph?.nodes || [];
  const earned = (kind) => nodes.filter((n) => n.kind === kind && n.status !== "in_progress").length;
  const sub = earned("subskill");
  const whole = earned("skill");
  const verified = nodes.filter((n) => n.kind === "claimed").length;
  const going = nodes.filter((n) => n.status === "in_progress").length;
  return (
    <>
      <PageHead eyebrow="Skills" title="What you've earned">
        <p className="lede">Skills you've earned, and the ones you're part-way through. Click one to see the tasks behind it.</p>
        <div className="meta">
          <span>{sub} sub-skills earned</span>
          <span>{whole} complete skill areas</span>
          <span>{going} in progress</span>
          <span>{verified} verified prior skills</span>
        </div>
      </PageHead>
      {nodes.length ? (
        <div className="skills-layout">
          <div className="graph-card">
            <SkillGraph skillGraph={graph} selectedId={selected?.id} onSelect={setSelected} />
          </div>
          <SkillPanel node={selected} skillGraph={graph} who="you" />
        </div>
      ) : (
        <p className="muted">No skills acquired yet. A sub-skill is earned when every one of its tasks is done.</p>
      )}
    </>
  );
}
