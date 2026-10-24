import os
from datetime import datetime, timedelta

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from voice_assistant import scheduler as S

NOW = datetime(2026, 10, 24, 10, 0)  # a Saturday


def task(**kw):
    base = dict(id="t1", name="Test", prompt="say hi", mode="remind", kind="daily", time="08:00")
    base.update(kw)
    return S.Task(**base)


def test_next_run_for_each_kind():
    assert S.compute_next(task(kind="daily", time="08:00"), NOW) == datetime(2026, 10, 25, 8, 0)
    assert S.compute_next(task(kind="daily", time="11:30"), NOW) == datetime(2026, 10, 24, 11, 30)
    assert S.compute_next(task(kind="weekdays", time="08:00"), NOW) == datetime(2026, 10, 26, 8, 0)  # Saturday -> Monday
    assert S.compute_next(task(kind="weekly", time="07:30", days=[2, 4]), NOW) == datetime(2026, 10, 28, 7, 30)  # Wed
    assert S.compute_next(task(kind="every", every_minutes=90), NOW) == datetime(2026, 10, 24, 11, 30)
    assert S.compute_next(task(kind="once", run_at="2026-10-24T17:00"), NOW) == datetime(2026, 10, 24, 17, 0)
    assert S.compute_next(task(kind="once", run_at="2026-10-24T09:00"), NOW) is None
    assert S.compute_next(task(kind="daily", time="10:00"), NOW) == datetime(2026, 10, 25, 10, 0)  # strictly after


def test_describing_tasks_in_plain_words():
    assert S.describe(task(kind="weekdays", time="08:00")) == "every weekday at 08:00"
    assert S.describe(task(kind="weekly", time="07:30", days=[0, 2])) == "every Mon, Wed at 07:30"
    assert S.describe(task(kind="every", every_minutes=30)) == "every 30 minutes"
    assert S.describe(task(kind="every", every_minutes=120)) == "every 2 hours"
    assert S.describe(task(kind="once", run_at="2026-10-24T17:05")) == "once on Sat 24 Oct at 17:05"


def test_making_tasks_validates_and_understands_relative_times():
    t = S.make_task("Call Sam", "call Sam", kind="once", in_minutes=45, now=NOW)
    assert t.kind == "once" and t.next_run == "2026-10-24T10:45"
    assert S.make_task("x", "y", kind="once", time="09:00", now=NOW).next_run == "2026-10-25T09:00"  # already passed today: tomorrow
    assert S.make_task("x", "y", kind="once", time="17:00", now=NOW).next_run == "2026-10-24T17:00"
    assert S.make_task("x", "y", kind="weekly", time="7:05", days=["Mon", "friday"], now=NOW).days == [0, 4]
    for bad in (dict(name=""), dict(prompt=" "), dict(mode="shout"), dict(kind="yearly"), dict(kind="daily", time="25:00"), dict(kind="daily", time="nope"),
                dict(kind="weekly", time="08:00"), dict(kind="every", every_minutes=1), dict(kind="once", time="09:00", date_="2026-10-01"),
                dict(kind="once", time="09:00", date_="24/10"), dict(kind="weekly", time="08:00", days=["someday"])):
        args = dict(name="n", prompt="p", mode="remind", kind="once", time="12:00", now=NOW)
        args.update(bad)
        with pytest.raises(ValueError):
            S.make_task(**args)


def test_store_persists_survives_damage_and_finds_by_name(tmp_path):
    p = tmp_path / "tasks.json"
    st = S.TaskStore(p)
    st.add(S.make_task("Morning briefing", "weather", "do", "daily", "08:00", now=NOW))
    st.add(S.make_task("Call Sam", "call Sam", "remind", "once", in_minutes=30, now=NOW))
    again = S.TaskStore(p)
    assert [t.name for t in again.tasks] == ["Morning briefing", "Call Sam"]
    assert again.find("morning").name == "Morning briefing" and again.find("call sam").name == "Call Sam" and again.find("nope") is None
    assert again.find(again.tasks[0].id).id == again.tasks[0].id
    p.write_text("{broken", encoding="utf-8")
    assert S.TaskStore(p).tasks == []
    for i in range(S.MAX_TASKS):
        again.tasks.append(task(id=f"x{i}"))
    with pytest.raises(ValueError):
        again.add(task(id="over"))


def runner(results=None, log=None):
    def run(t):
        (log if log is not None else []).append(t.name)
        if results and isinstance(results.get(t.name), Exception):
            raise results[t.name]
        return "done", f"ran {t.name}"
    return run


def sched(tmp_path, tasks, log=None, results=None, **kw):
    st = S.TaskStore(tmp_path / "t.json")
    st.tasks = list(tasks)
    notes, changes = [], []
    s = S.Scheduler(st, runner(results, log), note=notes.append, on_change=lambda: changes.append(1), clock=lambda: NOW, **kw)
    return s, st, notes, changes


def test_due_tasks_run_once_and_repeats_move_to_the_next_time(tmp_path):
    log = []
    daily = S.make_task("Daily", "x", kind="daily", time="09:00", now=NOW - timedelta(days=1))
    daily.next_run = "2026-10-24T09:55"
    later = S.make_task("Later", "y", kind="daily", time="18:00", now=NOW)
    s, st, notes, changes = sched(tmp_path, [daily, later], log)
    assert s.tick(NOW) == ["Daily"] and log == ["Daily"]
    d = st.find("daily")
    assert d.last_status == "done" and d.last_result == "ran Daily" and d.next_run == "2026-10-25T09:00:00" and d.enabled
    assert changes and s.tick(NOW) == []  # not due again


def test_a_one_off_task_turns_itself_off_and_is_cleaned_up_after_a_week(tmp_path):
    once = S.make_task("Once", "x", kind="once", in_minutes=1, now=NOW - timedelta(minutes=5))
    s, st, *_ = sched(tmp_path, [once])
    assert s.tick(NOW) == ["Once"]
    t = st.find("once")
    assert not t.enabled and t.next_run == "" and t.last_status == "done"
    s.tick(NOW + timedelta(days=8))
    assert st.tasks == []


def test_a_task_that_is_too_late_is_skipped_and_reported_but_a_repeat_every_n_minutes_still_runs(tmp_path):
    missed = task(id="a", name="Missed", kind="daily", time="08:00", next_run="2026-10-24T08:00:00")  # 2 hours late
    every = task(id="b", name="Poller", kind="every", every_minutes=30, next_run="2026-10-24T08:00:00")
    log = []
    s, st, notes, _ = sched(tmp_path, [missed, every], log)
    assert s.tick(NOW) == ["Poller"] and log == ["Poller"]
    m = st.find("missed")
    assert m.last_status == "missed" and m.next_run == "2026-10-25T08:00:00" and "Missed" in m.last_result
    assert any("was missed" in n for n in notes)


def test_a_task_a_few_minutes_late_still_runs(tmp_path):
    late = task(next_run="2026-10-24T09:50:00")
    s, st, *_ = sched(tmp_path, [late])
    assert s.tick(NOW) == ["Test"]


def test_paused_tasks_and_the_master_switch_stop_everything(tmp_path):
    due = task(next_run="2026-10-24T09:59:00")
    paused = task(id="p", name="Paused", next_run="2026-10-24T09:59:00", enabled=False)
    s, st, *_ = sched(tmp_path, [due, paused], enabled=lambda: False)
    assert s.tick(NOW) == []
    s.enabled = lambda: True
    assert s.tick(NOW) == ["Test"]


def test_a_failing_task_is_recorded_and_does_not_stop_the_others(tmp_path):
    a = task(id="a", name="Bad", next_run="2026-10-24T09:59:00")
    b = task(id="b", name="Good", next_run="2026-10-24T09:59:00")
    s, st, *_ = sched(tmp_path, [a, b], results={"Bad": RuntimeError("boom")})
    assert s.tick(NOW) == ["Bad", "Good"]
    assert st.find("bad").last_status == "failed" and "boom" in st.find("bad").last_result and st.find("good").last_status == "done"


def test_run_now_runs_in_the_background_without_moving_the_schedule(tmp_path):
    import time as _t

    t = task(next_run="2026-10-25T08:00:00")
    s, st, *_ = sched(tmp_path, [t])
    assert s.run_now("t1") and not s.run_now("nope")
    end = _t.time() + 3
    while _t.time() < end and not t.last_status:
        _t.sleep(0.01)
    assert t.last_status == "done" and t.next_run == "2026-10-25T08:00:00"


# ---- the voice tools -------------------------------------------------------------------------------
def tools(tmp_path, answer=True, tasks=()):
    s, st, notes, changes = sched(tmp_path, tasks)
    asked = []
    built = {x.name: x for x in S.build_schedule_tools(st, s, lambda m: asked.append(m) or answer, clock=lambda: NOW)}
    return built, st, asked, changes


def test_scheduling_by_voice_asks_first_and_saves(tmp_path):
    t, st, asked, changes = tools(tmp_path)
    out = t["schedule_task"].handler(name="Call Sam", what="call Sam", in_minutes=30)
    assert "Scheduled" in out and "Saturday 24 October at 10:30" in out and asked and "Call Sam" in asked[0] and len(st.tasks) == 1 and changes


def test_declined_or_invalid_schedules_save_nothing(tmp_path):
    t, st, *_ = tools(tmp_path, answer=False)
    assert t["schedule_task"].handler(name="x", what="y", in_minutes=30) == "The user declined this action."
    assert t["schedule_task"].handler(name="x", what="y", kind="daily", time="99:00").startswith("Error:")
    assert st.tasks == []


def test_listing_pausing_running_and_cancelling(tmp_path):
    base = S.make_task("Briefing", "weather", "do", "daily", "08:00", now=NOW)
    t, st, asked, _ = tools(tmp_path, tasks=[base])
    assert "Briefing" in t["list_scheduled_tasks"].handler() and "every day at 08:00" in t["list_scheduled_tasks"].handler()
    assert t["pause_scheduled_task"].handler(task="briefing") == "Paused “Briefing”."
    assert not base.enabled
    assert t["pause_scheduled_task"].handler(task="briefing", paused=False).startswith("Resumed") and base.enabled and base.next_run
    assert "Running" in t["run_scheduled_task_now"].handler(task="Briefing")
    assert t["cancel_scheduled_task"].handler(task="briefing") == "Deleted “Briefing”." and st.tasks == []
    assert t["cancel_scheduled_task"].handler(task="gone").startswith("No scheduled task")


# ---- the app ----------------------------------------------------------------------------------------
@pytest.fixture
def win(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox

    import voice_assistant.app as A

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(A, "save_config", lambda *a, **k: None)
    monkeypatch.setattr("voice_assistant.app.config_dir", lambda: tmp_path)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    from voice_assistant.config import Config

    w = A.MainWindow(Config(models_dir=str(tmp_path / "none"), setup_done=True, check_updates=False))
    w.scheduler.stop()
    yield w, A
    w.close()


def test_a_reminder_is_spoken_and_noted(win):
    w, A = win
    spoken = []
    w.session.announce = spoken.append
    status, text = w._run_scheduled(task(mode="remind", prompt="stand up and stretch"))
    assert status == "done" and spoken == ["Reminder: stand up and stretch"]


def test_a_job_runs_unattended_declines_approvals_and_says_what_it_skipped(win, monkeypatch):
    w, A = win
    spoken, seen = [], {}

    class FakeAgent:
        def __init__(self, llm, prompt, registry, rounds, max_messages=0):
            seen["prompt"] = prompt

        def respond(self, text, cancel):
            seen["approved"] = w._confirm("Open Spotify?\nmore detail")  # would normally pop a dialog and wait
            yield "I checked the weather."

    monkeypatch.setattr(A, "Agent", FakeAgent)
    w.session.announce = spoken.append
    status, text = w._run_scheduled(task(mode="do", prompt="tell me the weather and open Spotify", name="Briefing"))
    assert status == "done" and seen["approved"] is False and "scheduled task running by itself" in seen["prompt"]
    assert "I checked the weather." in text and "Skipped because it needed your approval: Open Spotify?" in text
    assert spoken and spoken[0].startswith("Briefing.") and not getattr(w._unattended, "on", False)  # the flag is cleared afterwards


def test_the_tasks_tab_lists_adds_pauses_and_deletes(win):
    w, A = win
    from voice_assistant.schedule_ui import AddTaskDialog

    page = w.tasks_page
    w.task_store.add(S.make_task("Briefing", "weather", "do", "weekdays", "08:00"))
    page.refresh()
    assert page.list.count() == 1 and "every weekday at 08:00" in page.list.item(0).text()
    page.list.setCurrentRow(0)
    page._toggle()
    assert not w.task_store.tasks[0].enabled and page.toggle_btn.text() == "Resume"
    dlg = AddTaskDialog()
    dlg.name.setText("Water plants")
    dlg.what.setPlainText("water the plants")
    dlg.repeat.setCurrentIndex(dlg.repeat.findData("every"))
    dlg.every.setValue(60)
    dlg._ok()
    assert dlg.task and dlg.task.every_minutes == 60
    dlg.name.setText("")
    dlg.task = None
    dlg._ok()
    assert dlg.task is None and "needs a name" in dlg.error.text()
    page._delete()
    assert w.task_store.tasks == []


def test_voice_only_mode_keeps_the_scheduling_tools(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    import voice_assistant.app as A
    from voice_assistant.config import Config

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(A, "save_config", lambda *a, **k: None)
    monkeypatch.setattr("voice_assistant.app.config_dir", lambda: tmp_path)
    w = A.MainWindow(Config(models_dir=str(tmp_path / "none"), setup_done=True, check_updates=False, odysseus_mode="only"))
    try:
        w.scheduler.stop()
        names = set(w._registry.names())
        assert set(S.TOOL_NAMES) <= names and "ask_odysseus" in names and "get_time" not in names
    finally:
        w.close()
