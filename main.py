import argparse
import os
import re
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from rich.console import Console  # noqa: E402
from rich.markdown import Markdown  # noqa: E402
from rich.markup import escape  # noqa: E402

from roadmap_agent import progress, user  # noqa: E402
from roadmap_agent.schemas import Plan, Task  # noqa: E402

console = Console()

DEFAULT_MODELS = {"ollama": "qwen2.5:3b", "anthropic": "claude-opus-5-5", "openai": "gpt-5-mini", "groq": "openai/gpt-oss-120b"}
COMMANDS = {"new", "status", "today", "next", "done", "skip", "fail", "web", "profile", "tests", "submit", "hire", "upload"}
MARK = {"done": "[x]", "todo": "[ ]", "failed": "[ ] (retry)"}
KIND = {"core": "", "remedial": " _helper_", "step": " _step_"}


def fmt_day(d: date) -> str:
    return d.strftime("%a %d %b")


def fmt_span(span: tuple[date, date] | None) -> str:
    if not span:
        return ""
    a, b = span
    return fmt_day(a) if a == b else f"{fmt_day(a)} - {fmt_day(b)}"


# ---------------------------------------------------------------- rendering


def task_line(t: Task, span: tuple[date, date] | None = None) -> list[str]:
    after = f" - after {', '.join(t.depends_on)}" if t.depends_on else ""
    when = f" **{fmt_span(span)}**" if span else ""
    topic = f" [{t.topic}]" if t.topic else ""
    pts = (f"{progress.difficulty(t)}, earned {progress.earned(t)} pts" if t.status == "done"
           else f"{progress.difficulty(t)}, {progress.max_points(t)} pts")
    lines = [f"- {MARK[t.status]} `{t.id}`{when} **{t.title}**{KIND[t.kind]} ({t.estimated_hours:g}h, {pts}{after}){topic}: {t.description}"]
    lines.append(f"  - Deliverable: {t.deliverable}")
    if t.spent_hours and t.status != "done":
        lines.append(f"  - {t.spent_hours:g}h already spent on this attempt")
    if t.evaluations:
        e = t.evaluations[-1]
        score = f", review {e.review.score}/100" if e.review else ""
        lines.append(f"  - Last submission: {'passed' if e.passed else 'not passed'}, tests {e.tests_passed}/{e.tests_total}{score}")
    lines += [f"  - {url}" for url in t.resources]
    return lines


def progress_line(plan: Plan, today: date) -> str:
    pr = progress.projection(plan, today)
    slip = pr["slip_days"]
    state = "on track" if slip <= 0 else f"{slip} days behind"
    return (
        f"{pr['done_hours']:g}h done, {pr['left_hours']:g}h left - finishing {fmt_day(pr['projected_end'])} "
        f"(planned {fmt_day(pr['planned_end'])}, {state})"
    )


def to_markdown(plan: Plan, today: date, issues: list[str] | None = None) -> str:
    rm = plan.roadmap
    sched = progress.schedule(plan, today)
    lines = [f"# {rm.skill} Roadmap ({rm.total_weeks} weeks)", "", rm.summary, ""]
    lines += [
        f"**Schedule:** {plan.profile.get('weekly_hours')}h/week over {plan.profile.get('days_per_week', 7)} study days "
        f"(~{progress.daily_hours(plan):.1f}h/day), started {fmt_day(progress.start_date(plan))}",
        "",
        f"**Progress:** {progress_line(plan, today)}",
        "",
    ]
    if rm.prerequisites:
        lines += ["**Prerequisites:** " + ", ".join(rm.prerequisites), ""]
    if plan.profile.get("known_skills"):
        lines += ["**Builds on what you already know:** " + ", ".join(plan.profile["known_skills"]), ""]
    for i, m in enumerate(rm.milestones, 1):
        hours = progress.milestone_hours(plan, m)
        lines += [f"## Milestone {i}: {m.title}  (weeks {m.week_start}-{m.week_end}, ~{hours:.0f}h)", "", f"_{m.objective}_", ""]
        if not m.tasks_written:
            lines += [f"- Tasks are written when you get close to this milestone, based on how the earlier ones went. Scheduled {fmt_span(sched.get(f'M{i}'))}", ""]
            continue
        for t in m.tasks:
            lines += task_line(t, sched.get(t.id))
        lines += ["", "**Done when:**"] + [f"- {c}" for c in m.success_criteria] + [""]
    if issues:
        lines += ["---", "> Unresolved validation warnings:"] + [f"> - {x}" for x in issues]
    return "\n".join(lines)


HINT = (
    "[dim]  finished: python main.py done {id} [--spent H]   |   didn't start: python main.py skip {id}\n"
    "  tried but couldn't do it: python main.py fail {id} --spent H[/]"
)


def show_next(plan: Plan, today: date) -> None:
    t = progress.next_task(plan)
    if not t:
        m = progress.current_milestone(plan)
        if m and not m.tasks_written:
            console.print(f"[yellow]Tasks for '{escape(m.title)}' aren't written yet - check your API key / model and rerun.[/]")
        else:
            console.print("[bold green]All tasks done - roadmap complete![/]")
        return
    m = progress.current_milestone(plan)
    span = progress.schedule(plan, today).get(t.id)
    console.print(f"\n[bold]Next up[/] [dim](milestone: {escape(m.title)})[/]")
    console.print(Markdown("\n".join(task_line(t, span))))
    console.print(HINT.format(id=t.id))


# ---------------------------------------------------------------- plan files


def slug(skill: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", skill.lower()).strip("_")


def resolve_plan(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    plans = sorted(Path(".").glob("roadmap_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not plans:
        sys.exit('No roadmap_*.json found. Create one first: python main.py new "Skill" ...')
    return plans[0]


def open_plan(args) -> tuple[Plan, Path]:
    path = resolve_plan(args.plan)
    plan = progress.load(path)
    if not plan.profile.get("start_date"):  # plans made before scheduling existed
        plan.profile["start_date"] = args.today.isoformat()
        plan.profile.setdefault("days_per_week", 7)
    write_ahead(plan, path, args.today)
    return plan, path


def write_ahead(plan: Plan, path: Path, today: date) -> None:
    """Write tasks for the current milestone if missing, and the next one when the current is nearly done."""
    todo = progress.milestones_to_write(plan)
    if not todo:
        return
    from roadmap_agent.graph import fill_milestone

    for i in todo:
        m = plan.roadmap.milestones[i]
        console.print(f"[cyan]Writing tasks for milestone {i + 1}: {escape(m.title)} ...[/]")
        try:
            plan.profile["interests"] = user.load(path.parent).interests
            fill_milestone(plan, i)
        except Exception as e:  # no key, network, ... - keep the plan usable and try again next time
            console.print(f"[yellow]  couldn't write them now ({type(e).__name__}: {e}); will retry next time[/]")
            break
    save_all(plan, path, today)


def save_all(plan: Plan, path: Path, today: date, issues: list[str] | None = None) -> None:
    progress.save(plan, path)
    user.rebuild(path.parent, today, extra_plan=path)  # skill graph + stats stay in sync with every change
    path.with_suffix(".md").write_text(to_markdown(plan, today, issues), encoding="utf-8")


def cmd_profile(args) -> None:
    folder = Path(".")
    profile = user.load(folder)
    changed = False
    if args.name is not None:
        profile.name, changed = args.name, True
    if args.interests is not None:
        profile.interests, changed = user.split_list(args.interests), True
    if args.skills is not None:
        profile.claimed_skills, changed = user.split_list(args.skills), True
    if changed:
        user.save(profile, folder)
    profile = user.rebuild(folder, args.today)
    st, g = profile.stats, profile.skill_graph

    demo = " [yellow](demo profile - stats and skill graph are frozen; set \"demo\": false to track real progress)[/]" if profile.demo else ""
    console.print(f"[bold]{escape(profile.name or 'Learner')}[/]{demo}")
    console.print(f"  Interests: {escape(', '.join(profile.interests) or '- (set with: profile --interests \"football, music\")')}")
    console.print(f"  Told us you know: {escape(', '.join(profile.claimed_skills) or '-')}")
    rate = f"{st.first_try_rate:.0%}" if st.first_try_rate is not None else "-"
    by_diff = ", ".join(f"{k} {v}" for k, v in st.points_by_difficulty.items())
    console.print(f"\n[bold]Points: {st.points}[/]" + (f"  [dim]({by_diff})[/]" if by_diff else ""))
    console.print(
        f"[bold]Stats[/]  {st.hours_logged:g}h logged | {st.tasks_done} tasks done | first-try success {rate} | "
        f"{st.failures} failures, {st.skips} skips, {st.helper_tasks} helper tasks | "
        f"streak {st.current_streak} (best {st.longest_streak}) | {st.active_days} active days"
    )
    for r in st.roadmaps:
        state = "on track" if r.slip_days <= 0 else f"{r.slip_days} days behind"
        console.print(
            f"  {escape(r.skill)}: {r.progress:.0%} ({r.tasks_done}/{r.tasks_total} tasks), "
            f"finishing {r.projected_end} - {state}"
        )

    console.print("\n[bold]Skills acquired[/] [dim](only what finished tasks have earned)[/]")
    if not g.nodes:
        console.print("  none yet - finish every task of a sub-skill to earn it")
    by_name = {n.name: n for n in g.nodes}

    def earned(n) -> str:
        bits = [f"acquired {n.acquired_on}" if n.acquired_on else "acquired"]
        bits += [f"{n.hours:g}h"] if n.hours else []
        bits += [f"{n.points} pts"] if n.points else []
        bits += ["needed extra help"] if n.failures or n.helpers_used else []
        return ", ".join(bits)

    for n in [n for n in g.nodes if n.kind == "claimed"]:
        console.print(escape(f"  ✓ {n.name} - verified by the skills check ({earned(n)})"), style="green")
    groups: list[str] = []
    for n in g.nodes:  # nodes are stored in learning order
        name = n.parent if n.kind == "subskill" else n.name if n.kind == "skill" else None
        if name and name not in groups:
            groups.append(name)
    for name in groups:
        whole = by_name.get(name)
        done = f"  ✓ whole skill acquired ({earned(whole)})" if whole and whole.kind == "skill" else ""
        console.print(escape(f"  {name}{done}"), style="bold green" if done else "bold")
        for n in g.nodes:
            if n.kind == "subskill" and n.parent == name:
                console.print(escape(f"      ✓ {n.name:<46.46} {earned(n)}"), style="green")
    console.print(f"\n[dim]Saved to {user.profile_path(folder)}[/]")


def cmd_tests(args) -> None:
    from roadmap_agent import submissions

    _, path = open_plan(args)
    plan = progress.load(path)
    _, t = progress.find(plan, args.task_id)
    try:
        view = submissions.prepare_tests(path, args.task_id, on_event=lambda k, msg, d: console.print(f"[cyan]{msg} ...[/]"))
    except submissions.SubmissionError as e:
        sys.exit(str(e))
    console.print(f"[bold]{t.id} {escape(t.title)}[/]")
    console.print(f"  Submit: {escape(', '.join(view['required_files']) or 'the deliverable file(s)')}")
    console.print(f"  [dim]{escape(view['format'])}[/]")
    console.print("[bold]What is checked:[/]")
    for c in view["checks"]:
        console.print(f"  - {escape(c['verifies'])}  [dim](task: \"{escape(c['quote'])}\")[/]")
    if view["dropped"]:
        console.print(f"  [dim]({view['dropped']} generated test(s) not graded: invented a requirement or "
                      "failed against the reference solution)[/]")
    console.print(f"[dim]  then: python main.py submit {t.id} --path ./my_folder   (or --repo <git url> [--branch b] [--subdir d])[/]")


def print_evaluation(e: dict) -> None:
    mark = "[bold green]PASSED[/]" if e["passed"] else "[bold red]NOT PASSED[/]"
    console.print(f"{mark}  tests {e['tests_passed']}/{e['tests_total']}  [dim]({escape(e['source'])})[/]")
    for f in e["failures"][:8]:
        console.print(f"  [red]x[/] {escape(f)}")
    r = e.get("review")
    if r:
        console.print(f"[bold]Review score: {r['score']}/100[/]")
        for title, key in (("Strengths", "strengths"), ("Issues", "issues"), ("Next steps", "next_steps")):
            if r[key]:
                console.print(f"  [bold]{title}[/]")
                for i in r[key][:5]:
                    console.print(f"    - {escape(i)}")


def cmd_submit(args) -> None:
    from roadmap_agent import submissions

    _, path = open_plan(args)
    try:
        res = submissions.submit(
            path, args.task_id, args.path or args.repo, args.branch, args.subdir, args.spent, args.today, args.force,
            on_event=lambda k, msg, d: k in ("tests", "adapt") and console.print(f"[cyan]{msg} ...[/]"),
        )
    except submissions.SubmissionError as e:
        sys.exit(f"{e}" + (" Use --force to evaluate it again anyway." if "already done" in str(e) else ""))
    print_evaluation(res["evaluation"])
    out, tid = res["outcome"], res["task_id"]
    if out["action"] == "done":
        console.print(f"[green]Marked {tid} done.[/] [bold]+{out['points']} pts[/] [dim](max {out['max_points']}; review score counts)[/]")
    elif out["action"] == "continue":
        console.print(f"[yellow]Not a full attempt yet - keep going on {tid} next study day, then resubmit.[/]")
    elif out["action"] in ("helpers", "split"):
        console.print("[bold]Do these first, they build what this task needs:[/]" if out["action"] == "helpers"
                      else "[bold]Split into smaller steps:[/]")
        for a in out["added"]:
            console.print(f"  + {a['id']} {escape(a['title'])} ({a['hours']:g}h)")
    elif not res["evaluation"]["passed"]:
        console.print("[dim]Fix and resubmit. If you've spent real time and are stuck: add --spent H to adapt the plan.[/]")
    plan = progress.load(path)
    save_all(plan, path, args.today)
    show_next(plan, args.today)


def cmd_hire(args) -> None:
    from roadmap_agent import server

    server.serve_hire(Path("."), args.port, open_browser=not args.no_browser)


def cmd_upload(args) -> None:
    """Copy this folder's roadmaps, profile and progress into your account on the shared backend."""
    import getpass

    from roadmap_agent import migrate

    password = os.environ.get("GLAI_PASSWORD") or getpass.getpass(f"Password for {args.email}: ")
    sent = migrate.upload(Path("."), args.email, password)
    console.print(f"[green]Uploaded {len(sent)} file(s):[/] {escape(', '.join(sent))}")


def cmd_web(args) -> None:
    from roadmap_agent import server

    _, path = open_plan(args)  # also writes the next milestone's tasks if they're due
    server.serve_learner(Path("."), args.port, open_path=f"/?roadmap={path.stem}", open_browser=not args.no_browser)


def apply_model_args(args) -> None:
    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
        if not args.model:  # a local model name is meaningless to a hosted provider
            os.environ["LLM_MODEL"] = DEFAULT_MODELS.get(args.provider, os.getenv("LLM_MODEL", ""))
    if args.model:
        os.environ["LLM_MODEL"] = args.model


# ---------------------------------------------------------------- commands


def cmd_new(args) -> None:
    from roadmap_agent.create import create_roadmap

    console.print(f"[dim]model: {os.getenv('LLM_PROVIDER', 'ollama')} / {os.getenv('LLM_MODEL', 'qwen2.5:3b')}[/]")

    def show(kind: str, msg: str, data: dict) -> None:
        if kind == "known":
            console.print("[bold cyan]Building on what you already know:[/]", *[f"  - {s}" for s in data["skills"]], sep="\n")
        elif kind == "plan_queries":
            console.print("[bold cyan]Search queries:[/]", *[f"  - {q}" for q in data["queries"]], sep="\n")
        elif kind == "search":
            console.print(f"[dim]  fetched {data['results']} results[/]")
        elif kind == "validate":
            issues = data["issues"]
            console.print(f"[bold]validate:[/] {f'{len(issues)} issue(s)' if issues else 'OK'}")
            for x in issues:
                console.print(f"  [yellow]- {x}[/]")
        elif kind != "saved":
            console.print(f"[bold green]{kind}[/] done")

    path, plan, issues = create_roadmap(
        Path("."), args.skill, level=args.level, weekly_hours=args.hours, target_weeks=args.weeks, goal=args.goal,
        new_skills=user.split_list(args.skills), days_per_week=args.days_per_week,
        start=date.fromisoformat(args.start) if args.start else None, today=args.today,
        out=Path(args.out).with_suffix(".json") if args.out else None,
        use_other_roadmaps=not args.ignore_other_roadmaps, on_event=show,
    )
    save_all(plan, path, args.today, issues)  # also writes the markdown copy
    console.print(Markdown(to_markdown(plan, args.today, issues)))
    console.print(f"\n[bold]Saved to {path.with_suffix('.md')} (progress file: {path})[/]")
    show_next(plan, args.today)


def cmd_status(args) -> None:
    plan, path = open_plan(args)
    sched = progress.schedule(plan, args.today)
    console.print(f"[bold]{escape(plan.roadmap.skill)}[/] [dim]({path})[/]")
    for i, m in enumerate(plan.roadmap.milestones, 1):
        done = sum(t.status == "done" for t in m.tasks)
        bar = "#" * done + "." * (len(m.tasks) - done)
        console.print(escape(f"  {i}. {m.title:<45.45} [{bar}] {done}/{len(m.tasks)}"))
        if m is progress.current_milestone(plan):
            for t in m.tasks:
                style = {"done": "dim", "failed": "red", "todo": "default"}[t.status]
                when = fmt_span(sched.get(t.id))
                line = f"       {MARK[t.status]:<11} {t.id:<7} {when:<25} {t.title}{KIND[t.kind].replace('_', '')}"
                console.print(escape(line), style=style)
    console.print(f"\n  {escape(progress_line(plan, args.today))}")
    show_next(plan, args.today)


def cmd_today(args) -> None:
    """Today's quests across every roadmap (or just --plan)."""
    today = args.today
    paths = [resolve_plan(args.plan)] if args.plan else sorted(Path(".").glob("roadmap_*.json"))
    if not paths:
        sys.exit('No roadmap yet. Create one: python main.py new "Skill" ...')
    total_h, total_q = 0.0, 0
    for path in paths:
        open_plan(argparse.Namespace(plan=str(path), today=today))  # writes the next milestone's tasks if due
        plan = progress.load(path)
        sched = progress.schedule(plan, today)
        console.print(f"\n[bold]{escape(plan.roadmap.skill)}[/] [dim]({path.stem})[/]")
        if not progress.is_study_day(plan, today):
            console.print(f"  rest day - next quest {fmt_day(progress.next_study_day(plan, today))}")
            continue
        tasks = progress.tasks_on(plan, today, today)
        if not tasks:
            console.print("  nothing left today")
            continue
        total_q += len(tasks)
        total_h += min(progress.daily_hours(plan), sum(progress.remaining_hours(t) for t in tasks))
        for t in tasks:
            console.print(Markdown("\n".join(task_line(t, sched.get(t.id)))))
        console.print(f"[dim]  done: python main.py done {tasks[0].id} --plan {path.name}   |   "
                      f"skip: python main.py skip {tasks[0].id} --plan {path.name}[/]")
    console.print(f"\n[bold]{total_q} quest{'s' if total_q != 1 else ''} today across {len(paths)} roadmap"
                  f"{'s' if len(paths) != 1 else ''} - about {total_h:.1f}h[/]")


def cmd_next(args) -> None:
    plan, _ = open_plan(args)
    show_next(plan, args.today)


def cmd_done(args) -> None:
    plan, path = open_plan(args)
    t = progress.mark_done(plan, args.task_id, args.today, args.spent)
    save_all(plan, path, args.today)
    write_ahead(plan, path, args.today)
    console.print(f"[green]Done:[/] {t.id} {escape(t.title)}  [bold]+{t.points} pts[/] "
                  f"[dim]({progress.difficulty(t)}, max {progress.max_points(t)})[/]")
    show_next(plan, args.today)


def cmd_skip(args) -> None:
    plan, path = open_plan(args)
    day = progress.skip(plan, args.task_id, args.today)
    save_all(plan, path, args.today)
    _, t = progress.find(plan, args.task_id)
    console.print(f"[yellow]Moved to {fmt_day(day)}:[/] {t.id} {escape(t.title)} - everything after it shifts too")
    console.print(f"  {escape(progress_line(plan, args.today))}")


def cmd_fail(args) -> None:
    from roadmap_agent.graph import get_llm

    plan, path = open_plan(args)
    m, t = progress.find(plan, args.task_id)
    before = [x.id for x in m.tasks if x.status != "done"]
    needed = progress.ENOUGH_TIME * t.estimated_hours
    llm = get_llm() if t.spent_hours + args.spent >= needed else None
    action, added = progress.mark_failed(plan, args.task_id, args.spent, args.today, llm=llm)
    save_all(plan, path, args.today)
    m, t = progress.find(plan, args.task_id)

    if action == "continue":
        console.print(
            f"[yellow]Not a full attempt yet[/] ({t.spent_hours:g}h of {t.estimated_hours:g}h; a real attempt is "
            f"{needed:g}h+). Continue {t.id} on {fmt_day(t.not_before)} - {progress.remaining_hours(t):g}h left."
        )
    else:
        what = "Do these first, they build what this task needs" if action == "helpers" else "Too big a step - split it into"
        console.print(f"[red]Couldn't complete[/] {t.id} {escape(t.title)} (failure {t.fail_count})")
        console.print(f"[bold]{what}:[/]")
        for a in added:
            console.print(f"  + {a.id} {escape(a.title)} ({a.estimated_hours:g}h)")
        after = [x.id for x in m.tasks if x.status != "done"]
        console.print(f"[bold]New order:[/] {' -> '.join(after)}  [dim](was {' -> '.join(before)})[/]")
    console.print(f"  {escape(progress_line(plan, args.today))}")
    show_next(plan, args.today)


def main():
    # keep the old `python main.py "Skill" --hours 8` form working
    if len(sys.argv) > 1 and sys.argv[1] not in COMMANDS and not sys.argv[1].startswith("-"):
        sys.argv.insert(1, "new")

    p = argparse.ArgumentParser(description="Realistic learning roadmaps that adapt to your progress.")
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--today", type=date.fromisoformat, default=date.today(), help=argparse.SUPPRESS)  # testing
    model_opts = argparse.ArgumentParser(add_help=False)
    model_opts.add_argument("--provider", default=None, help="groq | anthropic | openai | ollama (overrides LLM_PROVIDER in .env)")
    model_opts.add_argument("--model", default=None, help="e.g. claude-opus-5-5, gpt-5-mini, qwen2.5:3b (overrides LLM_MODEL)")
    plan_opt = argparse.ArgumentParser(add_help=False, parents=[common, model_opts])
    plan_opt.add_argument("--plan", default=None, help="progress file (default: newest roadmap_*.json here)")

    n = sub.add_parser("new", parents=[common, model_opts], help="research a skill and build a roadmap")
    n.add_argument("skill", help='e.g. "Machine Learning" or "CS50x"')
    n.add_argument("--level", default="beginner", help="beginner / intermediate / advanced")
    n.add_argument("--hours", type=int, default=8, help="hours per week you can commit")
    n.add_argument("--days-per-week", type=int, default=7, help="study days per week: 7 = every day (default), 6 = Mon-Sat, 5 = Mon-Fri")
    n.add_argument("--start", default=None, help="start date YYYY-MM-DD (default: today)")
    n.add_argument("--weeks", type=int, default=None, help="target duration in weeks (optional)")
    n.add_argument("--goal", default="", help='e.g. "land a junior data analyst job"')
    n.add_argument("--skills", default="", help='what you already know, comma-separated, e.g. "Python basics, SQL, Excel"')
    n.add_argument("--ignore-other-roadmaps", action="store_true", help="don't count skills finished in other roadmaps")
    n.add_argument("--out", default=None, help="output path (.md and .json are written)")
    n.set_defaults(func=cmd_new)

    sub.add_parser("status", parents=[plan_opt], help="progress overview").set_defaults(func=cmd_status)
    sub.add_parser("today", parents=[plan_opt], help="what to do today").set_defaults(func=cmd_today)
    sub.add_parser("next", parents=[plan_opt], help="show the next task").set_defaults(func=cmd_next)
    wb = sub.add_parser("web", parents=[plan_opt], help="open the roadmap dashboard (learner app)")
    wb.add_argument("--port", type=int, default=8001)
    wb.add_argument("--no-browser", action="store_true")
    wb.set_defaults(func=cmd_web)

    te = sub.add_parser("tests", parents=[plan_opt], help="show what a task's tests check and how to submit")
    te.add_argument("task_id")
    te.set_defaults(func=cmd_tests)

    sm = sub.add_parser("submit", parents=[plan_opt], help="run a task's tests on your code and review it")
    sm.add_argument("task_id")
    src = sm.add_mutually_exclusive_group(required=True)
    src.add_argument("--path", help="local folder with your code")
    src.add_argument("--repo", help="git repo URL with your code")
    sm.add_argument("--branch", default=None, help="git branch (default: the repo's default)")
    sm.add_argument("--subdir", default=None, help="subfolder holding this task's files")
    sm.add_argument("--spent", type=float, default=None, help="hours spent; with a failing result this adapts the plan")
    sm.add_argument("--force", action="store_true", help="evaluate even if the task is already done")
    sm.set_defaults(func=cmd_submit)

    up = sub.add_parser("upload", help="copy this folder into your account on the shared backend (Supabase)")
    up.add_argument("--email", required=True)
    up.set_defaults(func=cmd_upload)

    hi = sub.add_parser("hire", help="hiring site: search learners by acquired skills")
    hi.add_argument("--port", type=int, default=8000)
    hi.add_argument("--no-browser", action="store_true")
    hi.set_defaults(func=cmd_hire)

    pf = sub.add_parser("profile", parents=[common], help="your interests, skills, skill graph and stats")
    pf.add_argument("--name", default=None)
    pf.add_argument("--interests", default=None, help='comma-separated, e.g. "football, music, finance"')
    pf.add_argument("--skills", default=None, help='what you already know (replaces the list), e.g. "Python basics, SQL"')
    pf.set_defaults(func=cmd_profile)

    d = sub.add_parser("done", parents=[plan_opt], help="mark a task as completed")
    d.add_argument("task_id")
    d.add_argument("--spent", type=float, default=None, help="hours it took (default: the estimate)")
    d.set_defaults(func=cmd_done)

    s = sub.add_parser("skip", parents=[plan_opt], help="didn't start the task: move it to the next study day")
    s.add_argument("task_id")
    s.set_defaults(func=cmd_skip)

    f = sub.add_parser("fail", parents=[plan_opt], help="tried but couldn't do it; the plan adapts")
    f.add_argument("task_id")
    f.add_argument("--spent", type=float, required=True, help="hours you spent trying")
    f.set_defaults(func=cmd_fail)

    args = p.parse_args()
    if hasattr(args, "provider"):
        apply_model_args(args)
    try:
        args.func(args)
    except KeyError as e:
        sys.exit(str(e.args[0]))


if __name__ == "__main__":
    main()
