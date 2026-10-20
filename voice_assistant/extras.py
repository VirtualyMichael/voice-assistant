"""Everyday helpers: media and volume keys, weather, persistent notes, and user-defined routines ("good night" = several actions)."""
from __future__ import annotations

import json
import time
from datetime import datetime

import httpx

from .config import config_dir
from .tools import Tool

VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2, "mute": 0xAD, "volume_up": 0xAF, "volume_down": 0xAE}
KEYEVENTF_KEYUP = 2
WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
       61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow", 73: "snow", 75: "heavy snow", 80: "rain showers", 81: "rain showers",
       82: "heavy rain showers", 95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail"}


def press(key: str, times: int = 1) -> None:
    import ctypes

    for _ in range(times):
        ctypes.windll.user32.keybd_event(VK[key], 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK[key], 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.01)


def weather(place: str, client: httpx.Client | None = None) -> str:
    """Current conditions and today's range from Open-Meteo (no account or key). Sends the place name to open-meteo.com."""
    http = client or httpx.Client(timeout=10)
    geo = http.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": place, "count": 1}).json().get("results")
    if not geo:
        return f"I couldn't find a place called '{place}'."
    g = geo[0]
    w = http.get("https://api.open-meteo.com/v1/forecast", params={
        "latitude": g["latitude"], "longitude": g["longitude"], "current": "temperature_2m,weather_code,wind_speed_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max", "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph", "timezone": "auto", "forecast_days": 1}).json()
    c, d = w["current"], w["daily"]
    return (f"{g['name']}: {round(c['temperature_2m'])}°F and {WMO.get(c['weather_code'], 'mixed conditions')}, wind {round(c['wind_speed_10m'])} mph. "
            f"Today {round(d['temperature_2m_min'][0])} to {round(d['temperature_2m_max'][0])}°F, {d['precipitation_probability_max'][0]}% chance of precipitation.")


class Notes:
    """Things the user asked the assistant to remember, kept on this PC (notes.json)."""

    def __init__(self, path=None):
        self.path = path or config_dir() / "notes.json"

    def _load(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def add(self, text: str) -> str:
        notes = self._load() + [{"text": text.strip(), "at": datetime.now().strftime("%Y-%m-%d %H:%M")}]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(notes, indent=1), encoding="utf-8")
        return f"Saved note #{len(notes)}."

    def listing(self, query: str = "") -> str:
        q = query.strip().lower()
        rows = [f"#{i} ({n['at']}) {n['text']}" for i, n in enumerate(self._load(), 1) if q in n["text"].lower()]
        return "\n".join(rows) or "No matching notes."

    def remove(self, number: int) -> str:
        notes = self._load()
        if not 1 <= number <= len(notes):
            return "No such note."
        gone = notes.pop(number - 1)
        self.path.write_text(json.dumps(notes, indent=1), encoding="utf-8")
        return f"Deleted: {gone['text']}"


def build_extra_tools(cfg, confirm, call_tool, notes: Notes | None = None, keys=press) -> list[Tool]:
    """call_tool(name, json_args) runs another registered tool (used by routines)."""
    from .agent_tools import _obj, _str

    notes = notes or Notes()
    declined = "The user declined this action."

    def media(action: str) -> str:
        if action not in ("play_pause", "next", "previous", "stop"):
            return "Error: action must be play_pause, next, previous or stop."
        keys(action)
        return f"Sent {action.replace('_', '/')}."

    def volume(action: str, percent: int | None = None) -> str:
        if percent is not None:  # each key press moves Windows volume by 2%
            keys("volume_down", 50)
            keys("volume_up", max(0, min(100, int(percent))) // 2)
            return f"Volume set to about {max(0, min(100, int(percent)))}%."
        if action not in ("up", "down", "mute"):
            return "Error: action must be up, down or mute (or give a percent)."
        keys({"up": "volume_up", "down": "volume_down", "mute": "mute"}[action], 5 if action != "mute" else 1)
        return {"up": "Volume up.", "down": "Volume down.", "mute": "Toggled mute."}[action]

    def run_routine(name: str) -> str:
        steps = cfg.routines.get(name.strip().lower())
        if steps is None:
            return "No such routine. Known routines: " + (", ".join(cfg.routines) or "none") + "."
        if cfg.confirm_routines and not confirm(f"Run routine '{name}'?\n\n" + "\n".join(s.get("tool", "?") for s in steps)):
            return declined
        out = []
        for s in steps:
            if s.get("tool") == "run_routine":
                continue  # no routines calling routines
            out.append(f"{s.get('tool')}: {call_tool(s.get('tool', ''), json.dumps(s.get('args', {})))}")
        return "\n".join(out) or "The routine has no steps."

    tools = [
        Tool("media_control", "Control music/video playback on this PC: play_pause, next, previous, stop.", _obj({"action": _str}, ["action"]), media),
        Tool("set_volume", "Change PC volume: action up/down/mute, or give percent (0-100) to set it.", _obj({"action": _str, "percent": {"type": "integer"}}), volume),
        Tool("remember_note", "Save something the user wants remembered (stored on this PC).", _obj({"text": _str}, ["text"]), notes.add),
        Tool("list_notes", "Search or list the user's saved notes.", _obj({"query": _str}), notes.listing),
        Tool("delete_note", "Delete a saved note by its number.", _obj({"number": {"type": "integer"}}, ["number"]), lambda number: notes.remove(int(number))),
    ]
    if cfg.routines:
        tools.append(Tool("run_routine", "Run one of the user's saved routines: " + ", ".join(cfg.routines) + ".", _obj({"name": _str}, ["name"]), run_routine))
    if cfg.allow_web:
        tools.append(Tool("get_weather", "Current weather and today's forecast for a place (city name).", _obj({"place": _str}, ["place"]), weather))
    return tools
