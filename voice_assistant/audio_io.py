"""Device selection, microphone capture (up to 2 inputs mixed), optional mic forwarding, and playback (up to 2 outputs)."""
from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Callable

import numpy as np

log = logging.getLogger(__name__)

ASR_RATE = 16000  # what the recognizer expects
FORWARD_RATE = 48000  # capture rate while forwarding the mic, so game voice is not band-limited to 8 kHz
DECIM = FORWARD_RATE // ASR_RATE
LEAD_IN_S = 0.12  # silence before speech after an idle gap: devices (HDMI, Bluetooth) clip the first syllable otherwise
TAIL_S = 0.10  # silence after speech so the last word is not cut off when the stream is idle-closed
IDLE_GAP_S = 1.0
IDLE_CLOSE_S = 30.0


class DeviceNotFound(Exception):
    pass


def _sd():
    import sounddevice as sd

    return sd


def list_devices(kind: str) -> list[str]:
    """Device names for 'input' or 'output' on the default host API (avoids duplicate entries)."""
    sd = _sd()
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    try:
        default = sd.default.device[0 if kind == "input" else 1]
        api = sd.query_devices(default)["hostapi"] if default is not None and default >= 0 else 0
    except Exception:
        api = 0
    names: list[str] = []
    for d in sd.query_devices():
        if d["hostapi"] == api and d[key] > 0 and d["name"] not in names:
            names.append(d["name"])
    return names


def resolve_device(name: str, kind: str) -> int | None:
    """Saved device name -> index. Empty name = system default (None)."""
    if not name:
        return None
    sd = _sd()
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    devs = [(i, d) for i, d in enumerate(sd.query_devices()) if d[key] > 0]
    for match in (lambda n: n == name, lambda n: n.startswith(name) or name.startswith(n)):
        for i, d in devs:
            if match(d["name"]):
                return i
    raise DeviceNotFound(f"{kind} device not found: {name}")


class AudioIO:
    def __init__(self, cfg, on_audio: Callable[[np.ndarray], None], on_warning: Callable[[str], None] = log.warning):
        self.cfg, self.on_audio, self.warn = cfg, on_audio, on_warning
        self.level = 0.0  # last mic block RMS (0..1), for the meter
        self.vr_active = False  # set by the VR watcher; selects the VR profile devices
        self._muted = False  # runtime mute for the forwarded mic (the assistant still hears you)
        self._streams: list = []
        self._rate = ASR_RATE
        self._sec: collections.deque = collections.deque(maxlen=FORWARD_RATE * 2)
        self._fwd_stream = None
        self._fwd_buf = np.zeros(0, np.float32)
        self._fwd_lock = threading.Lock()
        self._lock = threading.RLock()  # playback and device refresh must not overlap
        self._outs: dict = {}  # (device name, rate) -> [stream, last_used]; kept open between sentences

    @property
    def forward_muted(self) -> bool:
        return self._muted

    @forward_muted.setter
    def forward_muted(self, muted: bool) -> None:
        self._muted = muted
        if muted:  # drop what is already queued so the mute takes effect at once
            with self._fwd_lock:
                self._fwd_buf = np.zeros(0, np.float32)

    @property
    def forwarding(self) -> bool:
        return self._fwd_stream is not None

    # ---- input -------------------------------------------------------------
    def start_input(self) -> bool:
        self.stop_input()
        sd = _sd()
        want_forward = bool(self.cfg.mic_forward and self.cfg.output_device_2)
        self._rate = FORWARD_RATE if want_forward else ASR_RATE
        block = self._rate * 3 // 100  # 30 ms
        try:
            primary = resolve_device(self.cfg.device("input_device", self.vr_active), "input")
        except DeviceNotFound as e:
            self.warn(f"{e}; using the default microphone")
            primary = None
        try:
            secondary = resolve_device(self.cfg.input_device_2, "input") if self.cfg.input_device_2 else None
        except DeviceNotFound as e:
            self.warn(f"{e}; second input ignored")
            secondary = None
        try:
            if secondary is not None:
                s2 = sd.InputStream(
                    device=secondary, samplerate=self._rate, channels=1, dtype="float32", blocksize=block,
                    callback=lambda data, *_: self._sec.extend(data[:, 0]),
                )
                s2.start()
                self._streams.append(s2)
            if want_forward:
                self._start_forward(sd)
            s1 = sd.InputStream(
                device=primary, samplerate=self._rate, channels=1, dtype="float32", blocksize=block, callback=self._primary_cb,
            )
            s1.start()
            self._streams.append(s1)
            return True
        except Exception as e:
            self.warn(f"microphone unavailable: {e}")
            self.stop_input()
            return False

    def _start_forward(self, sd) -> None:
        try:
            dev = resolve_device(self.cfg.output_device_2, "output")
            self._fwd_stream = sd.OutputStream(
                device=dev, samplerate=FORWARD_RATE, channels=1, dtype="float32", callback=self._fwd_cb
            )
            self._fwd_stream.start()
        except Exception as e:
            self._fwd_stream = None
            self.warn(f"mic forwarding unavailable: {e}")

    def _fwd_cb(self, outdata, frames, *_):
        with self._fwd_lock:
            n = min(frames, len(self._fwd_buf))
            outdata[:n, 0] = self._fwd_buf[:n]
            self._fwd_buf = self._fwd_buf[n:]
        outdata[n:, 0] = 0.0  # underrun: silence

    def _forward(self, mic: np.ndarray) -> None:
        if self._fwd_stream is None or self._muted:
            return
        with self._fwd_lock:
            buf = np.concatenate([self._fwd_buf, mic])
            self._fwd_buf = buf[-FORWARD_RATE // 4 :]  # cap at 250 ms so latency cannot build up

    def _primary_cb(self, data, frames, *_):
        mic = data[:, 0].copy()
        self._forward(mic)  # game voice gets the mic only, never the mixed-in second input
        x = mic
        if len(self._streams) > 1:  # second input mixed in (for the assistant)
            extra = np.zeros(len(x), np.float32)
            for i in range(min(len(x), len(self._sec))):
                extra[i] = self._sec.popleft()
            x = np.clip(x + extra, -1.0, 1.0)
        self.level = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
        if self._rate != ASR_RATE:
            n = len(x) - len(x) % DECIM
            x = x[:n].reshape(-1, DECIM).mean(axis=1)  # simple decimation to 16 kHz for recognition
        self.on_audio(x.astype(np.float32, copy=False))

    def stop_input(self) -> None:
        for s in self._streams:
            try:
                s.stop()
                s.close()
            except Exception:
                pass
        self._streams.clear()
        if self._fwd_stream is not None:
            try:
                self._fwd_stream.stop()
                self._fwd_stream.close()
            except Exception:
                pass
            self._fwd_stream = None
        with self._fwd_lock:
            self._fwd_buf = np.zeros(0, np.float32)
        self._sec.clear()
        self.level = 0.0

    # ---- output (used as the Speaker's player) -------------------------------
    def _out_entry(self, sd, name: str, sr: int) -> list:
        key = (name, sr)
        entry = self._outs.get(key)
        if entry is None:
            stream = sd.OutputStream(device=resolve_device(name, "output"), samplerate=sr, channels=1, dtype="float32")
            stream.start()
            entry = self._outs[key] = [stream, 0.0]  # last_used 0 = brand new, so it gets the lead-in silence
        return entry

    def _drop_out(self, key, abort: bool = False) -> None:
        entry = self._outs.pop(key, None)
        if entry:
            try:
                (entry[0].abort if abort else entry[0].stop)()
                entry[0].close()
            except Exception:
                pass

    def refresh_devices(self) -> None:
        """Re-read the device list. PortAudio caches it at start-up, and Windows renumbers devices when a headset
        powers up or down, so old indices can point at a different device (e.g. the monitor instead of the headset)."""
        with self._lock:
            self.stop_input()
            self.close_outputs()
            sd = _sd()
            sd._terminate()
            sd._initialize()

    def close_outputs(self) -> None:
        for key in list(self._outs):
            self._drop_out(key, abort=True)

    def player(self, samples: np.ndarray, sr: int, cancel: threading.Event, on_start: Callable[[], None]) -> None:
        with self._lock:
            self._play(samples, sr, cancel, on_start)

    def _play(self, samples: np.ndarray, sr: int, cancel: threading.Event, on_start: Callable[[], None]) -> None:
        sd = _sd()
        now = time.monotonic()
        for key, (_, last) in list(self._outs.items()):  # close streams unused for a while
            if last and now - last > IDLE_CLOSE_S:
                self._drop_out(key)
        names = [self.cfg.device("output_device", self.vr_active)] + ([self.cfg.output_device_2] if self.cfg.output_device_2 else [])
        targets = []
        for name in names:  # first entry may be "" = system default
            try:
                targets.append(((name, sr), self._out_entry(sd, name, sr)))
            except Exception as e:
                self.warn(f"output {name or 'default'} unavailable: {e}")
        if not targets:
            return
        silence = lambda sec: np.zeros((int(sr * sec), 1), np.float32)  # noqa: E731
        try:
            for _, (stream, last) in targets:
                if now - last > IDLE_GAP_S:
                    stream.write(silence(LEAD_IN_S))
            on_start()
            block = max(sr // 20, 1)  # 50 ms so cancel is quick
            for i in range(0, len(samples), block):
                if cancel.is_set():
                    for key, _ in targets:  # abort discards audio already buffered in the device
                        self._drop_out(key, abort=True)
                    return
                chunk = np.ascontiguousarray(samples[i : i + block], dtype=np.float32).reshape(-1, 1)
                for _, (stream, _l) in targets:
                    stream.write(chunk)
            for _, (stream, _l) in targets:
                stream.write(silence(TAIL_S))
            end = time.monotonic()
            for _, entry in targets:
                entry[1] = end
        except Exception as e:
            self.warn(f"playback error: {e}")
            for key, _ in targets:
                self._drop_out(key, abort=True)
