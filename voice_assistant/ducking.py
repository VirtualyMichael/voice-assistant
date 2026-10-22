"""Quiet the music while the assistant is listening, thinking or speaking, then bring it back.

Only music apps' own levels in the Windows Volume Mixer change (browsers, Spotify, VLC and similar), never the master volume and never this assistant's own sound."""
from __future__ import annotations

import threading

MUSIC_APPS = ("chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "spotify", "vlc", "musicbee", "foobar2000", "itunes", "applemusic", "music.ui", "youtubemusic", "youtube music")
OWN_APPS = ("voiceassistant", "python", "pythonw")


def is_music(name: str) -> bool:
    n = name.lower().replace(".exe", "")
    return n not in OWN_APPS and any(m in n for m in MUSIC_APPS)


class Ducker:
    def __init__(self, audio, level: float = 0.25, enabled=lambda: True, init_com=None):
        self.audio, self.level, self.enabled = audio, level, enabled
        self._saved: list[tuple[dict, float]] = []  # (session, the level it had before we lowered it)
        self._lock = threading.Lock()
        self._init_com = init_com

    def duck(self) -> None:
        with self._lock:
            if self._saved or not self.enabled():
                return
            for s in self.audio.sessions():
                if s["active"] and is_music(s["name"]):
                    now = float(s["get"]())
                    if now > 0.02:
                        self._saved.append((s, now))
                        s["set"](now * self.level)

    def restore(self) -> None:
        with self._lock:
            saved, self._saved = self._saved, []
            for s, level in saved:
                try:
                    s["set"](level)
                except Exception:
                    pass  # the app closed while it was quiet

    def on_state(self, state: str) -> None:
        """Called whenever the assistant's state changes (idle, listening, thinking, speaking). Runs the volume change off the caller's thread."""

        def work() -> None:
            try:
                if self._init_com:
                    self._init_com()
                (self.duck if state != "idle" else self.restore)()
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()
