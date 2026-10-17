"""Chunked text-to-speech playback: synthesis and playback overlap on separate threads."""
from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

Synth = Callable[[str], "tuple[np.ndarray, int]"]
# player(samples, sample_rate, cancel_event, on_start) blocks until finished or cancelled
Player = Callable[[np.ndarray, int, threading.Event, Callable[[], None]], None]


def sounddevice_player(samples: np.ndarray, sr: int, cancel: threading.Event, on_start: Callable[[], None]) -> None:
    import sounddevice as sd

    block = max(sr // 20, 1)  # 50 ms blocks so cancel takes effect quickly
    with sd.OutputStream(samplerate=sr, channels=1, dtype="float32") as out:
        on_start()
        for i in range(0, len(samples), block):
            if cancel.is_set():
                return
            out.write(np.ascontiguousarray(samples[i : i + block], dtype=np.float32).reshape(-1, 1))


class Speaker:
    def __init__(self, synth: Synth, player: Player = sounddevice_player):
        self._synth, self._player = synth, player
        self._cond = threading.Condition()
        self._gen = 0
        self._pending = 0
        self._cancel = threading.Event()
        self._on_first: Callable[[], None] | None = None
        self._text_q: queue.Queue = queue.Queue()
        self._audio_q: queue.Queue = queue.Queue(maxsize=4)
        for fn in (self._synth_loop, self._play_loop):
            threading.Thread(target=fn, daemon=True).start()

    def begin_turn(self, on_first_audio: Callable[[], None]) -> None:
        with self._cond:
            self._on_first = on_first_audio

    def say(self, text: str) -> None:
        with self._cond:
            self._pending += 1
            self._text_q.put((self._gen, text))

    def play_samples(self, samples: np.ndarray, sr: int) -> None:
        """Queue ready-made audio (a chime) behind any speech, skipping synthesis."""
        with self._cond:
            self._pending += 1
            gen = self._gen
        self._audio_q.put((gen, samples, sr))

    def stop(self) -> None:
        """Drop queued chunks and cut off the one playing."""
        with self._cond:
            self._gen += 1
            self._cancel.set()
            self._cancel = threading.Event()
            self._on_first = None

    def wait_idle(self, timeout: float | None = None) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: self._pending == 0, timeout)

    def _done(self) -> None:
        with self._cond:
            self._pending -= 1
            self._cond.notify_all()

    def _current(self, gen: int) -> bool:
        with self._cond:
            return gen == self._gen

    def _synth_loop(self) -> None:
        while True:
            gen, text = self._text_q.get()
            if not self._current(gen):
                self._done()
                continue
            try:
                samples, sr = self._synth(text)
            except Exception:
                log.exception("TTS failed for %r", text)
                self._done()
                continue
            self._audio_q.put((gen, samples, sr))

    def _fire_first(self) -> None:
        with self._cond:
            cb, self._on_first = self._on_first, None
        if cb:
            cb()

    def _play_loop(self) -> None:
        while True:
            gen, samples, sr = self._audio_q.get()
            with self._cond:
                live, cancel = gen == self._gen, self._cancel
            try:
                if live:
                    self._player(samples, sr, cancel, self._fire_first)
            except Exception:
                log.exception("Audio playback failed")
            finally:
                self._done()
