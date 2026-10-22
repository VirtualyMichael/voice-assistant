"""Everyday helpers: media and volume keys, weather, persistent notes, and user-defined routines ("good night" = several actions)."""
from __future__ import annotations

import json
import subprocess
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


SMTC_SCRIPT = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType=WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$all = @($mgr.GetSessions())
if ($all.Count -eq 0) { 'NONE'; exit }
$s = $mgr.GetCurrentSession()
if ($null -eq $s) { $s = $all[0] }
$status = [string]$s.GetPlaybackInfo().PlaybackStatus
$act = $env:VA_MEDIA
$ok = $true
if ($act -eq 'play') { if ($status -ne 'Playing') { $ok = Await ($s.TryPlayAsync()) ([bool]) } }
elseif ($act -eq 'pause') { if ($status -eq 'Playing') { $ok = Await ($s.TryPauseAsync()) ([bool]) } }
elseif ($act -eq 'toggle') { $ok = Await ($s.TryTogglePlayPauseAsync()) ([bool]) }
elseif ($act -eq 'next') { $ok = Await ($s.TrySkipNextAsync()) ([bool]) }
elseif ($act -eq 'previous') { $ok = Await ($s.TrySkipPreviousAsync()) ([bool]) }
elseif ($act -eq 'stop') { $ok = Await ($s.TryStopAsync()) ([bool]) }
Start-Sleep -Milliseconds 400
$now = [string]$s.GetPlaybackInfo().PlaybackStatus
$p = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
"{0}|{1}|{2}|{3}|{4}|{5}" -f $ok, $status, $now, $s.SourceAppUserModelId, $p.Title, $p.Artist
"""

PLAY_ALIASES = {"play": "play", "resume": "play", "start": "play", "unpause": "play", "pause": "pause", "hold": "pause",
                "play_pause": "toggle", "toggle": "toggle", "playpause": "toggle", "stop": "stop",
                "next": "next", "skip": "next", "forward": "next", "next_track": "next",
                "previous": "previous", "prev": "previous", "back": "previous", "last": "previous", "previous_track": "previous"}
KEY_FOR = {"play": "play_pause", "pause": "play_pause", "toggle": "play_pause", "stop": "stop", "next": "next", "previous": "previous"}


def media_session(action: str, run=subprocess.run) -> str | None:
    """Control the music app through Windows' media session (the same thing the keyboard media keys talk to) and report what really happened.
    None when no app is playing/paused media, or the lookup failed."""
    try:
        p = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", SMTC_SCRIPT], capture_output=True, text=True, timeout=15,
                env={**__import__("os").environ, "VA_MEDIA": action})
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (p.stdout or "").strip().splitlines()
    if not out or out[-1].strip() == "NONE" or out[-1].count("|") < 5:
        return None
    ok, before, after, app, title, artist = (out[-1].split("|") + [""] * 6)[:6]
    app = app.split("!")[0].split("_")[0].replace(".exe", "")
    what = f"{title} by {artist}" if title and artist else title or "something"
    if ok.lower() != "true":
        return f"{app} did not accept that command (it is {after.lower()})."
    return f"{app} is now {after.lower()}: {what}."


BROWSERS = ("chrome", "msedge", "brave", "firefox", "opera", "vivaldi")
APP_ALIASES = {"youtubemusic": BROWSERS + ("youtubemusic",), "youtube": BROWSERS, "ytmusic": BROWSERS + ("youtubemusic",), "browser": BROWSERS,
               "music": BROWSERS + ("spotify", "zunemusic", "applemusic", "itunes"), "edge": ("msedge",), "googlechrome": ("chrome",)}


class Audio:
    """Windows volume: the master volume, and each app's own level in the Volume Mixer (what lowering 'YouTube Music' there does)."""

    def _speakers(self):
        from pycaw.pycaw import AudioUtilities

        return AudioUtilities.GetSpeakers().EndpointVolume

    def master(self) -> float:
        return float(self._speakers().GetMasterVolumeLevelScalar())

    def set_master(self, level: float) -> None:
        self._speakers().SetMasterVolumeLevelScalar(max(0.0, min(1.0, level)), None)

    def toggle_mute(self) -> bool:
        ev = self._speakers()
        muted = not bool(ev.GetMute())
        ev.SetMute(muted, None)
        return muted

    def sessions(self) -> list[dict]:
        from pycaw.pycaw import AudioUtilities

        out = []
        for s in AudioUtilities.GetAllSessions():
            p = s.Process
            if p is None or s.SimpleAudioVolume is None:
                continue
            vol = s.SimpleAudioVolume
            out.append({"name": p.name().rsplit(".", 1)[0], "active": s.State == 1, "get": vol.GetMasterVolume,
                        "set": (lambda v, vol=vol: vol.SetMasterVolume(max(0.0, min(1.0, v)), None))})
        return out


def match_sessions(sessions: list[dict], app: str) -> list[dict]:
    key = "".join(app.lower().split()).replace("-", "").replace(".exe", "")
    wanted = set(APP_ALIASES.get(key, ())) | {key}
    hits = [s for s in sessions if any(w and (w in s["name"].lower() or s["name"].lower() in w) for w in wanted)]
    return [s for s in hits if s["active"]] or hits  # the ones making sound now, else all of that app's


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


ROUTINE_TOOL_NAMES = ("run_routine", "list_routines", "save_routine", "delete_routine")
NOT_IN_ROUTINES = {"run_routine", "save_routine", "delete_routine", "list_routines", "run_powershell", "delegate_task", "computer_task", "control_click", "control_type",
                   "control_keys", "control_scroll", "control_focus", "write_clipboard", "write_text_file"}  # a routine must not hide a shell command, a delegated job or a computer-control session in a phrase


def build_extra_tools(cfg, confirm, call_tool, notes: Notes | None = None, keys=press, session=media_session, audio=None, known_tools=lambda: [], persist=lambda: None) -> list[Tool]:
    """call_tool(name, json_args) runs another registered tool (used by routines); known_tools() lists the tool names a routine step may use; persist() saves the settings."""
    from .agent_tools import _obj, _str

    notes = notes or Notes()
    audio = audio or Audio()
    declined = "The user declined this action."

    def media(action: str) -> str:
        act = PLAY_ALIASES.get(action.strip().lower().replace(" ", "_"))
        if act is None:
            return "Error: action must be play, pause, play_pause, next, previous or stop."
        result = session(act)
        if result:
            return result
        keys(KEY_FOR[act])  # no app reports a media session: fall back to the keyboard media key
        return ("No music app is reporting playback to Windows, so I sent the media key, which may do nothing. "
                "Tell the user to open their player and start a song once, then try again.")

    def volume(action: str = "", percent: int | None = None, app: str = "") -> str:
        act = action.strip().lower()
        act = {"raise": "up", "louder": "up", "increase": "up", "lower": "down", "quieter": "down", "decrease": "down", "unmute": "mute"}.get(act, act)
        try:
            if app.strip():  # one app's level in the Windows Volume Mixer (YouTube Music runs inside the browser, so its process is the browser)
                hits = match_sessions(audio.sessions(), app)
                if not hits:
                    heard = sorted({s["name"] for s in audio.sessions() if s["active"]})
                    return f"No app matching '{app}' is playing sound right now. Apps making sound: {', '.join(heard) or 'none'}."
                now = hits[0]["get"]()
                target = (int(percent) / 100 if percent is not None else now + (0.1 if act == "up" else -0.1 if act == "down" else 0))
                target = max(0.0, min(1.0, target))
                for h in hits:
                    h["set"](target)
                return f"{app} volume set to {round(target * 100)}% (its own level in the Windows mixer; the master volume is unchanged)."
            if percent is not None:
                audio.set_master(max(0, min(100, int(percent))) / 100)
                return f"Volume set to {max(0, min(100, int(percent)))}%."
            if act in ("up", "down"):
                now = audio.master()
                audio.set_master(now + (0.1 if act == "up" else -0.1))
                return f"Volume {'up' if act == 'up' else 'down'} to {round(max(0.0, min(1.0, now + (0.1 if act == 'up' else -0.1))) * 100)}%."
            if act == "mute":
                return "Muted." if audio.toggle_mute() else "Unmuted."
        except Exception:
            if percent is not None:  # the audio API failed: fall back to the keyboard volume keys (2% each)
                keys("volume_down", 50)
                keys("volume_up", max(0, min(100, int(percent))) // 2)
                return f"Volume set to about {max(0, min(100, int(percent)))}%."
            if act in ("up", "down", "mute"):
                keys({"up": "volume_up", "down": "volume_down", "mute": "mute"}[act], 5 if act != "mute" else 1)
                return {"up": "Volume up.", "down": "Volume down.", "mute": "Toggled mute."}[act]
        return "Error: give a percent (0-100), or an action: up, down or mute. Add app (e.g. YouTube Music) for one app's volume."

    def find_routine(name: str) -> str | None:
        """The saved routine this spoken name means ('goodnight', 'Good night!' and 'good night' are one), or None."""
        from .ytm_browser import compact, similarity

        want = compact(name)
        for key in cfg.routines:
            if compact(key) == want:
                return key
        near = sorted(((similarity(name, key), key) for key in cfg.routines), reverse=True)
        return near[0][1] if near and near[0][0] >= 0.85 and (len(near) == 1 or near[1][0] < near[0][0] - 0.05) else None

    def run_routine(name: str) -> str:
        key = find_routine(name)
        if key is None:
            return "No such routine. Known routines: " + (", ".join(cfg.routines) or "none") + "."
        steps = cfg.routines[key]
        if cfg.confirm_routines and not confirm(f"Run routine '{key}'?\n\n" + "\n".join(s.get("tool", "?") for s in steps)):
            return declined
        out = []
        for s in steps:
            if s.get("tool") in NOT_IN_ROUTINES:
                continue  # no routines calling routines, and nothing hidden inside one
            out.append(f"{s.get('tool')}: {call_tool(s.get('tool', ''), json.dumps(s.get('args', {})))}")
        return "\n".join(out) or "The routine has no steps."

    def list_routines() -> str:
        if not cfg.routines:
            return "No routines saved yet. The user can ask you to save one, e.g. 'make a good night routine that turns the lights off and pauses the music'."
        return "\n".join(f"{name}: " + ", ".join(f"{s.get('tool')}({json.dumps(s.get('args', {}))})" for s in steps) for name, steps in cfg.routines.items())

    def save_routine(name: str, steps: str) -> str:
        """steps: a JSON list like [{"tool": "set_lights", "args": {"on": false}}, {"tool": "media_control", "args": {"action": "pause"}}]."""
        key = " ".join(name.lower().split())
        if not key or len(key) > 60:
            return "Error: give the routine a short name."
        try:
            parsed = json.loads(steps) if isinstance(steps, str) else steps
            assert isinstance(parsed, list) and parsed and all(isinstance(s, dict) and isinstance(s.get("tool"), str) and isinstance(s.get("args", {}), dict) for s in parsed)
        except Exception:
            return 'Error: steps must be a JSON list like [{"tool": "set_lights", "args": {"on": false}}].'
        known = set(known_tools())
        bad = [s["tool"] for s in parsed if s["tool"] in NOT_IN_ROUTINES or (known and s["tool"] not in known)]
        if bad:
            return f"Error: these cannot be routine steps: {', '.join(dict.fromkeys(bad))}. Use tools such as set_lights, set_volume, media_control, play_music, vrchat_set, set_timer."
        listing = "\n".join(f"{s['tool']} {json.dumps(s.get('args', {}))}" for s in parsed)
        replacing = key in cfg.routines
        if not confirm(f"{'Replace' if replacing else 'Save'} the routine '{key}'?\n\n{listing}"):
            return declined
        cfg.routines[key] = [{"tool": s["tool"], "args": s.get("args", {})} for s in parsed]
        persist()
        return f"Saved routine '{key}' with {len(parsed)} step(s). Say its name to run it."

    def delete_routine(name: str) -> str:
        key = find_routine(name)
        if key is None:
            return "No such routine. Known routines: " + (", ".join(cfg.routines) or "none") + "."
        if not confirm(f"Delete the routine '{key}'?"):
            return declined
        del cfg.routines[key]
        persist()
        return f"Deleted routine '{key}'."

    tools = [
        Tool("media_control", "Control music/video playback on this PC: play, pause, play_pause, next, previous, stop. Reports what the player actually did.", _obj({"action": _str}, ["action"]), media),
        Tool("set_volume", "Set volume once: give percent (0-100) to set it, or action up/down/mute. Add app (for example 'YouTube Music' or 'Spotify') to change only that app's volume; otherwise it changes the whole PC. Call it a single time per request.", _obj({"action": _str, "percent": {"type": "integer"}, "app": _str}), volume),
        Tool("remember_note", "Save something the user wants remembered (stored on this PC).", _obj({"text": _str}, ["text"]), notes.add),
        Tool("list_notes", "Search or list the user's saved notes.", _obj({"query": _str}), notes.listing),
        Tool("delete_note", "Delete a saved note by its number.", _obj({"number": {"type": "integer"}}, ["number"]), lambda number: notes.remove(int(number))),
    ]
    tools += [
        Tool("run_routine", "Run one of the user's saved routines (several actions under one phrase, like 'good night'). Call list_routines to see them.", _obj({"name": _str}, ["name"]), run_routine),
        Tool("list_routines", "List the user's saved routines and their steps.", _obj({}), list_routines),
        Tool("save_routine", "Save a routine the user describes ('make a gaming mode routine that dims the lights and sets YouTube Music to 20%'). steps is a JSON list of {\"tool\": name, \"args\": {...}} using the other tools "
             "(set_lights, set_volume, media_control, play_music, vrchat_set, ...). The user confirms before it is saved.", _obj({"name": _str, "steps": _str}, ["name", "steps"]), save_routine),
        Tool("delete_routine", "Delete a saved routine by name (the user confirms).", _obj({"name": _str}, ["name"]), delete_routine),
    ]
    if cfg.allow_web:
        tools.append(Tool("get_weather", "Current weather and today's forecast for a place (city name).", _obj({"place": _str}, ["place"]), weather))
    return tools
