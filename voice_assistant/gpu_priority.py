"""Keeps the AI's graphics-card work out of a game's way (Windows).

The graphics driver shares the card between programs by scheduling priority. Games and video run at normal priority; the model server
LM Studio starts (llama-server.exe) and the cloned-voice server (chatterbox_server.py) can be lowered so that whenever a game or a video needs
the card it goes first, and the AI uses what is left. This is a priority, not a fixed percentage: with nothing else running the AI still gets
the whole card, and while a game uses all of it the AI slows down instead of the game.
"""
from __future__ import annotations

import logging
import sys
import threading
from typing import Callable, Iterable

log = logging.getLogger(__name__)

# D3DKMT_SCHEDULINGPRIORITYCLASS: 0 idle, 1 below normal, 2 normal (the default). Higher ones need administrator rights and are never used here.
PRIORITIES = {"idle": 0, "below_normal": 1, "normal": 2}
SERVER_NAMES = ("llama-server.exe",)  # what LM Studio runs the model in
SCRIPT_MARKERS = ("chatterbox_server",)  # the cloned-voice server is a python script with this in its command line
PROCESS_SET_INFORMATION = 0x0200


def set_gpu_priority(pid: int, level: str) -> bool:
    """Set one process's graphics scheduling priority. False when Windows refuses or this is not Windows."""
    if sys.platform != "win32" or level not in PRIORITIES:
        return False
    import ctypes
    from ctypes import wintypes

    k32, gdi = ctypes.WinDLL("kernel32", use_last_error=True), ctypes.WinDLL("gdi32")
    k32.OpenProcess.restype = wintypes.HANDLE
    h = k32.OpenProcess(PROCESS_SET_INFORMATION, False, int(pid))
    if not h:
        return False
    try:
        gdi.D3DKMTSetProcessSchedulingPriorityClass.argtypes = [wintypes.HANDLE, ctypes.c_int]
        gdi.D3DKMTSetProcessSchedulingPriorityClass.restype = ctypes.c_long
        return gdi.D3DKMTSetProcessSchedulingPriorityClass(h, PRIORITIES[level]) == 0
    except (AttributeError, OSError):
        return False
    finally:
        k32.CloseHandle(h)


def find_ai_processes(lister: Callable[[], Iterable[tuple[int, str, str]]] | None = None) -> list[int]:
    """Process ids of the model server(s) and the cloned-voice server. `lister` yields (pid, name, command line) and exists for tests."""
    if lister is None:
        import psutil

        def lister():
            for p in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    yield p.info["pid"], p.info["name"] or "", " ".join(p.info["cmdline"] or [])
                except (psutil.Error, OSError):
                    continue

    names = {n.lower() for n in SERVER_NAMES}
    return [pid for pid, name, cmd in lister() if name.lower() in names or any(m in cmd for m in SCRIPT_MARKERS)]


class GpuCourtesy:
    """Applies the priority to the AI's server processes now and every `interval_s` seconds (they are restarted when a model is reloaded)."""

    def __init__(self, level: str = "below_normal", interval_s: float = 20.0, apply: Callable[[int, str], bool] = set_gpu_priority,
                 finder: Callable[[], list[int]] = find_ai_processes):
        self.level, self.interval, self._apply, self._find = level, interval_s, apply, finder
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.lowered: set[int] = set()

    def run_once(self) -> int:
        """Returns how many processes were newly set."""
        if self.level == "normal":
            return 0
        done = 0
        live = set(self._find())
        for pid in live - self.lowered:
            if self._apply(pid, self.level):
                self.lowered.add(pid)
                done += 1
            else:
                log.info("could not lower the graphics priority of process %s", pid)
        self.lowered &= live
        return done

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="gpu-courtesy")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:
                log.exception("graphics priority check failed")
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()
