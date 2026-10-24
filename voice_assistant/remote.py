"""Let a paired phone use this PC's assistant: the phone streams microphone audio in, the PC does the listening, thinking and speaking, and the
spoken reply streams back to the phone.

The server only listens on this PC's Tailscale address (never on every network interface), and a phone must send a pairing token it was given
once, when it was paired, before anything else is accepted. Only a hash of each token is stored here. Browsers are refused (they send an Origin
header; the phone app does not).

Messages are JSON text frames, plus binary frames for audio (16-bit little-endian mono PCM; 16 kHz from the phone, any rate from the PC):
  phone -> PC   hello {token, device}, <audio>, wake, end_of_speech, interrupt, text {text}, get_keywords {phrase, threshold}, get_settings, set_setting {key, value}, confirm_result {id, ok},
                camera_pending {id, opened}, camera_photo {id, jpeg_b64}, camera_cancel {id, reason}, ping
  PC -> phone   ready {protocol, state, name}, state {state}, partial {text}, user_final {text}, assistant_delta {text}, done {summary},
                confirm {id, prompt}, audio_start {sr}, <audio>, audio_flush, audio_end, keywords {phrase, lines}, keywords_error {phrase, message},
                camera_request {id, prompt, auto}, camera_close {id}, settings {allow_safety, restart_pending, sections}, setting_result {ok, key, value, restart, message},
                error {message}, pong
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import platform
import queue
import secrets
import socket
import subprocess
import threading
import time
import uuid
import wave
from pathlib import Path

import numpy as np

from .config import config_dir
from .remote_settings import SettingsService
from .session import MultiEvents, Session, SessionEvents, State

log = logging.getLogger(__name__)

PROTOCOL = 1
HELLO_TIMEOUT_S = 5.0
MAX_MESSAGE_BYTES = 2 * 1024 * 1024  # a photo arrives as one base64 message; the phone shrinks it to well under this
MAX_PHOTO_BYTES = 1_400_000
CAMERA_WAIT_S = 90.0
MAX_AUDIO_SAMPLES = 32000  # two seconds in one frame is far more than any phone sends
_TAILSCALE_EXES = ("tailscale", r"C:\Program Files\Tailscale\tailscale.exe")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")  # the range Tailscale addresses come from


def tailscale_ip(run=subprocess.run) -> str | None:
    """This PC's Tailscale IPv4 address, or None when Tailscale is not installed or not connected."""
    for exe in _TAILSCALE_EXES:
        try:
            out = run([exe, "ip", "-4"], capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            continue
        for line in (out.stdout or "").split():
            try:
                if ipaddress.ip_address(line) in _CGNAT:
                    return line
            except ValueError:
                continue
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if ipaddress.ip_address(info[4][0]) in _CGNAT:
                return info[4][0]
    except (OSError, ValueError):
        pass
    return None


class PairedDevices:
    """Phones allowed to connect. Only a hash of each token is kept, so the file alone cannot be used to connect."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else config_dir() / "remote-devices.json"
        self._lock = threading.Lock()
        self._devices: dict = {}
        self._stamp: int | None = None
        self._sync()

    def _sync(self) -> None:
        """Pick up changes another process made (a phone paired or removed from the command line while the assistant runs)."""
        try:
            stamp = self.path.stat().st_mtime_ns
        except OSError:
            stamp = None
        if stamp == self._stamp:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._devices = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            self._devices = {}
        self._stamp = stamp

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._devices, indent=2), encoding="utf-8")
        tmp.replace(self.path)
        self._stamp = self.path.stat().st_mtime_ns

    def pair(self, name: str) -> tuple[str, str]:
        """Add a phone; returns (device id, token). The token is shown once (in the pairing QR code) and cannot be recovered."""
        token = secrets.token_urlsafe(32)
        device_id = uuid.uuid4().hex[:8]
        with self._lock:
            self._sync()
            self._devices[device_id] = {"name": (name or "Phone")[:40], "hash": self._hash(token), "created": int(time.time())}
            self._save()
        return device_id, token

    def verify(self, token: str) -> str | None:
        digest = self._hash(token)
        with self._lock:
            self._sync()
            found = None
            for device_id, info in self._devices.items():
                if hmac.compare_digest(info.get("hash", ""), digest):  # compare all, so timing does not reveal which matched
                    found = device_id
            return found

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            self._sync()
            if self._devices.pop(device_id, None) is None:
                return False
            self._save()
            return True

    def list(self) -> list[dict]:
        with self._lock:
            self._sync()
            return [{"id": i, "name": d.get("name", ""), "created": d.get("created", 0)} for i, d in self._devices.items()]


def _device_names(kind: str) -> list[str]:
    from .audio_io import list_devices

    return list_devices(kind)


def to_pcm16(samples: np.ndarray) -> np.ndarray:
    return (np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * 32767.0).astype("<i2")


class _Client:
    """One connected phone. Everything sent goes through one writer thread, so a slow phone never stalls the assistant's own threads."""

    def __init__(self, ws, device_id: str):
        self.ws, self.device_id = ws, device_id
        self.alive = True
        self._out: queue.Queue = queue.Queue()
        self._pending: dict[str, tuple[threading.Event, list]] = {}
        self._cameras: dict[str, tuple[threading.Event, list]] = {}
        self._lock = threading.Lock()
        threading.Thread(target=self._writer, daemon=True).start()

    def _writer(self) -> None:
        while (item := self._out.get()) is not None:
            try:
                self.ws.send(item)
            except Exception:
                self.alive = False
                return

    def send(self, message) -> bool:
        if not self.alive:
            return False
        self._out.put(json.dumps(message) if isinstance(message, dict) else message)
        return True

    def close(self, code: int = 1000, reason: str = "") -> None:
        self.alive = False
        self._out.put(None)
        try:
            self.ws.close(code, reason)
        except Exception:
            pass

    def ask(self, prompt: str, timeout: float, stop: threading.Event | None) -> bool | None:
        mid, event, box = uuid.uuid4().hex, threading.Event(), []
        with self._lock:
            self._pending[mid] = (event, box)
        try:
            if not self.send({"type": "confirm", "id": mid, "prompt": prompt}):
                return None
            end = time.monotonic() + timeout
            while self.alive and time.monotonic() < end and not event.is_set() and not (stop is not None and stop.is_set()):
                event.wait(0.1)
            return box[0] if box else None
        finally:
            with self._lock:
                self._pending.pop(mid, None)

    def resolve(self, mid: str, ok: bool) -> None:
        with self._lock:
            entry = self._pending.get(mid)
        if entry:
            entry[1].append(bool(ok))
            entry[0].set()

    def request_photo(self, prompt: str, auto: bool, timeout: float, abandoned) -> tuple[bytes | None, str]:
        """Ask the phone to open its camera. Returns (JPEG bytes, "ok"), or (None, why): cancelled, timeout, interrupted, disconnected, ..."""
        mid, event, box = uuid.uuid4().hex, threading.Event(), []
        with self._lock:
            self._cameras[mid] = (event, box)
        try:
            if not self.send({"type": "camera_request", "id": mid, "prompt": prompt[:300], "auto": bool(auto)}):
                return None, "disconnected"
            end = time.monotonic() + timeout
            while self.alive and time.monotonic() < end and not event.is_set() and not abandoned():
                event.wait(0.1)
            if box:
                return box[0]
            self.send({"type": "camera_close", "id": mid})
            if not self.alive:
                return None, "disconnected"
            return None, "timeout" if time.monotonic() >= end else "interrupted"
        finally:
            with self._lock:
                self._cameras.pop(mid, None)

    def resolve_camera(self, mid: str, photo: bytes | None, reason: str) -> None:
        with self._lock:
            entry = self._cameras.get(mid)
        if entry:
            entry[1].append((photo, reason))
            entry[0].set()

    def cancel_cameras(self, reason: str = "interrupted") -> None:
        with self._lock:
            ids = list(self._cameras)
        for mid in ids:
            self.resolve_camera(mid, None, reason)
            self.send({"type": "camera_close", "id": mid})

    def release(self) -> None:
        with self._lock:
            pending = list(self._pending.values()) + list(self._cameras.values())
        for event, _ in pending:
            event.set()


class _PhoneEvents(SessionEvents):
    def __init__(self, server: "RemoteServer"):
        self.server = server

    def _send(self, message: dict) -> None:
        client = self.server.client
        if client is not None:
            client.send(message)

    def state(self, state): self._send({"type": "state", "state": state.value})
    def partial(self, text): self._send({"type": "partial", "text": text})
    def user_final(self, text): self._send({"type": "user_final", "text": text})
    def assistant_delta(self, text): self._send({"type": "assistant_delta", "text": text})
    def assistant_done(self, metrics): self._send({"type": "done", "summary": metrics.describe()})
    def error(self, message): self._send({"type": "error", "message": message})


class RemoteServer:
    def __init__(self, session: Session, events: MultiEvents, cfg, devices: PairedDevices | None = None, host: str | None = None,
                 serve=None, find_ip=tailscale_ip, clock=time.monotonic, settings_save=None, device_names=None):
        """host: the address to listen on (tests pass 127.0.0.1); by default this PC's Tailscale address."""
        self.session, self.events, self.cfg = session, events, cfg
        self.devices = devices or PairedDevices()
        self.host, self._serve, self._find_ip, self.clock = host, serve, find_ip, clock
        self.bound_host = ""
        self.port: int | None = None
        self.error = ""
        self.client: _Client | None = None
        self._server = None
        self._lock = threading.Lock()
        self._phone_events = _PhoneEvents(self)
        self.settings = SettingsService(cfg, lambda: bool(getattr(cfg, "remote_allow_safety_changes", False)), device_names or _device_names, settings_save)
        # Developer aid, off unless set: keep each utterance the phone sends as a WAV in this folder to judge microphone quality.
        # Audio is otherwise never written to disk.
        debug = os.environ.get("VA_REMOTE_DEBUG_DIR")
        self._debug_dir = Path(debug) if debug else None
        self._capture: list[bytes] | None = None

    # ---- server -----------------------------------------------------------------
    def start(self) -> bool:
        if self._serve is None:
            from websockets.sync.server import serve

            self._serve = serve
        host = self.host or self._find_ip()
        if not host:
            self.error = "Tailscale is not running on this PC, so the phone link is off. Install Tailscale and sign in, then restart."
            return False
        try:
            self._server = self._serve(self._handler, host, self.cfg.remote_port, max_size=MAX_MESSAGE_BYTES)
        except OSError as e:
            self.error = f"Could not open port {self.cfg.remote_port} on {host}: {e}"
            return False
        self.bound_host, self.port = host, self._server.socket.getsockname()[1]
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self.events.add(self._phone_events)
        self.session.phone_active = self.phone_active
        self.error = ""
        return True

    def stop(self) -> None:
        self.events.remove(self._phone_events)
        if self.session is not None:
            self.session.phone_active = lambda: False
        client, self.client = self.client, None
        if client:
            client.close(1001, "server stopping")
        if self._server is not None:
            self._server.shutdown()
            self._server = None

    @property
    def running(self) -> bool:
        return self._server is not None

    def phone_active(self) -> bool:
        """A phone is connected and acting as the microphone."""
        client = self.client
        return client is not None and client.alive

    def status(self) -> dict:
        client = self.client
        return {"running": self.running, "host": self.bound_host, "port": self.port, "error": self.error,
                "connected": client is not None and client.alive, "device": client.device_id if client else ""}

    def pairing_payload(self, name: str = "") -> dict:
        """What the pairing QR code carries. The token inside is shown once; only its hash is kept."""
        device_id, token = self.devices.pair(name)
        return {"v": PROTOCOL, "host": self.bound_host, "port": self.port, "token": token, "id": device_id, "name": platform.node()}

    # ---- connection -------------------------------------------------------------
    def _authenticate(self, ws) -> str | None:
        try:
            if ws.request.headers.get("Origin"):  # a web page, not the phone app
                ws.close(1008, "origin not allowed")
                return None
        except Exception:
            pass
        try:
            hello = json.loads(ws.recv(timeout=HELLO_TIMEOUT_S))
            token = hello.get("token") if isinstance(hello, dict) and hello.get("type") == "hello" else None
        except Exception:
            token = None
        device_id = self.devices.verify(token) if isinstance(token, str) and token else None
        if device_id is None:
            try:
                ws.send(json.dumps({"type": "error", "message": "This phone is not paired with this PC."}))
                ws.close(1008, "not paired")
            except Exception:
                pass
        return device_id

    def _handler(self, ws) -> None:
        device_id = self._authenticate(ws)
        if device_id is None:
            return
        client = _Client(ws, device_id)
        with self._lock:
            old, self.client = self.client, client
        if old is not None:
            old.close(1000, "replaced by a newer connection")
        client.send({"type": "ready", "protocol": PROTOCOL, "state": self.session.state.value, "name": platform.node(),
                     "output": self.cfg.remote_output})
        try:
            for raw in ws:
                self._on_message(client, raw)
        except Exception:
            log.debug("phone connection ended", exc_info=True)
        finally:
            with self._lock:
                if self.client is client:
                    self.client = None
            client.alive = False
            client.release()
            client._out.put(None)

    def _on_message(self, client: _Client, raw) -> None:
        if isinstance(raw, (bytes, bytearray)):
            usable = len(raw) - len(raw) % 2
            if 0 < usable <= MAX_AUDIO_SAMPLES * 2:
                if self._capture is not None:
                    self._capture.append(bytes(raw[:usable]))
                self.session.feed_audio(np.frombuffer(bytes(raw[:usable]), dtype="<i2").astype(np.float32) / 32768.0, remote=True)
            return
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        kind = msg.get("type") if isinstance(msg, dict) else None
        if kind == "wake":
            self._capture = [] if self._debug_dir else None
            if self.session.state in (State.THINKING, State.SPEAKING):  # pressing the button while it talks means "stop and listen"
                self.session.stop()
            self.session.activate()
        elif kind == "end_of_speech":
            self._save_capture()
            self.session.finalize()
        elif kind == "interrupt":
            self._save_capture()
            client.cancel_cameras("interrupted")
            self.session.stop()
        elif kind == "camera_photo":
            self._receive_photo(client, msg)
        elif kind == "camera_cancel":
            client.resolve_camera(str(msg.get("id", "")), None, str(msg.get("reason") or "cancelled")[:80])
        elif kind == "camera_pending":
            if not msg.get("opened"):  # the screen is off: Android will not let the app open the camera by itself
                self.session.announce("Tap the notification on your phone to take the photo.")
        elif kind == "text":
            self.session.submit_text(str(msg.get("text", ""))[:2000])
        elif kind == "get_keywords":
            self._send_keywords(client, msg)
        elif kind == "get_settings":
            client.send({"type": "settings", **self.settings.snapshot()})
        elif kind == "set_setting":
            client.send({"type": "setting_result", **self.settings.change(str(msg.get("key", "")), msg.get("value"))})
        elif kind == "confirm_result":
            client.resolve(str(msg.get("id", "")), bool(msg.get("ok")))
        elif kind == "ping":
            client.send({"type": "pong"})

    def _receive_photo(self, client: _Client, msg: dict) -> None:
        mid = str(msg.get("id", ""))
        try:
            data = base64.b64decode(str(msg.get("jpeg_b64", "")), validate=True)
        except ValueError:
            data = b""
        if not data.startswith(b"\xff\xd8\xff"):
            client.resolve_camera(mid, None, "invalid")
        elif len(data) > MAX_PHOTO_BYTES:
            client.resolve_camera(mid, None, "too_big")
        else:
            client.resolve_camera(mid, data, "ok")

    def request_photo(self, prompt: str = "", auto: bool = False, timeout: float = CAMERA_WAIT_S) -> tuple[bytes | None, str]:
        """Have the connected phone open its camera and return the photo the user takes: (JPEG bytes, "ok") or (None, why)."""
        client = self.client
        if client is None or not client.alive:
            return None, "no_phone"
        # the turn was cut short (Stop, or the user started talking again) while we were waiting: stop waiting too
        abandoned = lambda: self.session.state in (State.IDLE, State.LISTENING)  # noqa: E731
        return client.request_photo(prompt, auto, timeout, abandoned)

    def _send_keywords(self, client: _Client, msg: dict) -> None:
        """The phone spots its own wake word; it asks the PC to turn the phrase into the wake model's token format (the model's BPE file lives here)."""
        from .models import keyword_lines

        phrase = str(msg.get("phrase", ""))[:60]
        try:
            threshold = min(max(float(msg.get("threshold", 0.12)), 0.03), 0.6)
            lines = keyword_lines(Path(self.cfg.models_dir) / "kws", phrase, threshold=threshold)
        except (ValueError, OSError, ImportError, RuntimeError) as e:  # ModelsMissing is a RuntimeError
            client.send({"type": "keywords_error", "phrase": phrase, "message": str(e)})
            return
        client.send({"type": "keywords", "phrase": phrase, "lines": lines})

    def _save_capture(self) -> None:
        chunks, self._capture = self._capture, None
        if not chunks or self._debug_dir is None:
            return
        try:
            self._debug_dir.mkdir(parents=True, exist_ok=True)
            with wave.open(str(self._debug_dir / f"utterance-{int(time.time())}.wav"), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"".join(chunks))
        except OSError:
            log.debug("could not save the debug recording", exc_info=True)

    # ---- replies and approvals --------------------------------------------------
    def ask(self, prompt: str, timeout: float = 60.0, stop: threading.Event | None = None) -> bool | None:
        """Ask the phone to approve something. True/False when answered, None when no phone is connected, no answer came, or stop was set."""
        client = self.client
        return client.ask(prompt, timeout, stop) if client is not None and client.alive else None

    def make_player(self, local_player):
        """Wrap the speaker's player: while a phone is connected the reply streams to it (and to the PC speakers too in 'both' mode)."""
        def player(samples, sr, cancel, on_start):
            client = self.client
            mode = self.cfg.remote_output
            if client is None or not client.alive or mode == "pc":
                return local_player(samples, sr, cancel, on_start)
            local = None
            if mode == "both":
                local = threading.Thread(target=local_player, args=(samples, sr, cancel, lambda: None), daemon=True)
                local.start()
            self._stream(client, samples, sr, cancel, on_start)
            if local is not None:
                local.join()
        return player

    def _stream(self, client: _Client, samples, sr: int, cancel: threading.Event, on_start, lead: float = 0.3, frame_s: float = 0.1) -> None:
        """Send audio at playback speed (the speaker's player contract is to block until the sound is done), keeping `lead` seconds ahead."""
        pcm = to_pcm16(samples)
        client.send({"type": "audio_start", "sr": sr, "samples": int(len(pcm))})
        on_start()
        step, start = max(int(sr * frame_s), 1), self.clock()

        def cancelled() -> bool:
            if cancel.is_set():
                client.send({"type": "audio_flush"})
                return True
            return False

        for i in range(0, len(pcm), step):
            if cancelled():
                return
            while (delay := start + i / sr - lead - self.clock()) > 0:
                if cancel.wait(min(delay, 0.05)):
                    break
            if cancelled():
                return
            client.send(pcm[i : i + step].tobytes())
        end = start + len(pcm) / sr
        while (delay := end - self.clock()) > 0:
            if cancel.wait(min(delay, 0.05)):
                break
        if not cancelled():
            client.send({"type": "audio_end"})
