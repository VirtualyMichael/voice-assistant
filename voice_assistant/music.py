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


def app_launcher(run=subprocess.run) -> list[str] | None:
    """Command that starts the installed YouTube Music app: read from its Start-menu shortcut (it holds the app id and browser profile)."""
    ps = ("$sh = New-Object -ComObject WScript.Shell; "
          "$root = Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'Microsoft\\Windows\\Start Menu\\Programs'; "
          "Get-ChildItem $root -Recurse -Filter 'YouTube Music*.lnk' -ErrorAction SilentlyContinue | "
          "ForEach-Object { $l = $sh.CreateShortcut($_.FullName); $l.TargetPath + '|' + $l.Arguments }")
    try:
        out = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        target, _, args = line.strip().partition("|")
        if "--app-id" in args and Path(target).exists():
            return [target, *args.split()]
    return None


def open_users_youtube_music() -> None:
    """Start the user's own YouTube Music (their installed app, signed in; else their default browser)."""
    cmd = app_launcher()
    if cmd:
        subprocess.Popen(cmd)
    else:
        import webbrowser

        webbrowser.open("https://music.youtube.com")


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


def is_playing(expected: str = "") -> bool:
    """Something is playing; when `expected` (a song title) is given, it must be that song, not whatever was on before."""
    from .extras import media_session

    now = (media_session("status") or "").lower()
    return "playing" in now and (not expected or expected.lower()[:15] in now)


def press_play(hwnd: int) -> None:
    """A new window opened by a link often waits with the song loaded but paused (browsers block autoplay): bring it forward and press Space,
    YouTube Music's play/pause key."""
    from .screen import focus_window

    focus_window(hwnd)
    time.sleep(0.5)
    import keyboard

    keyboard.send("space")


def play_in_ytm(video_id: str, windows, launch, close, wait=time.sleep, timeout: float = 12.0, clock=time.time,
                foreground=foreground_window, playing=is_playing, nudge=press_play, expected: str = "") -> str:
    """Open the song through the app. Chrome may start a new window or reuse the open one, so afterwards keep one YouTube Music window
    (the one in front, which is the one that took the link), close the rest, and press play if the song is loaded but paused."""
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
    wait(1.5)  # let the page load
    fg = foreground()
    new = [h for h in now if h not in before]
    keep = new[-1] if new else (fg if fg in now else now[-1])  # a new window is the one holding the song; with none, Chrome reused the open one
    stuck = [h for h in now if h != keep and not close(h)]
    closed = len(now) - 1 - len(stuck)
    started = playing(expected)
    if not started:
        nudge(keep)
        wait(2.0)
        started = playing(expected)
    return (("Playing in YouTube Music" if started else "Opened the song in YouTube Music, but it did not start by itself; the user may need to press play")
            + (f"; closed {closed} extra window(s)" if closed else "") + "."
            + (f" {len(stuck)} extra window(s) would not close; the user can close them." if stuck else ""))


def build_music_tools(cfg, confirm, windows, launcher=app_launcher, resolver=resolve_song, close=close_window, wait=time.sleep,
                      foreground=foreground_window, playing=is_playing, nudge=press_play, in_window=None, browser_play=None) -> list[Tool]:
    from .agent_tools import _obj, _str

    def play_music(query: str) -> str:
        """query like 'Maps by Maroon 5'."""
        browser_note = ""
        if browser_play is not None:  # the reliable route: search and click play in the page itself
            try:
                out = browser_play(query)
                if out.startswith(("Playing", "Opened", "Could not find")):  # "Opened ... press play" is a final answer too: do not start it a second time elsewhere
                    return out
                browser_note = f"(The assistant's YouTube Music window said: {out[:160]}) "
            except Exception as e:
                browser_note = f"(The assistant's YouTube Music window could not do it: {str(e)[:160]}) "
        if in_window is not None and ytm_windows(windows()):
            out = in_window(query)  # YouTube Music is already open: search and play inside that window, like a person would
            if out.startswith("Done"):
                return out
            note = f"(Tried it inside the open window first: {out[:200]}) "
        else:
            note = ""
        note = browser_note + note
        found = resolver(query)
        if not found:
            return f"{note}Could not find a song for '{query}'. Ask the user for the title and artist."
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

        result = play_in_ytm(vid, windows, launch, close, wait, foreground=foreground, playing=playing, nudge=nudge, expected=label.split(' by ')[0])
        if not cmd:
            result = "The YouTube Music app was not found on this PC, so the song was opened in the web browser instead. " + result
        return f"{note}{label}: {result}"

    return [Tool("play_music", "Play a song in YouTube Music from a spoken request such as 'play Maps by Maroon 5'. If YouTube Music is already open it searches and plays inside that window (when computer control is on); otherwise it opens the song in the app. "
                 "Use this instead of launch_app, web_search and open_url for playing songs.", _obj({"query": _str}, ["query"]), play_music)]
