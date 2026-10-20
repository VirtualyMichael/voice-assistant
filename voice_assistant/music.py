"""Play a song in YouTube Music. Finds the song, opens it in a fresh YouTube Music app window and closes the old one.

Why a new window: the installed YouTube Music app has no address bar and nothing outside it can navigate an existing window, but opening the song's link
through the app's own shortcut starts playing it straight away. The old window is then closed (YouTube Music asks "Leave app?", which is answered for you)."""
from __future__ import annotations

import ctypes
import os
import subprocess
import time
from pathlib import Path

from .tools import Tool

BROWSERS = ("chrome", "msedge", "brave", "vivaldi", "opera")
WATCH = "https://music.youtube.com/watch?v="


def resolve_song(query: str) -> tuple[str, str] | None:
    """(video id, 'Title by Artist') for the best matching song, or None. Uses YouTube Music's own search when available, else a web search."""
    try:
        from ytmusicapi import YTMusic

        for kind in ("songs", "videos"):
            hits = YTMusic().search(query, filter=kind, limit=3)
            if hits:
                h = hits[0]
                artists = ", ".join(a["name"] for a in h.get("artists") or [])
                return h["videoId"], f"{h['title']} by {artists}" if artists else h["title"]
    except Exception:
        pass
    try:
        from ddgs import DDGS

        for r in DDGS().text(f"{query} site:music.youtube.com", max_results=5):
            href = r.get("href", "")
            if "watch?v=" in href:
                return href.split("watch?v=")[1].split("&")[0], r.get("title", query)
    except Exception:
        pass
    return None


def is_ytm_window(title: str, exe: str) -> bool:
    t = title.lower()
    return exe.lower() in BROWSERS and "youtube music" in t and "google chrome" not in t and "microsoft edge" not in t  # app windows, not browser tabs


def ytm_windows(windows) -> list[int]:
    return [h for h, t, e in windows if is_ytm_window(t, e)]


def app_launcher() -> list[str] | None:
    """Command that starts the installed YouTube Music app: read from its Start-menu shortcut (it holds the app id and browser profile)."""
    ps = (r"$sh = New-Object -ComObject WScript.Shell; Get-ChildItem \"$env:APPDATA\Microsoft\Windows\Start Menu\Programs\" -Recurse -Filter 'YouTube Music*.lnk' "
          r"-ErrorAction SilentlyContinue | Select-Object -First 1 | ForEach-Object { $l = $sh.CreateShortcut($_.FullName); $l.TargetPath + '|' + $l.Arguments }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if "|" not in out or "--app-id" not in out:
        return None
    target, args = out.split("|", 1)
    return [target, *args.split()] if Path(target).exists() else None


def close_window(hwnd: int, wait=time.sleep, now=time.time) -> bool:
    """Close a browser app window, answering "Leave app?" with its default button. True when the window is gone."""
    u = ctypes.windll.user32
    u.PostMessageW(hwnd, 0x10, 0, 0)  # WM_CLOSE
    for attempt in range(2):
        wait(0.8)
        if not u.IsWindow(hwnd):
            return True
        if attempt == 0:
            u.keybd_event(0x12, 0, 0, 0)  # Alt tap so we may take focus
            u.keybd_event(0x12, 0, 2, 0)
            u.SetForegroundWindow(hwnd)
            wait(0.4)
            if u.GetForegroundWindow() == hwnd:  # only press Enter when the prompt's window really has focus
                u.keybd_event(0x0D, 0, 0, 0)
                u.keybd_event(0x0D, 0, 2, 0)
    wait(0.8)
    return not u.IsWindow(hwnd)


def foreground_window() -> int:
    return int(ctypes.windll.user32.GetForegroundWindow())


def is_playing() -> bool:
    from .extras import media_session

    return "playing" in (media_session("status") or "").lower()


def play_in_ytm(video_id: str, windows, launch, close, wait=time.sleep, timeout: float = 12.0, clock=time.time,
                foreground=foreground_window, playing=is_playing) -> str:
    """Open the song through the app. Chrome may start a new window or reuse the open one, so afterwards keep one YouTube Music window
    (the one in front, which is the one that took the link) and close the rest. If that stops the music, start the song again."""
    before = set(ytm_windows(windows()))
    launch(WATCH + video_id)
    deadline = clock() + timeout
    now: list[int] = []
    while clock() < deadline:
        wait(0.5)
        now = ytm_windows(windows())
        if set(now) - before:  # a new window: it has the song
            break
    if not now:
        return "Started it, but no YouTube Music window showed up."
    wait(1.0)  # let the song start before anything is closed
    fg = foreground()
    new = [h for h in now if h not in before]
    keep = fg if fg in now else (new[-1] if new else now[-1])
    extras = [h for h in now if h != keep]
    stuck = [h for h in extras if not close(h)]
    note = ""
    if extras and not playing():  # the closed window was the one playing: play it again in the one left
        launch(WATCH + video_id)
        wait(3.0)
        note = " (restarted it after tidying up)"
    done = len(extras) - len(stuck)
    return ("Playing in YouTube Music" + (f"; closed {done} extra window(s)" if done else "") + note + "."
            + (f" {len(stuck)} extra window(s) would not close; the user can close them." if stuck else ""))


def build_music_tools(cfg, confirm, windows, launcher=app_launcher, resolver=resolve_song, close=close_window, wait=time.sleep) -> list[Tool]:
    from .agent_tools import _obj, _str

    def play_music(query: str) -> str:
        """query like 'Maps by Maroon 5'."""
        found = resolver(query)
        if not found:
            return f"Could not find a song for '{query}'. Ask the user for the title and artist."
        vid, label = found
        if cfg.confirm_launch and not confirm(f"Play {label} in YouTube Music?\n\nIt opens in a new YouTube Music window and the old one is closed."):
            return "The user declined this action."
        cmd = launcher()

        def launch(url: str) -> None:
            if cmd:
                subprocess.Popen([*cmd, url])
            else:
                import webbrowser

                webbrowser.open(url)

        result = play_in_ytm(vid, windows, launch, close, wait)
        return f"{label}: {result}"

    return [Tool("play_music", "Play a song in YouTube Music from a spoken request such as 'play Maps by Maroon 5'. Finds the song, opens it and replaces any open YouTube Music window. "
                 "Use this instead of launch_app, web_search and open_url for playing songs.", _obj({"query": _str}, ["query"]), play_music)]
