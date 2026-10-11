"""The database-backed folder (supa.Folder): GLAI's modules run unchanged on a learner's rows. Offline: a fake
Supabase keeps rows in memory and applies the same owner filter row-level security would."""

import json
from datetime import date

from roadmap_agent import progress, supa, user
from roadmap_agent.schemas import Milestone, Plan, Roadmap, Task


class FakeSupabase:
    def __init__(self):
        self.rows = {"documents": [], "profiles": []}
        self.token = "t"

    def select(self, table, params):
        rows = self.rows[table]
        for k, v in params.items():
            if k in ("select", "order", "limit"):
                continue
            want = v.split(".", 1)[1]
            rows = [r for r in rows if str(r.get(k)).lower() == want.lower()]
        return [dict(r, updated_at="2026-10-12T10:00:00+00:00") for r in rows]

    def upsert(self, table, row, on_conflict):
        keys = on_conflict.split(",")
        self.rows[table] = [r for r in self.rows[table] if any(r.get(k) != row.get(k) for k in keys)] + [dict(row)]

    def delete(self, table, params):
        drop = self.select(table, params)
        self.rows[table] = [r for r in self.rows[table]
                            if not any(all(r.get(k) == d.get(k) for k in ("owner", "name")) for d in drop)]


db = FakeSupabase()
me = supa.Folder(db, "user-a")
other = supa.Folder(db, "user-b")

# --- path shape, as pathlib
doc = me / "leveller" / "leveller.json"
assert (doc.name, doc.stem, doc.suffix, doc.parent.key) == ("leveller.json", "leveller", ".json", "leveller")
assert (me / "roadmap_sql.json").with_suffix(".md").name == "roadmap_sql.md"
assert not doc.exists() and me.exists()

# --- reading and writing documents; the owner is always this learner
doc.write_text(json.dumps({"v": 1}))
assert json.loads(doc.read_text()) == {"v": 1} and doc.exists() and (me / "leveller").is_dir()
assert db.rows["documents"][0]["owner"] == "user-a"
assert not (other / "leveller" / "leveller.json").exists(), "another learner's folder doesn't see it"
doc.unlink()
assert not doc.exists()
try:
    doc.unlink()
    raise AssertionError("unlinking a missing document must fail like pathlib")
except FileNotFoundError:
    pass
doc.unlink(missing_ok=True)

# --- roadmaps: glob, sort and the whole progress/user pipeline
plan = Plan(profile={"skill": "SQL", "weekly_hours": 5, "days_per_week": 5, "start_date": "2026-10-12"},
            roadmap=Roadmap(skill="SQL", summary="", total_weeks=1, milestones=[Milestone(
                title="Basics", objective="o", week_start=1, week_end=1, success_criteria=[],
                tasks=[Task(id="1.1", title="Select", description="d", estimated_hours=1, deliverable="q.sql", topic="Selects"),
                       Task(id="1.2", title="Join", description="d", estimated_hours=1, deliverable="j.sql", topic="Joins")])]))
for name in ("roadmap_sql.json", "roadmap_py.json"):
    progress.save(plan, me / name)
assert [p.name for p in me.glob("roadmap_*.json")] == ["roadmap_py.json", "roadmap_sql.json"]
assert sorted(me.glob("roadmap_*.json")) == me.glob("roadmap_*.json")
progress.mark_done(plan, "1.1", date(2026, 10, 12))
progress.save(plan, me / "roadmap_sql.json")

# --- the profile is its own row, with the in-progress graph computed from the learner's roadmaps
profile = user.rebuild(me, date(2026, 10, 12))
assert profile.stats.tasks_done == 1
row = next(r for r in db.rows["profiles"] if r["id"] == "user-a")
assert row["profile"]["stats"]["tasks_done"] == 1
assert [n["name"] for n in row["learning_graph"]["nodes"] if n["status"] == "in_progress"], row["learning_graph"]
assert user.profile_path(me).exists() and json.loads(user.profile_path(me).read_text())["stats"]["tasks_done"] == 1

print("ALL OK")
