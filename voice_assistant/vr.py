"""Detects a running VR runtime (SteamVR by default) by polling the process list."""
from __future__ import annotations

import threading
from typing import Callable, Iterable

DEFAULT_PROCESSES = ("vrserver.exe", "vrmonitor.exe")


def vr_running(processes: Iterable[str] = DEFAULT_PROCESSES, lister: Callable[[], Iterable[str]] | None = None) -> bool:
    """True if any watched process is running. A scan takes ~10 ms, so polling every few seconds is cheap."""
    if lister is None:
        import psutil

        def lister() -> Iterable[str]:
            return (p.info["name"] or "" for p in psutil.process_iter(["name"]))

    wanted = {p.lower() for p in processes}
    return any(n.lower() in wanted for n in lister())


class VrWatcher:
    """Calls on_change(active) when the VR state changes; needs two matching polls to avoid flapping."""

    def __init__(
        self,
        on_change: Callable[[bool], None],
        processes: Iterable[str] = DEFAULT_PROCESSES,
        interval_s: float = 3.0,
        probe: Callable[[], bool] | None = None,
    ):
        self.on_change, self.interval = on_change, interval_s
        procs = tuple(processes)
        self._probe = probe or (lambda: vr_running(procs))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.active = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.active:  # leaving auto-switch mode returns to the normal devices
            self.active = False
            self.on_change(False)

    def _run(self) -> None:
        pending: bool | None = None
        while not self._stop.is_set():
            try:
                now = self._probe()
            except Exception:
                now = self.active
            if now == self.active:
                pending = None
            elif pending == now:
                self.active, pending = now, None
                self.on_change(now)
            else:
                pending = now
            self._stop.wait(self.interval)
