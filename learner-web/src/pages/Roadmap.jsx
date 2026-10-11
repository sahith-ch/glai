import { fmtDay } from "../format.js";
import { PageHead } from "../components/RoadmapHeader.jsx";
import { Milestone } from "../components/Tasks.jsx";

export default function Roadmap({ r }) {
  return (
    <>
      <PageHead eyebrow="Roadmap" title={r.skill}>
        <div className="meta">
          <span>{r.milestones.length} milestones</span>
          <span>Started {fmtDay(r.profile.start_date)}</span>
          <span>Planned finish {fmtDay(r.projection.planned_end)}</span>
          {r.prerequisites?.length > 0 && <span>Prerequisites: {r.prerequisites.join(", ")}</span>}
        </div>
      </PageHead>
      <div className="milestones">{r.milestones.map((m) => <Milestone key={m.index} m={m} />)}</div>
    </>
  );
}
