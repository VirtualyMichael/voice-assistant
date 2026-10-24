"""Things the assistant can do on the user's phone: alarms, timers and calendar events (texts, calls and notifications come later).

The PC only asks (phone_action messages, see remote.py). The phone shows each request to the user and does nothing unless they agree; for a
calendar event it opens the phone's own calendar with the event filled in, so the user saves it there. Arguments are checked here first, so a
confused model cannot send the phone anything malformed.
"""
from __future__ import annotations

import re
from datetime import datetime

from .tools import Tool

_WHY = {
    "no_phone": "No phone is connected. Ask the user to open the Voice Assistant app on their phone.",
    "timeout": "The user did not answer on their phone in time, so nothing was done.",
    "interrupted": "The user interrupted, so nothing was done.",
    "disconnected": "The phone disconnected before answering, so nothing was done.",
    "declined": "The user said no on their phone, so nothing was done.",
}
ACTION_NAMES = ("phone_set_alarm", "phone_set_timer", "phone_add_calendar_event")


def _hhmm(text: str) -> tuple[int, int]:
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\s*", str(text).lower())
    if not m:
        raise ValueError("Give the time like 07:30 or 7:30 pm.")
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
    if ap:
        if not 1 <= h <= 12:
            raise ValueError("With am or pm the hour is 1 to 12.")
        h = h % 12 + (12 if ap == "pm" else 0)
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        raise ValueError("That is not a time of day.")
    return h, mi


def _when(text: str) -> datetime:
    try:
        return datetime.fromisoformat(str(text).strip())
    except ValueError:
        raise ValueError("Give the date and time as YYYY-MM-DDTHH:MM, for example 2026-11-02T15:00.") from None


DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def build_phone_action_tools(get_remote) -> list[Tool]:
    from .agent_tools import _obj, _str

    def ask(action: str, args: dict) -> str:
        remote = get_remote()
        if remote is None:
            return _WHY["no_phone"]
        ok, message = remote.request_action(action, args)
        if ok:
            return message or "Done on the phone."
        return _WHY.get(message, f"It did not work on the phone: {message}.")

    def phone_set_alarm(time: str, label: str = "", days: list | None = None) -> str:
        h, m = _hhmm(time)
        repeat = [d for d in (str(x).lower()[:3] for x in (days or [])) if d in DAYS]
        return ask("set_alarm", {"hour": h, "minute": m, "label": str(label)[:60], "days": repeat})

    def phone_set_timer(seconds: float, label: str = "") -> str:
        s = int(float(seconds))
        if not 1 <= s <= 24 * 3600:
            return "Error: a phone timer is 1 second to 24 hours."
        return ask("set_timer", {"seconds": s, "label": str(label)[:60]})

    def phone_add_calendar_event(title: str, start: str, end: str = "", location: str = "", notes: str = "", all_day: bool = False) -> str:
        if not str(title).strip():
            return "Error: the event needs a title."
        begin = _when(start)
        finish = _when(end) if end else None
        if finish is not None and finish <= begin:
            return "Error: the end must be after the start."
        args = {"title": str(title)[:120], "start": begin.isoformat(timespec="minutes"), "end": finish.isoformat(timespec="minutes") if finish else "",
                "location": str(location)[:200], "notes": str(notes)[:1000], "all_day": bool(all_day)}
        return ask("add_event", args)

    return [
        Tool("phone_set_alarm",
             "Set an alarm on the user's phone (it rings even if the PC is off). time is the time of day, e.g. '07:30' or '6:45 am'. days repeats it on those "
             "weekdays (mon..sun); leave empty for once. The user confirms on the phone. It only creates a new alarm: it cannot list or check alarms, "
             "so never call it to answer a question about alarms.",
             _obj({"time": _str, "label": _str, "days": {"type": "array", "items": _str}}, ["time"]), phone_set_alarm),
        Tool("phone_set_timer",
             "Start a countdown timer in the phone's clock app (use this when the user is away from the PC or asks for it on their phone; set_timer speaks at the PC). "
             "The user confirms on the phone.", _obj({"seconds": {"type": "number"}, "label": _str}, ["seconds"]), phone_set_timer),
        Tool("phone_add_calendar_event",
             "Add an event to the calendar on the user's phone. start and end are local date and time as YYYY-MM-DDTHH:MM (call get_time first if you need today's date). "
             "The phone's calendar opens with the event filled in and the user saves it there.",
             _obj({"title": _str, "start": _str, "end": _str, "location": _str, "notes": _str, "all_day": {"type": "boolean"}}, ["title", "start"]),
             phone_add_calendar_event),
    ]


PHONE_ACTIONS_PROMPT = (
    " On the user's phone you can set alarms (phone_set_alarm), start timers (phone_set_timer) and add calendar events (phone_add_calendar_event); "
    "the user confirms each one on the phone, so just call the tool. Things the user shares from their phone arrive as attachments: use the attachment tools on them."
)
