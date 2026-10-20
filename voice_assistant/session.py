"""Orchestrates one conversation: audio in -> recognition -> agent -> chunked speech out."""
from __future__ import annotations

import logging
import queue
import re
import threading
import time
from enum import Enum
from typing import Callable, Protocol

import numpy as np

from .approval import Approval, parse_yes_no, speakable
from .agent import Agent
from .chunker import SentenceChunker, clean_for_speech
from .config import Config
from .metrics import MetricsLog, TurnMetrics
from .tones import SAMPLE_RATE as TONE_RATE
from .tones import duration, listening_chime

log = logging.getLogger(__name__)


class State(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


class Recognizer(Protocol):
    def accept(self, samples: np.ndarray) -> None: ...
    def text(self) -> str: ...
    def is_endpoint(self) -> bool: ...
    def reset(self) -> None: ...


class SessionEvents:
    """Override what you need; called from worker threads."""

    def state(self, state: State) -> None: ...
    def partial(self, text: str) -> None: ...
    def user_final(self, text: str) -> None: ...
    def assistant_delta(self, text: str) -> None: ...
    def assistant_done(self, metrics: TurnMetrics) -> None: ...
    def error(self, message: str) -> None: ...


def trim_leading_silence(x: np.ndarray, sr: int = 16000, pad_s: float = 0.5) -> np.ndarray:
    """Cut the quiet lead-in off a recording but keep `pad_s` before the first loud frame.

    The fast recognizer only shows text some time after you start talking, so onset is found from energy instead.
    """
    frame = int(0.02 * sr)
    n = len(x) // frame
    if n < 3:
        return x
    rms = np.sqrt((x[: n * frame].reshape(n, frame) ** 2).mean(axis=1))
    threshold = max(float(np.percentile(rms, 10)) * 3.0, 0.006)
    loud = np.nonzero(rms > threshold)[0]
    if len(loud) == 0:
        return x
    return x[max(0, int(loud[0]) * frame - int(pad_s * sr)) :]


class Session:
    def __init__(
        self,
        cfg: Config,
        agent: Agent,
        speaker=None,
        recognizer: Recognizer | None = None,
        wake=None,
        events: SessionEvents | None = None,
        metrics: MetricsLog | None = None,
        clock: Callable[[], float] = time.monotonic,
        accurate=None,
    ):
        self.cfg, self.agent, self.speaker, self.recognizer, self.wake = cfg, agent, speaker, recognizer, wake
        self.events = events or SessionEvents()
        self.metrics = metrics or MetricsLog()
        self.clock = clock
        self.accurate = accurate  # optional second recognizer with a transcribe(samples) method
        self._utt: list[np.ndarray] = []  # audio of the sentence being spoken, for the accurate recognizer
        self._listen_window = cfg.listen_timeout_s
        self.state = State.IDLE
        self._lock = threading.RLock()
        self._turn_id = 0
        self._cancel = threading.Event()
        self._audio_q: queue.Queue = queue.Queue(maxsize=300)
        self._need_reset = False
        self._listen_started = 0.0
        self._heard = False
        self._last_partial = ""
        self._spoken = ""  # text sent to speech this turn, used to recognise our own voice
        self._ignore_until = 0.0
        self._voice_turn = False
        self._filler_said = False
        self._approval: Approval | None = None
        self.wake_probe = None  # set by the 'Test wake word' button: called with (samples, hit) instead of waking
        self._chime = listening_chime()
        orig_on_tool = agent.on_tool
        agent.on_tool = lambda msg: (orig_on_tool(msg), self._tool_filler())
        self._worker = threading.Thread(target=self._audio_loop, daemon=True)
        self._worker.start()

    # ---- public controls -------------------------------------------------
    def feed_audio(self, samples: np.ndarray) -> None:
        try:
            self._audio_q.put_nowait(samples)
        except queue.Full:
            pass  # drop rather than block the audio callback

    def activate(self) -> None:
        with self._lock:
            if self.state is State.IDLE and self.recognizer is not None:
                self._enter_listening()
                self._play_chime()

    def start(self) -> None:
        """Call once audio is flowing: opens the mic straight away in 'always' mode."""
        with self._lock:
            if self.state is State.IDLE:
                self._rest()

    def toggle(self) -> None:
        """Hotkey: start listening, or stop whatever is happening."""
        with self._lock:
            if self.state is State.IDLE:
                self.activate()
            elif self.state is State.SPEAKING and self.cfg.interrupt_mode == "ignore":
                return
            else:
                self._interrupt(State.IDLE)

    def stop(self) -> None:
        """Unconditional stop (UI button)."""
        with self._lock:
            self._interrupt(State.IDLE)

    def submit_text(self, text: str) -> None:
        text = text.strip()
        if text:
            with self._lock:
                if self.state in (State.THINKING, State.SPEAKING):
                    self._interrupt(State.IDLE)
                self._begin_turn(text, self.clock())

    def ask_voice(self, prompt: str, stop: threading.Event | None = None, timeout: float = 25.0) -> bool | None:
        """Read a confirmation prompt aloud, then listen for yes/no. True/False when answered, None on silence, unclear speech or stop."""
        if not (self.speaker and self.recognizer):
            return None
        a = Approval()
        text = speakable(prompt)
        with self._lock:
            self._approval, self._spoken = a, text
            self.speaker.say(clean_for_speech(text, self.cfg.tts_pronunciations))
        start = self.clock()
        while not self.speaker.wait_idle(0.1) and self.clock() - start < 30:
            if stop is not None and stop.is_set():
                break
        with self._lock:
            self._ignore_until = self.clock() + self.cfg.post_speech_guard_s
            self._need_reset, a.listening = True, True
        end = self.clock() + timeout
        while self.clock() < end and not a.done.is_set() and not (stop is not None and stop.is_set()):
            a.done.wait(0.1)
        with self._lock:
            a.listening, self._approval = False, None
        return a.result

    def _approve_audio(self, a: Approval, samples: np.ndarray) -> None:
        if self.clock() < self._ignore_until:
            return
        rec = self.recognizer
        rec.accept(samples)
        if rec.is_endpoint():
            heard = rec.text().strip()
            rec.reset()
            answer = parse_yes_no(heard)
            if answer is not None:  # no echo check: the prompt itself says "yes" and "no", the post-speech guard covers our own voice
                a.result = answer
                a.done.set()

    def announce(self, text: str) -> None:
        """Speak text outside a normal turn (timers, mode changes). Waits for any reply in progress to finish."""
        if not self.speaker:
            return

        def run() -> None:
            deadline = self.clock() + 60
            while self.state in (State.THINKING, State.SPEAKING) and self.clock() < deadline:
                time.sleep(0.2)
            with self._lock:
                if self.state in (State.THINKING, State.SPEAKING):
                    return
                self._spoken = text  # mic must not mistake our own voice for the user
                self._set_state(State.SPEAKING)
                self.speaker.begin_turn(lambda: None)
                self.speaker.say(clean_for_speech(text, self.cfg.tts_pronunciations))
            while not self.speaker.wait_idle(0.1):
                if self.state is not State.SPEAKING:
                    return
            with self._lock:
                if self.state is State.SPEAKING:
                    self._ignore_until = self.clock() + self.cfg.post_speech_guard_s
                    self._rest()

        threading.Thread(target=run, daemon=True).start()

    def close(self) -> None:
        self.stop()
        self._audio_q.put(None)

    # ---- state helpers (call with lock held) -------------------------------
    def _set_state(self, state: State) -> None:
        if self.state is not state:
            self.state = state
            self.events.state(state)

    def _enter_listening(self, keep_recognizer: bool = False, window: float | None = None) -> None:
        self._need_reset = not keep_recognizer
        if not keep_recognizer:
            self._utt = []
        self._listen_window = self.cfg.listen_timeout_s if window is None else window
        self._listen_started, self._heard, self._last_partial = self.clock(), keep_recognizer, ""
        self._set_state(State.LISTENING)

    def _play_chime(self) -> None:
        """Signal 'speak now' and hold the mic closed until the chime (and a short gap) are over, so the start of your
        sentence is neither lost nor mixed with the chime."""
        if not (self.cfg.listen_tone and self.speaker):
            return
        self.speaker.begin_turn(lambda: None)
        self.speaker.play_samples(self._chime, TONE_RATE)
        self._ignore_until = self.clock() + duration(self._chime) + self.cfg.listen_delay_s + 0.1

    def _rest(self) -> None:
        """Where we go when nothing is happening: open mic in 'always' mode, else idle."""
        self.events.partial("")
        if self.cfg.listen_mode == "always" and self.recognizer is not None:
            self._enter_listening()
        else:
            self._set_state(State.IDLE)

    def _interrupt(self, to: State, keep_recognizer: bool = False) -> None:
        self._cancel.set()
        self._turn_id += 1
        if self.speaker:
            self.speaker.stop()
        if to is State.LISTENING and self.recognizer is not None:
            self._enter_listening(keep_recognizer)
        else:
            self._rest()

    # ---- audio thread ------------------------------------------------------
    def _audio_loop(self) -> None:
        while (samples := self._audio_q.get()) is not None:
            try:
                self._handle_audio(samples)
            except Exception as e:  # keep the loop alive
                log.exception("audio handling failed")
                self.events.error(str(e))

    def _handle_audio(self, samples: np.ndarray) -> None:
        with self._lock:
            state, reset = self.state, self._need_reset
            self._need_reset = False
        if reset and self.recognizer:
            self.recognizer.reset()
        a = self._approval
        if a is not None and a.listening and self.recognizer:
            self._approve_audio(a, samples)
            return
        if state is State.IDLE:
            if self.wake is not None:
                hit = self.wake.accept(samples)
                probe = self.wake_probe
                if probe is not None:
                    probe(samples, hit)
                elif hit:
                    self.activate()
        elif state is State.LISTENING and self.recognizer:
            if self.clock() >= self._ignore_until:  # skip the tail of our own speech
                self._listen(samples)
        elif state is State.SPEAKING and self.cfg.interrupt_mode == "voice" and self.recognizer:
            self.recognizer.accept(samples)
            self._utt_add(samples, 8.0)  # keep the words that triggered the interruption
            heard = self.recognizer.text().strip()
            if self.recognizer.is_endpoint():
                self.recognizer.reset()
                self._utt = []
            elif len(heard) >= self.cfg.barge_in_min_chars and not self._is_echo(heard):
                with self._lock:
                    if self.state is State.SPEAKING:
                        self._interrupt(State.LISTENING, keep_recognizer=True)
        # THINKING, or SPEAKING with hotkey/ignore modes: audio is discarded.

    def _is_echo(self, heard: str) -> bool:
        """True when most heard words are ones we are speaking (mic picking up the speakers)."""
        words = re.findall(r"[a-z']+", heard.lower())
        spoken = set(re.findall(r"[a-z']+", self._spoken.lower()))
        return not words or sum(w in spoken for w in words) / len(words) >= 0.6

    def _utt_add(self, samples: np.ndarray, max_s: float) -> None:
        self._utt.append(samples)
        limit, total = int(max_s * 16000), sum(len(x) for x in self._utt)
        while len(self._utt) > 1 and total - len(self._utt[0]) >= limit:
            total -= len(self._utt.pop(0))

    def _refine(self, text: str, audio: list[np.ndarray]) -> str:
        """Re-transcribe the finished sentence with the accurate recognizer; fall back to the streaming text."""
        if self.accurate is None or not audio:
            return text
        x = trim_leading_silence(np.concatenate(audio))
        if len(x) < 4800:  # under 0.3 s: not worth it
            return text
        try:
            return self.accurate.transcribe(x).strip() or text
        except Exception:
            log.exception("accurate recognizer failed; using the streaming text")
            return text

    def _listen(self, samples: np.ndarray) -> None:
        rec = self.recognizer
        rec.accept(samples)
        self._utt_add(samples, 30.0 if self._heard else 20.0)  # silence is trimmed at the end of the sentence
        text = rec.text()
        if text != self._last_partial:
            self._last_partial = text
            self.events.partial(text)
        if text.strip():
            self._heard = True
        if rec.is_endpoint():
            t_end = self.clock()  # taken before re-checking, so the extra time shows up in the latency numbers
            final = text.strip()
            audio, self._utt = self._utt, []
            rec.reset()
            self._last_partial, self._heard = "", False
            if final:
                final = self._refine(final, audio)
                with self._lock:
                    if self.state is State.LISTENING:
                        self._begin_turn(final, t_end, voice=True)
        elif (
            not self._heard
            and self.cfg.listen_mode != "always"
            and self.clock() - self._listen_started > self._listen_window
        ):
            with self._lock:
                if self.state is State.LISTENING:
                    self._rest()

    # ---- turn --------------------------------------------------------------
    def _begin_turn(self, text: str, t_end: float, voice: bool = False) -> None:
        self._turn_id += 1
        self._spoken = ""
        self._filler_said = False
        self._voice_turn = voice
        tid = self._turn_id
        self._cancel = cancel = threading.Event()
        self._set_state(State.THINKING)
        self.events.partial("")
        self.events.user_final(text)
        m = TurnMetrics(mode="cloud" if self.cfg.is_cloud else "local", t_speech_end=t_end)
        threading.Thread(target=self._run_turn, args=(tid, cancel, text, m), daemon=True).start()

    def _speak(self, tid: int, chunk: str) -> None:
        chunk = clean_for_speech(chunk, self.cfg.tts_pronunciations)
        if not chunk or not self.speaker:
            return
        with self._lock:
            if tid != self._turn_id:
                return
            if self.state is State.THINKING:
                self._set_state(State.SPEAKING)
            self._spoken += " " + chunk
            self.speaker.say(chunk)

    def _tool_filler(self) -> None:
        """Say a short phrase when a tool call starts before any reply text, so a slow task is not silent."""
        if self._filler_said or not self.speaker:
            return
        self._filler_said = True
        self._speak(self._turn_id, self.cfg.tool_filler)

    def _run_turn(self, tid: int, cancel: threading.Event, text: str, m: TurnMetrics) -> None:
        chunker = SentenceChunker()
        if self.speaker:
            self.speaker.begin_turn(lambda: setattr(m, "t_first_audio", self.clock()))
        gen = self.agent.respond(text, cancel)
        try:
            for delta in gen:
                if cancel.is_set():
                    break
                if m.t_first_text is None:
                    m.t_first_text = self.clock()
                self.events.assistant_delta(delta)
                for chunk in chunker.feed(delta):
                    self._speak(tid, chunk)
            if not cancel.is_set() and (tail := chunker.flush()):
                self._speak(tid, tail)
        except Exception as e:
            log.exception("turn failed")
            self.events.error(str(e))
        finally:
            gen.close()
        if self.speaker:
            while not cancel.is_set() and not self.speaker.wait_idle(0.1):
                pass
        with self._lock:
            if tid != self._turn_id or cancel.is_set():
                return
            m.t_done = self.clock()
            self.metrics.record(m)
            self.events.assistant_done(m)
            self._ignore_until = self.clock() + self.cfg.post_speech_guard_s
            if self.cfg.follow_up and self.cfg.follow_up_s > 0 and self._voice_turn and self.recognizer is not None:
                self._enter_listening(window=self.cfg.follow_up_s)
                self._play_chime()
            else:
                self._rest()
