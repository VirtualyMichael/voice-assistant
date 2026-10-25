"""A morning briefing: a scheduled task that, each morning, says the date, the weather and what is on the calendar.

It is an ordinary scheduled task (see scheduler.py) written in the assistant's own words, so it shows in the Tasks tab, can be paused or deleted there,
and runs with the same tools as anything else. The calendar comes from the user's phone (phone_read_calendar), only while the phone is connected.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from .scheduler import Scheduler, TaskStore, describe, make_task

BRIEFING_NAME = "Morning briefing"


def briefing_prompt(place: str = "", calendar: bool = True) -> str:
    steps = ["Say good morning and give today's date (call get_time)."]
    if place.strip():
        steps.append(f"Give the weather for {place.strip()} (call get_weather): the temperature range, and rain or snow if likely.")
    if calendar:
        steps.append("Call phone_read_calendar with days=2. Read out today's events in time order, with their times, then mention only the first event tomorrow. "
                     "If it says the calendar is off or no phone is connected, say that in one short clause and move on. Do not invent events.")
    steps.append("End with one short, friendly line.")
    return ("Give the user their morning briefing as a few short spoken sentences, no lists and no questions. " + " ".join(f"({i}) {s}" for i, s in enumerate(steps, 1)))


def build_briefing_tools(store: TaskStore, scheduler: Scheduler, confirm: Callable[[str], bool], clock: Callable[[], datetime] = datetime.now) -> list:
    from .agent_tools import _obj, _str
    from .tools import Tool

    def set_up_morning_briefing(time: str = "07:30", place: str = "", weekdays_only: bool = False, include_calendar: bool = True) -> str:
        try:
            task = make_task(BRIEFING_NAME, briefing_prompt(place, include_calendar), "do", "weekdays" if weekdays_only else "daily", time, now=clock())
        except ValueError as e:
            return f"Error: {e}."
        old = store.find(BRIEFING_NAME)
        replace = " (it replaces the one you have now)" if old is not None else ""
        if not confirm(f"Set up a morning briefing {describe(task)}{replace}? It says the date" + (f", the weather for {place.strip()}" if place.strip() else "")
                       + (" and what is on your phone's calendar." if include_calendar else ".")):
            return "The user declined this action."
        if old is not None:
            store.remove(old.id)
        try:
            store.add(task)
        except ValueError as e:
            return f"Error: {e}."
        scheduler.on_change()
        return f"The morning briefing is set {describe(task)}. It appears in the Tasks tab, where it can be paused or deleted."

    return [
        Tool("set_up_morning_briefing",
             "Set up (or change) a daily spoken morning briefing: the date, the weather for a place, and today's calendar events from the user's phone. time is 24-hour HH:MM. "
             "Ask the user for the time and their city if they have not said.",
             _obj({"time": _str, "place": _str, "weekdays_only": {"type": "boolean"}, "include_calendar": {"type": "boolean"}}), set_up_morning_briefing),
    ]
