"""Scheduling around the learner's real week, from their Google Calendar's free/busy.

Three layers, each doing what it is good at:

    capacity   plain arithmetic. Free time inside the learner's study window, shared across all their roadmaps
               in proportion to each roadmap's weekly hours; hours a busy day can't hold move to freer days of
               the same week (up to twice the normal day). The scheduler (progress.schedule) uses it per day.
    slots      rules. Each free block gets tasks that are allowed next (dependencies, plan order), sized to
               the block: deep work goes in long blocks, light tasks in short gaps.
    learning   a small contextual bandit. Every suggestion is logged; when the day is over it is resolved
               (was that task finished that day?) and the outcome updates success rates per context - time of
               day, block length, weekday/weekend, right after a meeting - for each kind of task. Those rates
               reorder the rules' candidates once there is evidence, and are shown as reasons.

Only free/busy is read (gcal.py); event titles never reach GLAI.
"""

import json
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from . import gcal, progress
from .schemas import Plan, Task

SETTINGS_FILE = "calendar.json"
LOG_FILE = "calendar_log.json"
CACHE_FILE = "calendar_cache.json"
CACHE_TTL = 300  # seconds before free/busy is fetched again
DEFAULT_SETTINGS = {
    "weekday": ["07:00", "22:30"],  # the window the learner is willing to study in
    "weekend": ["09:00", "22:00"],
    "min_slot_minutes": 20,  # shorter gaps are not worth a task
    "buffer_minutes": 10,  # breathing room after each event
    "calendars": ["primary"],
    "horizon_days": 14,
}
MAX_DAY_FACTOR = 2.0  # a free day takes at most twice its normal share when busy days spill over
CANDIDATES_PER_ROADMAP = 3  # how far past the plan order a slot may reach
PRIOR = (1.0, 1.0)  # Beta prior per learning cell: no opinion
LEARNING_WEIGHT = 0.8


# ---------------------------------------------------------------- settings


def load_settings(root: Path) -> dict:
    p = root / SETTINGS_FILE
    saved = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return {**DEFAULT_SETTINGS, **{k: v for k, v in saved.items() if k in DEFAULT_SETTINGS}}


def save_settings(root: Path, changes: dict) -> dict:
    s = load_settings(root)
    for key in ("weekday", "weekend"):
        if key in changes:
            a, b = changes[key]
            if not (_minutes(a) < _minutes(b)):
                raise ValueError(f"The {key} window must start before it ends.")
            s[key] = [a, b]
    for key, lo, hi in (("min_slot_minutes", 10, 180), ("buffer_minutes", 0, 60), ("horizon_days", 3, 28)):
        if key in changes:
            v = int(changes[key])
            if not lo <= v <= hi:
                raise ValueError(f"{key.replace('_', ' ')} must be between {lo} and {hi}.")
            s[key] = v
    (root / SETTINGS_FILE).write_text(json.dumps(s, indent=2), encoding="utf-8")
    _CACHE.clear()
    return s


def _minutes(hhmm: str) -> int:
    h, m = map(int, str(hhmm).split(":"))
    if not (0 <= h <= 24 and 0 <= m < 60):
        raise ValueError(f"Not a time: {hhmm}")
    return h * 60 + m


# ---------------------------------------------------------------- free time


@dataclass
class Slot:
    start: datetime
    end: datetime
    after_busy: bool = False  # an event ends just before it

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


_CACHE: dict = {}


def _busy(root: Path, settings: dict, now: datetime) -> tuple[list[tuple[datetime, datetime]] | None, str | None]:
    """Busy intervals for the horizon: fresh from Google every few minutes, else the last good copy."""
    key = (str(root), now.date())
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1], None
    start = datetime.combine(now.date(), datetime.min.time()).astimezone()
    end = start + timedelta(days=settings["horizon_days"] + 1)
    try:
        intervals = gcal.busy(root, start, end, settings["calendars"])
    except gcal.CalendarError as e:
        cached = _read_cache(root)
        return cached, f"{e} Using the last calendar read." if cached is not None else str(e)
    _CACHE[key] = (time.time(), intervals)
    (root / CACHE_FILE).write_text(json.dumps([[a.isoformat(), b.isoformat()] for a, b in intervals]), encoding="utf-8")
    return intervals, None


def _read_cache(root: Path) -> list[tuple[datetime, datetime]] | None:
    p = root / CACHE_FILE
    if not p.exists():
        return None
    return [(datetime.fromisoformat(a), datetime.fromisoformat(b)) for a, b in json.loads(p.read_text(encoding="utf-8"))]


def free_slots(day: date, busy: list[tuple[datetime, datetime]], settings: dict, now: datetime | None = None) -> list[Slot]:
    """Free blocks on `day` inside the study window, after removing busy time (plus a buffer after each event).
    Today's blocks start no earlier than now."""
    tz = (now or datetime.now()).astimezone().tzinfo
    window = settings["weekend" if day.weekday() >= 5 else "weekday"]
    lo = datetime.combine(day, datetime.min.time(), tz) + timedelta(minutes=_minutes(window[0]))
    hi = datetime.combine(day, datetime.min.time(), tz) + timedelta(minutes=_minutes(window[1]))
    if now and now.date() == day:
        rounded = now.astimezone(tz).replace(second=0, microsecond=0)
        rounded += timedelta(minutes=(5 - rounded.minute % 5) % 5)
        lo = max(lo, rounded)
    buffer = timedelta(minutes=settings["buffer_minutes"])
    blocked = sorted((a, b + buffer) for a, b in busy if a < hi and b + buffer > lo)
    slots, cursor, after = [], lo, False
    for a, b in blocked:
        if a > cursor:
            slots.append(Slot(cursor, min(a, hi), after))
        if b > cursor:
            cursor, after = b, True
    if cursor < hi:
        slots.append(Slot(cursor, hi, after))
    return [s for s in slots if s.minutes >= settings["min_slot_minutes"]]


def free_hours(slots: list[Slot]) -> float:
    return sum(s.minutes for s in slots) / 60


# ---------------------------------------------------------------- capacity across roadmaps


def capacities(plans: list[tuple[str, Plan]], free: dict[date, float], today: date) -> dict[str, dict[date, float]]:
    """Hours per roadmap per day. Each day's free time is shared in proportion to the roadmaps' normal day;
    what a busy day can't hold moves to later days of the same week that have room."""
    out: dict[str, dict[date, float]] = {name: {} for name, _ in plans}
    weeks: dict[tuple[int, int], list[date]] = {}
    for d in sorted(free):
        weeks.setdefault(d.isocalendar()[:2], []).append(d)
    for days in weeks.values():
        left = {d: free[d] - (sum(progress.hours_logged(p, d) for _, p in plans) if d == today else 0) for d in days}
        left = {d: max(0.0, h) for d, h in left.items()}
        demand = {name: {d: progress.daily_hours(p) if progress.is_study_day(p, d) else 0.0 for d in days}
                  for name, p in plans}
        # 1. each day: share what is free in proportion to demand
        for d in days:
            want = sum(demand[n][d] for n, _ in plans)
            scale = min(1.0, left[d] / want) if want else 0.0
            for n, _ in plans:
                out[n][d] = demand[n][d] * scale
            left[d] -= sum(out[n][d] for n, _ in plans)
        # 2. carry each roadmap's shortfall to the earliest later days with room
        for n, p in plans:
            short = sum(demand[n][d] for d in days) - sum(out[n][d] for d in days)
            for d in days:
                if short <= 1e-6:
                    break
                if not progress.is_study_day(p, d):
                    continue
                room = min(left[d], MAX_DAY_FACTOR * progress.daily_hours(p) - out[n][d])
                extra = max(0.0, min(room, short))
                out[n][d] += extra
                left[d] -= extra
                short -= extra
    return out


# ---------------------------------------------------------------- the learner


def _bucket(minutes: int) -> str:
    return "short" if minutes < 45 else "medium" if minutes <= 90 else "long"


def _time_of_day(dt: datetime) -> str:
    h = dt.hour
    return "morning" if h < 12 else "afternoon" if h < 17 else "evening" if h < 21 else "night"


def features(slot: Slot, task: Task) -> dict:
    return {"tod": _time_of_day(slot.start), "len": _bucket(slot.minutes), "weekend": slot.start.weekday() >= 5,
            "after_busy": slot.after_busy, "focus": progress.focus(task), "difficulty": progress.difficulty(task)}


def _cells(f: dict) -> list[str]:
    return [f"tod={f['tod']}|focus={f['focus']}", f"len={f['len']}|focus={f['focus']}",
            f"weekend={int(f['weekend'])}|focus={f['focus']}", f"tod={f['tod']}|difficulty={f['difficulty']}",
            f"after_busy={int(f['after_busy'])}|focus={f['focus']}"]


@dataclass
class Learner:
    """Success counts per context cell, rebuilt from the resolved suggestion log."""
    cells: dict[str, list[float]] = field(default_factory=dict)  # cell -> [done, not done]

    @classmethod
    def from_log(cls, log: dict) -> "Learner":
        model = cls()
        for day in log.get("days", {}).values():
            if not day.get("resolved"):
                continue
            for s in day.get("suggestions", []):
                for c in _cells(s["features"]):
                    done, missed = model.cells.setdefault(c, [0.0, 0.0])
                    model.cells[c] = [done + (s["outcome"] == 1), missed + (s["outcome"] == 0)]
        return model

    def predict(self, f: dict) -> tuple[float, float]:
        """(chance the task gets done in this context, how much evidence that rests on: 0..1)."""
        means, seen = [], 0.0
        for c in _cells(f):
            done, missed = self.cells.get(c, [0.0, 0.0])
            a, b = PRIOR[0] + done, PRIOR[1] + missed
            means.append(a / (a + b))
            seen += done + missed
        return sum(means) / len(means), min(1.0, seen / (5 * 8))  # ~8 outcomes per cell -> full weight

    def insight(self, f: dict) -> str | None:
        """The strongest piece of evidence behind a prediction, in words."""
        best = None
        for c in _cells(f)[:3]:
            done, missed = self.cells.get(c, [0.0, 0.0])
            n = done + missed
            if n >= 3 and (best is None or abs(done / n - 0.5) > abs(best[1] / best[2] - 0.5)):
                best = (c, done, n)
        if not best:
            return None
        cell, done, n = best
        context = cell.split("|")[0].split("=")[1]
        what = {"tod": f"in the {context}", "len": f"in {context} blocks",
                "weekend": "at weekends" if context == "1" else "on weekdays"}[cell.split("=")[0]]
        return f"you finished {int(done)} of {int(n)} {f['focus']} tasks {what}"


# ---------------------------------------------------------------- assigning tasks to slots


def _rules(slot: Slot, t: Task, rank: int) -> tuple[float, list[str]]:
    minutes, need = slot.minutes, progress.remaining_hours(t) * 60
    f = progress.focus(t)
    score, why = (1.0 if need <= minutes else minutes / need), []
    if need <= minutes:
        why.append(f"fits this {minutes}-min block")
    if f == "deep":
        score += 0.2 if minutes >= 90 else -0.4 if minutes < 45 else 0.0
        if minutes >= 90:
            why.append("long block suits deep work")
    elif f == "light":
        score += 0.2 if minutes < 45 else -0.1 if minutes > 90 else 0.0
        if minutes < 45:
            why.append("short gap suits a light task")
    score -= 0.15 * rank
    if rank == 0:
        why.append("next in your plan")
    return score, why


def _ready(plan: Plan, done_today: set[str]) -> list[Task]:
    """Unfinished tasks of the current milestones whose dependencies are met, in plan order."""
    out = []
    for m in plan.roadmap.milestones:
        if not m.tasks_written:
            break
        finished = {t.id for t in m.tasks if t.status == "done"} | done_today
        for t in m.tasks:
            if t.status != "done" and t.id not in done_today and all(d in finished for d in t.depends_on):
                out.append(t)
        if any(t.status != "done" for t in m.tasks):
            break  # milestones are sequential
    return out


def plan_day(day: date, slots: list[Slot], plans: list[tuple[str, Plan]], caps: dict[str, dict[date, float]],
             learner: Learner, carry: dict | None = None) -> list[dict]:
    """Fill the day's free blocks with tasks: rules propose, the learner reorders, nothing breaks dependencies.
    `carry` holds work placed on earlier days (remaining hours, tasks finished), so days follow on."""
    carry = carry if carry is not None else {}
    remaining = carry.setdefault("remaining", {(n, t.id): progress.remaining_hours(t)
                                               for n, p in plans for _, t in progress.all_tasks(p)})
    finished: dict[str, set[str]] = carry.setdefault("finished", {n: set() for n, _ in plans})
    budget = {n: caps.get(n, {}).get(day, progress.capacity(p, day)) for n, p in plans}
    out = []
    for slot in slots:
        cursor = slot.start
        items = []
        while (slot.end - cursor).total_seconds() / 60 >= 15:
            sub = Slot(cursor, slot.end, slot.after_busy and cursor == slot.start)
            best = None
            for n, p in plans:
                if budget[n] < 0.1:
                    continue
                for rank, t in enumerate(_ready(p, finished[n])[:CANDIDATES_PER_ROADMAP]):
                    rule, why = _rules(sub, t, rank)
                    f = features(sub, t)
                    chance, evidence = learner.predict(f)
                    score = rule + LEARNING_WEIGHT * (chance - 0.5) * evidence
                    if best is None or score > best[0]:
                        best = (score, n, t, why, f, chance, evidence)
            if best is None:
                break
            _, n, t, why, f, chance, evidence = best
            minutes = int(min(sub.minutes, remaining[(n, t.id)] * 60, budget[n] * 60))
            if minutes < 15:
                break
            note = learner.insight(f) if evidence > 0.1 else None
            items.append({"roadmap": n, "skill": dict(plans)[n].roadmap.skill, "task_id": t.id, "title": t.title,
                          "minutes": minutes, "focus": f["focus"], "difficulty": f["difficulty"],
                          "reasons": ([note] if note else []) + why, "chance": round(chance, 2),
                          "evidence": round(evidence, 2), "features": f,
                          "start": cursor.isoformat(timespec="minutes")})
            remaining[(n, t.id)] -= minutes / 60
            budget[n] -= minutes / 60
            if remaining[(n, t.id)] <= 1e-6:
                finished[n].add(t.id)
            cursor += timedelta(minutes=minutes)
        out.append({"start": slot.start.isoformat(timespec="minutes"), "end": slot.end.isoformat(timespec="minutes"),
                    "minutes": slot.minutes, "after_busy": slot.after_busy, "items": items})
    return out


# ---------------------------------------------------------------- the suggestion log


def _load_log(root: Path) -> dict:
    p = root / LOG_FILE
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"days": {}}


def _save_log(root: Path, log: dict) -> None:
    (root / LOG_FILE).write_text(json.dumps(log, indent=1), encoding="utf-8")


def resolve(log: dict, plans: list[tuple[str, Plan]], today: date) -> bool:
    """Close every past day: a suggestion succeeded if its task was finished that day."""
    changed = False
    done_on = {(n, h["task"], h["at"][:10]) for n, p in plans for h in p.history if h.get("event") == "done"}
    for d, entry in log["days"].items():
        if entry.get("resolved") or d >= today.isoformat():
            continue
        for s in entry["suggestions"]:
            s["outcome"] = 1 if (s["roadmap"], s["task_id"], d) in done_on else 0
        entry["resolved"] = True
        changed = True
    return changed


def record(log: dict, day: date, planned: list[dict]) -> None:
    """Remember what was suggested today (the latest plan wins until the day is over)."""
    suggestions = [{"roadmap": i["roadmap"], "task_id": i["task_id"], "features": i["features"], "start": i["start"]}
                   for s in planned for i in s["items"]]
    entry = log["days"].get(day.isoformat())
    if entry and entry.get("resolved"):
        return
    log["days"][day.isoformat()] = {"resolved": False, "suggestions": suggestions}


# ---------------------------------------------------------------- entry points


def roadmap_plans(root: Path) -> list[tuple[str, Plan]]:
    from .dashboard import roadmap_files

    out = []
    for p in roadmap_files(root):
        try:
            out.append((p.stem, progress.load(p)))
        except Exception:
            continue
    return out


def apply(root: Path, plans: list[tuple[str, Plan]], today: date) -> str | None:
    """Give every loaded plan its real per-day capacity, when a calendar is connected. Returns a problem, if any."""
    if not plans or not gcal.connected(root):
        return None
    settings = load_settings(root)
    now = datetime.now().astimezone() if today == date.today() else None
    busy, problem = _busy(root, settings, now or datetime.combine(today, datetime.min.time()).astimezone())
    if busy is None:
        return problem
    days = [today + timedelta(days=i) for i in range(settings["horizon_days"])]
    free = {d: free_hours(free_slots(d, busy, settings, now)) for d in days}
    caps = capacities(plans, free, today)
    for name, plan in plans:
        progress.set_capacity(plan, caps[name])
    return problem


def typical_slot_minutes(root: Path, today: date) -> tuple[int | None, float | None]:
    """(median free block in minutes, free hours per week) over the coming two weeks, if a calendar is connected."""
    if not gcal.connected(root):
        return None, None
    settings = load_settings(root)
    now = datetime.now().astimezone()
    busy, _ = _busy(root, settings, now)
    if busy is None:
        return None, None
    slots = [s for i in range(14) for s in free_slots(today + timedelta(days=i), busy, settings, now)]
    if not slots:
        return None, 0.0
    lengths = sorted(min(s.minutes, 180) for s in slots)
    return lengths[len(lengths) // 2], round(free_hours(slots) / 2, 1)


def calendar_payload(root: Path, today: date, days: int = 7) -> dict:
    """The learner's week: free blocks and the tasks suggested for each, plus what the learner has learned."""
    configured, connected = gcal.client() is not None, gcal.connected(root)
    settings = load_settings(root)
    base = {"configured": configured, "connected": connected, "settings": settings, "error": None, "days": [],
            "learning": {"observations": 0}}
    if not connected:
        return base
    plans = roadmap_plans(root)
    now = datetime.now().astimezone() if today == date.today() else datetime.combine(today, datetime.min.time()).astimezone()
    busy, problem = _busy(root, settings, now)
    if busy is None:
        return {**base, "error": problem}
    log = _load_log(root)
    changed = resolve(log, plans, today)
    learner = Learner.from_log(log)
    horizon = [today + timedelta(days=i) for i in range(settings["horizon_days"])]
    slots_by_day = {d: free_slots(d, busy, settings, now if d == today else None) for d in horizon}
    caps = capacities(plans, {d: free_hours(s) for d, s in slots_by_day.items()}, today)
    for name, plan in plans:
        progress.set_capacity(plan, caps[name])
    out_days, carry = [], {}
    for d in horizon[:days]:
        planned = plan_day(d, slots_by_day[d], plans, caps, learner, carry) if plans else []
        if d == today:
            record(log, d, planned)
            changed = True
        for s in planned:
            for i in s["items"]:
                i.pop("features", None)
        out_days.append({"date": d.isoformat(), "free_hours": round(free_hours(slots_by_day[d]), 1),
                         "study_hours": round(sum(caps[n].get(d, 0) for n, _ in plans), 1), "slots": planned})
    if changed:
        _save_log(root, log)
    resolved = [s for e in log["days"].values() if e.get("resolved") for s in e["suggestions"]]
    return {**base, "error": problem, "days": out_days,
            "free_hours_week": round(sum(free_hours(slots_by_day[d]) for d in horizon[:7]), 1),
            "learning": {"observations": len(resolved), "finished": sum(s["outcome"] for s in resolved)}}
