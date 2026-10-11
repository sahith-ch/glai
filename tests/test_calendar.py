"""Scheduling around a real week: free blocks, shared capacity, slot choices that learn, and the Google sign-in."""

import json
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from roadmap_agent import calendar_plan as cp
from roadmap_agent import gcal, progress, server
from roadmap_agent.schemas import Milestone, Plan, Roadmap, Task

TZ = datetime.now().astimezone().tzinfo
MON = date(2026, 10, 12)


def at(d: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


S = {**cp.DEFAULT_SETTINGS, "weekday": ["18:00", "22:00"], "weekend": ["09:00", "21:00"], "buffer_minutes": 10}

# --- free blocks: study window minus busy time (+ buffer), short gaps dropped
busy = [(at(MON, "18:30"), at(MON, "19:00")), (at(MON, "21:30"), at(MON, "21:50"))]
slots = cp.free_slots(MON, busy, S)
print("free:", [(s.start.strftime("%H:%M"), s.end.strftime("%H:%M"), s.after_busy) for s in slots])
assert [(s.start.strftime("%H:%M"), s.end.strftime("%H:%M")) for s in slots] == [("18:00", "18:30"), ("19:10", "21:30")]
assert slots[1].after_busy and not slots[0].after_busy
assert cp.free_slots(MON, busy, S, now=at(MON, "20:02"))[0].start == at(MON, "20:05")  # today: from now on


def task(i, topic, hours=1.0, focus=None, deps=(), difficulty="medium"):
    return Task(id=i, title=f"Task {i}", description="d", estimated_hours=hours, deliverable=f"t{i.replace('.', '_')}.py",
                topic=topic, focus=focus, depends_on=list(deps), difficulty=difficulty)


def plan(skill, tasks, weekly=10, days=5):
    return Plan(profile={"skill": skill, "weekly_hours": weekly, "days_per_week": days, "start_date": "2026-10-12"},
                roadmap=Roadmap(skill=skill, summary="", total_weeks=4, milestones=[
                    Milestone(title="M1", objective="o", week_start=1, week_end=4, tasks=tasks, success_criteria=[])]))


# --- capacity: two roadmaps share the free time; a busy day's hours move to freer days of the same week
py = plan("py", [task(f"1.{i}", "T", 2) for i in range(1, 9)], weekly=10)
sql = plan("sql", [task(f"1.{i}", "Q", 1) for i in range(1, 6)], weekly=5)
week = [MON + timedelta(days=i) for i in range(7)]
free = {d: 0.0 if d == MON else 4.0 for d in week}  # Monday fully booked
caps = cp.capacities([("py", py), ("sql", sql)], free, MON)
print("py:", {d.strftime("%a"): round(h, 2) for d, h in caps["py"].items()})
assert caps["py"][MON] == 0 and caps["sql"][MON] == 0
assert abs(sum(caps["py"].values()) - 10) < 1e-6 and abs(sum(caps["sql"].values()) - 5) < 1e-6, "weekly hours kept"
for d in week:
    assert caps["py"][d] + caps["sql"][d] <= free[d] + 1e-6, "never more than the free time"
assert caps["py"][week[5]] == 0, "weekends stay off when the roadmap studies Mon-Fri"

# --- the scheduler follows capacity: nothing is planned on the booked Monday
progress.set_capacity(py, caps["py"])
assert progress.schedule(py, MON)["1.1"][0] == MON + timedelta(days=1)
progress.set_capacity(py, None)
assert progress.schedule(py, MON)["1.1"][0] == MON  # without a calendar: as before

# --- slots: deep work in the long block, light work in the short gap, dependencies respected
mix = plan("mix", [task("1.1", "A", 0.5, "light"), task("1.2", "B", 2, "deep"), task("1.3", "C", 0.5, "light", deps=["1.2"])])
week_slots = [cp.Slot(at(MON, "18:00"), at(MON, "18:30")), cp.Slot(at(MON, "19:00"), at(MON, "21:30"))]
learner = cp.Learner()
day = cp.plan_day(MON, week_slots, [("mix", mix)], {"mix": {MON: 4.0}}, learner)
placed = [[i["task_id"] for i in s["items"]] for s in day]
print("placed:", placed)
assert placed[0] == ["1.1"], "the 30-min gap gets the light task"
assert placed[1][0] == "1.2" and "1.3" in placed[1] and placed[1].index("1.3") > placed[1].index("1.2"), "1.3 waits for 1.2"

# --- carried across days: tomorrow does not repeat today's plan
carry = {}
cp.plan_day(MON, week_slots, [("mix", mix)], {"mix": {MON: 4.0}}, learner, carry)
tue = cp.plan_day(MON + timedelta(days=1), week_slots, [("mix", mix)], {"mix": {MON + timedelta(days=1): 4.0}}, learner, carry)
assert all(not s["items"] for s in tue), "everything was already placed on Monday"

# --- learning: evening practice tasks that never get done lose to ones that do
log = {"days": {}}
for k in range(12):
    d = (MON - timedelta(days=30 - k)).isoformat()
    log["days"][d] = {"resolved": True, "suggestions": [
        {"roadmap": "x", "task_id": "a", "features": {"tod": "evening", "len": "long", "weekend": False, "after_busy": False,
                                                      "focus": "deep", "difficulty": "hard"}, "outcome": 0},
        {"roadmap": "x", "task_id": "b", "features": {"tod": "evening", "len": "long", "weekend": False, "after_busy": False,
                                                      "focus": "practice", "difficulty": "medium"}, "outcome": 1}]}
learned = cp.Learner.from_log(log)
evening_long = cp.Slot(at(MON, "19:00"), at(MON, "21:30"))
deep_chance, evidence = learned.predict(cp.features(evening_long, task("9.1", "Z", 2, "deep", difficulty="hard")))
practice_chance, _ = learned.predict(cp.features(evening_long, task("9.2", "Z", 2, "practice")))
print("learned:", round(deep_chance, 2), round(practice_chance, 2), "evidence", round(evidence, 2))
assert practice_chance > 0.7 > 0.3 > deep_chance and evidence > 0.5
assert "practice tasks in the evening" in learned.insight(cp.features(evening_long, task("9.2", "Z", 2, "practice")))
two = plan("two", [task("1.1", "A", 2, "deep", difficulty="hard"), task("1.2", "B", 2, "practice")])
first = cp.plan_day(MON, [evening_long], [("two", two)], {"two": {MON: 2.5}}, learned)[0]["items"][0]
assert first["task_id"] == "1.2", "the evening goes to the kind of task this learner actually finishes"
assert any("finished" in r for r in first["reasons"])

# --- resolving a day: a suggestion counts as done only if that task was finished that day
past = MON - timedelta(days=1)
log = {"days": {past.isoformat(): {"resolved": False, "suggestions": [
    {"roadmap": "two", "task_id": "1.1", "features": {}}, {"roadmap": "two", "task_id": "1.2", "features": {}}]}}}
two.history.append({"at": f"{past.isoformat()}T20:00:00", "event": "done", "task": "1.2"})
assert cp.resolve(log, [("two", two)], MON)
assert [s["outcome"] for s in log["days"][past.isoformat()]["suggestions"]] == [0, 1]

# --- the whole payload, with Google replaced by fixed busy times
root = Path(tempfile.mkdtemp())
progress.save(mix, root / "roadmap_mix.json")
gcal.connected = lambda r: True
gcal.busy = lambda r, a, b, c=None: [(at(MON, "18:30"), at(MON, "19:00"))]
cp._CACHE.clear()
cp.save_settings(root, {"weekday": ["18:00", "22:00"]})
payload = cp.calendar_payload(root, MON)
assert payload["connected"] and payload["error"] is None and len(payload["days"]) == 7
assert payload["days"][0]["slots"][0]["items"], "Monday's first free block has a task"
assert json.loads((root / cp.LOG_FILE).read_text())["days"][MON.isoformat()]["suggestions"], "suggestions logged"
code, body = server.learner_api(root, "POST", "/api/calendar/settings", {}, {"weekday": ["22:00", "18:00"]})
assert code == 400 and "start before" in body["error"]

# --- sign-in: state is single-use, and the token exchange carries the PKCE verifier
import os
os.environ.update(GOOGLE_CLIENT_ID="cid", GOOGLE_CLIENT_SECRET="sec")
url = gcal.start("http://127.0.0.1:8001/api/calendar/callback")
assert "calendar.freebusy" in url and "code_challenge=" in url and "access_type=offline" in url
state = url.split("state=")[1].split("&")[0]
sent = []
gcal._post = lambda u, data, headers=None, form=True: sent.append(data) or {"refresh_token": "r", "access_token": "a", "expires_in": 3600}
gcal.finish(root, state, "the-code")
assert sent[0]["code_verifier"] and sent[0]["grant_type"] == "authorization_code"
assert json.loads((root / gcal.TOKEN_FILE).read_text())["refresh_token"] == "r"
try:
    gcal.finish(root, state, "again")
    raise AssertionError("a state must not be reusable")
except gcal.CalendarError:
    pass

print("ALL OK")
