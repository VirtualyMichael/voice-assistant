"""Scheduled tasks: reminders and jobs that run at a set time or on a repeat while the assistant is open.

A task is either a *reminder* (the assistant just says your text) or a *job* (the assistant carries out your instruction with its tools, on its own,
and says the result). Jobs run unattended: anything that needs your approval is declined instead of waiting for you, so a task can never do more
while you are away than the assistant already does without asking. Tasks live in tasks.json on this PC. A task that came due while the app was
closed or the PC asleep still runs if it is less than 15 minutes late; older ones are skipped and reported.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from .config import config_dir

KINDS = ("once", "daily", "weekdays", "weekly", "every")
MODES = ("remind", "do")
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MAX_TASKS = 50
MIN_EVERY_MINUTES = 5
GRACE_MINUTES = 15
KEEP_FINISHED_DAYS = 7
TICK_S = 15.0
TOOL_NAMES = ("schedule_task", "list_scheduled_tasks", "cancel_scheduled_task", "pause_scheduled_task", "run_scheduled_task_now")


def default_path() -> Path:
    return config_dir() / "tasks.json"


@dataclass
class Task:
    id: str
    name: str
    prompt: str
    mode: str = "remind"  # remind: say the text | do: carry it out with the assistant's tools and say the result
    kind: str = "daily"
    time: str = "08:00"  # HH:MM, local time (daily, weekdays, weekly, once)
    days: list[int] = field(default_factory=list)  # weekly: 0 = Monday
    every_minutes: int = 0
    run_at: str = ""  # once: the local date and time
    enabled: bool = True
    created: str = ""
    next_run: str = ""
    last_run: str = ""
    last_status: str = ""  # done | failed | missed
    last_result: str = ""


def _dt(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def _hm(text: str) -> tuple[int, int]:
    try:
        h, m = str(text).strip().split(":")
        h, m = int(h), int(m)
    except ValueError as e:
        raise ValueError(f"the time {text!r} is not HH:MM (24-hour)") from e
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"the time {text!r} is not a real time of day")
    return h, m


def parse_days(days) -> list[int]:
    out: list[int] = []
    for d in days or []:
        if isinstance(d, int) and 0 <= d <= 6:
            idx = d
        else:
            key = str(d).strip().lower()[:3]
            if key not in DAYS:
                raise ValueError(f"{d!r} is not a day of the week")
            idx = DAYS.index(key)
        if idx not in out:
            out.append(idx)
    return sorted(out)


def compute_next(t: Task, after: datetime) -> datetime | None:
    """The first run strictly after `after` (for a repeat that just ran, pass the time it ran)."""
    if t.kind == "once":
        run = _dt(t.run_at)
        return run if run and run > after else None
    if t.kind == "every":
        return after + timedelta(minutes=max(t.every_minutes, MIN_EVERY_MINUTES))
    h, m = _hm(t.time)
    allowed = {"daily": range(7), "weekdays": range(5), "weekly": t.days}[t.kind]
    for add in range(0, 9):
        day = after.date() + timedelta(days=add)
        cand = datetime(day.year, day.month, day.day, h, m)
        if cand > after and cand.weekday() in allowed:
            return cand
    return None


def describe(t: Task) -> str:
    if t.kind == "once":
        run = _dt(t.run_at)
        return "once on " + (run.strftime("%a %d %b at %H:%M").replace(" 0", " ") if run else "?")
    if t.kind == "every":
        n = t.every_minutes
        return f"every {n} minutes" if n % 60 else (f"every {n // 60} hour" + ("s" if n // 60 != 1 else ""))
    if t.kind == "weekly":
        return f"every {', '.join(DAYS[d].title() for d in t.days)} at {t.time}"
    return f"every {'weekday' if t.kind == 'weekdays' else 'day'} at {t.time}"


def make_task(name: str, prompt: str, mode: str = "remind", kind: str = "once", time: str = "", date_: str = "", days=None,
              every_minutes: int = 0, in_minutes: float = 0, now: datetime | None = None) -> Task:
    """Validate and build a task (the voice tool and the Tasks page both use this). Raises ValueError with a plain-language reason."""
    now = now or datetime.now()
    name, prompt = " ".join(str(name).split())[:60], str(prompt).strip()[:1000]
    if not name:
        raise ValueError("the task needs a name")
    if not prompt:
        raise ValueError("the task needs text: what to say or do")
    if mode not in MODES:
        raise ValueError("mode must be 'remind' or 'do'")
    if in_minutes:
        kind = "once"
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    t = Task(uuid.uuid4().hex[:8], name, prompt, mode, kind, created=now.isoformat(timespec="seconds"))
    if kind == "once":
        if in_minutes:
            if not 1 <= float(in_minutes) <= 60 * 24 * 365:
                raise ValueError("in_minutes must be between 1 and a year")
            run = (now + timedelta(minutes=float(in_minutes))).replace(second=0, microsecond=0)
        else:
            h, m = _hm(time)
            try:
                day = date.fromisoformat(date_) if date_ else now.date()
            except ValueError as e:
                raise ValueError(f"the date {date_!r} is not YYYY-MM-DD") from e
            run = datetime(day.year, day.month, day.day, h, m)
            if not date_ and run <= now:  # a bare time that already passed today means tomorrow
                run += timedelta(days=1)
        if run <= now:
            raise ValueError("that time is already in the past")
        t.run_at, t.time = run.isoformat(timespec="minutes"), run.strftime("%H:%M")
        t.next_run = t.run_at
    elif kind == "every":
        if int(every_minutes or 0) < MIN_EVERY_MINUTES:
            raise ValueError(f"a repeat must be at least every {MIN_EVERY_MINUTES} minutes")
        t.every_minutes = int(every_minutes)
        t.next_run = compute_next(t, now).isoformat(timespec="seconds")
    else:
        _hm(time)
        t.time = f"{_hm(time)[0]:02d}:{_hm(time)[1]:02d}"
        if kind == "weekly":
            t.days = parse_days(days)
            if not t.days:
                raise ValueError("a weekly task needs at least one day")
        nxt = compute_next(t, now)
        t.next_run = nxt.isoformat(timespec="minutes") if nxt else ""
    return t


class TaskStore:
    def __init__(self, path: Path | None = None):
        self.path = path or default_path()
        self.lock = threading.RLock()
        self.tasks: list[Task] = []
        self.load()

    def load(self) -> None:
        known = {f.name for f in fields(Task)}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.tasks = [Task(**{k: v for k, v in d.items() if k in known}) for d in data.get("tasks", []) if isinstance(d, dict)]
        except (OSError, ValueError, TypeError):
            self.tasks = []  # missing or damaged: start empty rather than fail

    def save(self) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "tasks": [asdict(t) for t in self.tasks]}, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)

    def add(self, t: Task) -> None:
        with self.lock:
            if len(self.tasks) >= MAX_TASKS:
                raise ValueError(f"you already have {MAX_TASKS} scheduled tasks")
            self.tasks.append(t)
            self.save()

    def find(self, ref: str) -> Task | None:
        """By id, exact name, or a unique part of a name."""
        ref = (ref or "").strip().lower()
        with self.lock:
            for t in self.tasks:
                if t.id == ref or t.name.lower() == ref:
                    return t
            hits = [t for t in self.tasks if ref and ref in t.name.lower()]
            return hits[0] if len(hits) == 1 else None

    def remove(self, task_id: str) -> bool:
        with self.lock:
            before = len(self.tasks)
            self.tasks = [t for t in self.tasks if t.id != task_id]
            self.save()
            return len(self.tasks) != before


class Scheduler:
    """Wakes every few seconds and runs what is due. `run(task)` does the work and returns (status, text)."""

    def __init__(self, store: TaskStore, run: Callable[[Task], tuple[str, str]], enabled: Callable[[], bool] = lambda: True,
                 on_change: Callable[[], None] | None = None, note: Callable[[str], None] | None = None,
                 clock: Callable[[], datetime] = datetime.now):
        self.store, self.run, self.enabled, self.clock = store, run, enabled, clock
        self.on_change = on_change or (lambda: None)
        self.note = note or (lambda _m: None)
        self._running: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- lifecycle ---------------------------------------------------------------------------
    def start(self) -> None:
        self.tick()  # catch up on anything that came due while the app was closed
        self._thread = threading.Thread(target=self._loop, daemon=True, name="scheduler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(TICK_S):
            try:
                self.tick()
            except Exception:  # a bug in one run must never stop the schedule
                pass

    # ---- one pass ----------------------------------------------------------------------------
    def tick(self, now: datetime | None = None) -> list[str]:
        """Run what is due. Returns the names of tasks that ran (for tests and logs)."""
        now = now or self.clock()
        ran: list[str] = []
        if not self.enabled():
            return ran
        with self.store.lock:
            due = [t for t in self.store.tasks if t.enabled and t.next_run and (_dt(t.next_run) or now + timedelta(days=1)) <= now]
        for t in due:
            late = now - _dt(t.next_run)
            if late > timedelta(minutes=GRACE_MINUTES) and t.kind != "every":
                self._skip(t, now)
                continue
            if self._execute(t, now):
                ran.append(t.name)
        self._clean(now)
        return ran

    def _skip(self, t: Task, now: datetime) -> None:
        t.last_run, t.last_status, t.last_result = now.isoformat(timespec="seconds"), "missed", f"Missed: it was due {t.next_run.replace('T', ' ')[:16]} while the assistant was not running."
        self._advance(t, now)
        self.note(f"Scheduled task “{t.name}” was missed (the assistant was not running at {t.next_run[11:16] if t.next_run else t.time}).")
        self.store.save()
        self.on_change()

    def _advance(self, t: Task, now: datetime) -> None:
        nxt = compute_next(t, now)
        t.next_run = nxt.isoformat(timespec="seconds") if nxt else ""
        if t.kind == "once" or not nxt:
            t.enabled = False

    def _execute(self, t: Task, now: datetime | None = None) -> bool:
        with self.store.lock:
            if t.id in self._running:
                return False
            self._running.add(t.id)
        try:
            try:
                status, text = self.run(t)
            except Exception as e:  # a failing task is recorded, not fatal
                status, text = "failed", f"{e.__class__.__name__}: {e}"
            done = now or self.clock()
            t.last_run, t.last_status, t.last_result = done.isoformat(timespec="seconds"), status, str(text)[:600]
            self._advance(t, done)
            self.store.save()
            self.on_change()
            return True
        finally:
            with self.store.lock:
                self._running.discard(t.id)

    def run_now(self, task_id: str) -> bool:
        """Run a task right now in the background (from the Tasks page or by voice). Does not move its schedule."""
        t = next((x for x in self.store.tasks if x.id == task_id), None)
        if t is None or t.id in self._running:
            return False

        def work() -> None:
            with self.store.lock:
                self._running.add(t.id)
            try:
                try:
                    status, text = self.run(t)
                except Exception as e:
                    status, text = "failed", f"{e.__class__.__name__}: {e}"
                t.last_run, t.last_status, t.last_result = self.clock().isoformat(timespec="seconds"), status, str(text)[:600]
                self.store.save()
                self.on_change()
            finally:
                with self.store.lock:
                    self._running.discard(t.id)

        threading.Thread(target=work, daemon=True, name=f"task-{t.id}").start()
        return True

    def _clean(self, now: datetime) -> None:
        """Finished one-off tasks disappear after a week."""
        cutoff = now - timedelta(days=KEEP_FINISHED_DAYS)
        with self.store.lock:
            old = [t.id for t in self.store.tasks if t.kind == "once" and not t.enabled and (_dt(t.last_run) or now) < cutoff]
            for i in old:
                self.store.remove(i)
        if old:
            self.on_change()


# ---- voice tools --------------------------------------------------------------------------------
def build_schedule_tools(store: TaskStore, scheduler: Scheduler, confirm: Callable[[str], bool], clock: Callable[[], datetime] = datetime.now) -> list:
    from .agent_tools import _obj, _str
    from .tools import Tool

    def schedule_task(name: str, what: str, mode: str = "remind", kind: str = "once", time: str = "", date: str = "", days: list | None = None,
                      every_minutes: int = 0, in_minutes: float = 0) -> str:
        try:
            t = make_task(name, what, mode, kind, time, date, days, every_minutes, in_minutes, now=clock())
        except ValueError as e:
            return f"Error: {e}."
        verb = "say" if t.mode == "remind" else "do (on its own, using my tools)"
        if not confirm(f"Schedule “{t.name}” {describe(t)}?\n\nIt will {verb}: {t.prompt[:300]}"):
            return "The user declined this action."
        try:
            store.add(t)
        except ValueError as e:
            return f"Error: {e}."
        scheduler.on_change()
        first = _dt(t.next_run)
        return f"Scheduled “{t.name}” {describe(t)}. Next run: {first.strftime('%A %d %B at %H:%M') if first else 'none'}."

    def list_scheduled_tasks() -> str:
        if not store.tasks:
            return "No scheduled tasks."
        rows = []
        for t in store.tasks:
            nxt = _dt(t.next_run)
            state = "paused" if not t.enabled and t.next_run == "" and t.kind != "once" else ("done" if not t.enabled else "on")
            rows.append(f"- {t.name} [{t.id}] ({t.mode}) {describe(t)}; {state}" + (f"; next {nxt.strftime('%a %d %b %H:%M')}" if nxt and t.enabled else "")
                        + (f"; last {t.last_status}: {t.last_result[:120]}" if t.last_status else ""))
        return "\n".join(rows)

    def cancel_scheduled_task(task: str) -> str:
        t = store.find(task)
        if t is None:
            return f"No scheduled task matches {task!r}. Use list_scheduled_tasks."
        if not confirm(f"Delete the scheduled task “{t.name}” ({describe(t)})?"):
            return "The user declined this action."
        store.remove(t.id)
        scheduler.on_change()
        return f"Deleted “{t.name}”."

    def pause_scheduled_task(task: str, paused: bool = True) -> str:
        t = store.find(task)
        if t is None:
            return f"No scheduled task matches {task!r}."
        t.enabled = not paused
        if t.enabled:
            nxt = compute_next(t, clock())
            t.next_run = nxt.isoformat(timespec="seconds") if nxt else ""
            if not nxt:
                t.enabled = False
                return f"“{t.name}” has no future run, so it stays off."
        store.save()
        scheduler.on_change()
        return f"{'Paused' if paused else 'Resumed'} “{t.name}”."

    def run_scheduled_task_now(task: str) -> str:
        t = store.find(task)
        if t is None:
            return f"No scheduled task matches {task!r}."
        return f"Running “{t.name}” now; the result will be spoken." if scheduler.run_now(t.id) else f"“{t.name}” is already running."

    day_list = {"type": "array", "items": {"type": "string"}, "description": "weekly: mon, tue, wed, thu, fri, sat, sun"}
    props = {
        "name": _str, "what": {"type": "string", "description": "remind: the words to say. do: the instruction to carry out, written as a full self-contained request."},
        "mode": {"type": "string", "enum": list(MODES), "description": "remind = just say it; do = carry it out with the assistant's tools and say the result"},
        "kind": {"type": "string", "enum": list(KINDS)}, "time": {"type": "string", "description": "HH:MM, 24-hour local time"},
        "date": {"type": "string", "description": "once: YYYY-MM-DD (omit for today, or tomorrow if the time has passed)"}, "days": day_list,
        "every_minutes": {"type": "integer", "description": "every: minutes between runs (at least 5)"},
        "in_minutes": {"type": "number", "description": "once: run this many minutes from now (use for 'in 2 hours')"},
    }
    return [
        Tool("schedule_task", "Schedule a reminder or a job for later or on a repeat: 'remind me at 5 to call Sam', 'every weekday at 8 give me a briefing', 'in 20 minutes turn off the lights'. "
             "The user confirms first. Call get_time first when you need today's date.", _obj(props, ["name", "what"]), schedule_task),
        Tool("list_scheduled_tasks", "List the scheduled tasks with their next run and last result.", _obj({}, []), list_scheduled_tasks),
        Tool("cancel_scheduled_task", "Delete a scheduled task by name or id. The user confirms first.", _obj({"task": _str}, ["task"]), cancel_scheduled_task),
        Tool("pause_scheduled_task", "Pause (paused true) or resume (paused false) a scheduled task by name or id.", _obj({"task": _str, "paused": {"type": "boolean"}}, ["task"]), pause_scheduled_task),
        Tool("run_scheduled_task_now", "Run a scheduled task immediately, for example to test it.", _obj({"task": _str}, ["task"]), run_scheduled_task_now),
    ]
