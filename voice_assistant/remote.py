"""Let a paired phone use this PC's assistant: the phone streams microphone audio in, the PC does the listening, thinking and speaking, and the
spoken reply streams back to the phone.

The server only listens on this PC's Tailscale address (never on every network interface), and a phone must send a pairing token it was given
once, when it was paired, before anything else is accepted. Only a hash of each token is stored here. Browsers are refused (they send an Origin
header; the phone app does not).

Messages are JSON text frames, plus binary frames for audio (16-bit little-endian mono PCM; 16 kHz from the phone, any rate from the PC):
  phone -> PC   hello {token, device}, <audio>, wake, end_of_speech, interrupt, text {text}, get_keywords {phrase, threshold}, get_settings, set_setting {key, value}, confirm_result {id, ok},
                camera_pending {id, opened}, camera_photo {id, jpeg_b64}, camera_cancel {id, reason}, ping,
                camera_video_begin {id, count, duration_ms, [detail_count]}, camera_video_frame {id, index, t_ms, jpeg_b64, [detail]}, camera_video_audio {id, pcm_b64}, camera_video_end {id}
                (a video is sampled on the phone, so only a few small frames cross the link, never the clip itself),
                share_file {id, name, mime, total, seq, last, data_b64} (a file shared from another app, in pieces; see phone_share.py),
                phone_action_result {id, ok, message}
  PC -> phone   ready {protocol, state, name}, state {state}, partial {text}, user_final {text}, assistant_delta {text}, done {summary},
                confirm {id, prompt}, audio_start {sr}, <audio>, audio_flush, audio_end, keywords {phrase, lines}, keywords_error {phrase, message},
                camera_request {id, prompt, auto, [mode: "video", max_seconds, max_frames, max_edge, max_fps, max_bytes, audio, detail_frames, detail_edge]}, camera_close {id}, settings {allow_safety, restart_pending, sections}, setting_result {ok, key, value, restart, message},
                share_saved {id, ok, name, message}, phone_action {id, action, args} (see phone_actions.py; the user confirms each one on the phone),
                card {id, sources [{title, url, site}], settings [{section, label}], images [{jpeg_b64, page, caption, site}]} after done (sent again with
                the images once they are downloaded; see answer_cards.py),
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
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import config_dir
from .remote_settings import SettingsService
from .session import MultiEvents, Session, SessionEvents, State

log = logging.getLogger(__name__)

PROTOCOL = 1
HELLO_TIMEOUT_S = 5.0
CLOSE_TIMEOUT_S = 1.0  # how long a closing connection waits for the phone to confirm
MAX_MESSAGE_BYTES = 2 * 1024 * 1024  # a photo arrives as one base64 message; the phone shrinks it to well under this
MAX_PHOTO_BYTES = 1_400_000
CAMERA_WAIT_S = 90.0
ACTION_WAIT_S = 60.0  # how long the phone has for the user to agree to an action
MAX_ACTION_REPLY_CHARS = 8000  # room for a list of notifications
MAX_VIDEO_FRAMES = 500
MAX_VIDEO_FRAME_BYTES = 600_000
MAX_VIDEO_TOTAL_BYTES = 80_000_000
MAX_VIDEO_BUDGET_BYTES = 65_000_000  # what the PC will ask the phone for (the receiving limit above leaves room to spare)
MAX_VIDEO_SECONDS = 120
MAX_DETAIL_FRAMES = 8
MAX_VIDEO_AUDIO_BYTES = 8_000_000  # 16 kHz 16-bit mono: four minutes
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


PAIR_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L: easy to read out or type
PAIR_CODE_LENGTH = 10
PAIR_TTL_S = 300.0
PAIR_MAX_TRIES = 5


def normalize_code(code: str) -> str:
    return "".join(ch for ch in str(code).upper() if ch.isalnum())


def format_code(code: str) -> str:
    """ABCDEFGHJK -> ABCD-EFGH-JK, for showing to a person."""
    return "-".join([code[:4], code[4:8], code[8:]]).strip("-")


@dataclass(frozen=True)
class PairingOffer:
    code: str
    expires_at: float


class PairedDevices:
    """Phones allowed to connect. Only a hash of each token is kept, so the file alone cannot be used to connect."""

    def __init__(self, path: str | Path | None = None, clock=time.time):
        self.path = Path(path) if path else config_dir() / "remote-devices.json"
        self._lock = threading.Lock()
        self._clock = clock
        self._devices: dict = {}
        self._stamp: int | None = None
        # a pairing offer lives in memory only: it is never written to disk, works once, and expires
        self._offer: PairingOffer | None = None
        self._tries = 0
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

    # ---- one-time pairing codes ----
    def begin_pairing(self, ttl: float = PAIR_TTL_S) -> PairingOffer:
        """Open a pairing: a short code the phone scans or types within `ttl` seconds. A new offer replaces any earlier one."""
        code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(PAIR_CODE_LENGTH))
        with self._lock:
            self._offer = PairingOffer(code, self._clock() + ttl)
            self._tries = 0
            return self._offer

    def pairing_seconds_left(self) -> int:
        with self._lock:
            offer = self._offer
            return max(0, int(offer.expires_at - self._clock())) if offer is not None else 0

    def cancel_pairing(self) -> None:
        with self._lock:
            self._offer = None

    def redeem(self, code: str, name: str) -> tuple[str, str] | None:
        """Exchange the one-time code for a phone's own token: (device id, token), or None. Wrong guesses cancel the offer after a few tries."""
        with self._lock:
            offer = self._offer
            if offer is None or self._clock() >= offer.expires_at:
                self._offer = None
                return None
            if not hmac.compare_digest(offer.code, normalize_code(code)):
                self._tries += 1
                if self._tries >= PAIR_MAX_TRIES:
                    self._offer = None
                return None
            self._offer = None  # single use
        return self.pair(name)

    def touch(self, device_id: str) -> None:
        """Remember when a phone last connected (written at most once a minute)."""
        with self._lock:
            self._sync()
            info = self._devices.get(device_id)
            now = int(self._clock())
            if info is not None and now - int(info.get("last_seen", 0)) >= 60:
                info["last_seen"] = now
                self._save()

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
            return [{"id": i, "name": d.get("name", ""), "created": d.get("created", 0), "last_seen": d.get("last_seen", 0)} for i, d in self._devices.items()]


def _device_names(kind: str) -> list[str]:
    from .audio_io import list_devices

    return list_devices(kind)


def to_pcm16(samples: np.ndarray) -> np.ndarray:
    return (np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0) * 32767.0).astype("<i2")


@dataclass
class PhoneVideo:
    """A clip recorded on the phone: (seconds, JPEG) frames in time order, and how long the clip was."""

    frames: list
    duration: float
    audio: bytes | None = None  # the clip's sound as 16 kHz mono 16-bit samples, when it was asked for and sent
    detail: list | None = None  # a few extra frames at full detail, (seconds, JPEG), when they were asked for and sent


class _Client:
    """One connected phone. Everything sent goes through one writer thread, so a slow phone never stalls the assistant's own threads."""

    def __init__(self, ws, device_id: str):
        self.ws, self.device_id = ws, device_id
        self.alive = True
        self._out: queue.Queue = queue.Queue()
        self._pending: dict[str, tuple[threading.Event, list]] = {}
        self._cameras: dict[str, tuple[threading.Event, list]] = {}
        self._actions: dict[str, tuple[threading.Event, list]] = {}
        self._videos: dict[str, dict] = {}  # clips arriving frame by frame: id -> {count, duration, frames, bytes}
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

    def request_action(self, action: str, args: dict, timeout: float, abandoned) -> tuple[bool, str]:
        """Ask the phone to do something (set an alarm, add an event...). The phone shows it to the user first. (done, what the phone said)."""
        mid, event, box = uuid.uuid4().hex, threading.Event(), []
        with self._lock:
            self._actions[mid] = (event, box)
        try:
            if not self.send({"type": "phone_action", "id": mid, "action": action, "args": args}):
                return False, "disconnected"
            end = time.monotonic() + timeout
            while self.alive and time.monotonic() < end and not event.is_set() and not abandoned():
                event.wait(0.1)
            if box:
                return box[0]
            return False, "disconnected" if not self.alive else "timeout" if time.monotonic() >= end else "interrupted"
        finally:
            with self._lock:
                self._actions.pop(mid, None)

    def resolve_action(self, mid: str, ok: bool, message: str) -> None:
        with self._lock:
            entry = self._actions.get(mid)
        if entry:
            entry[1].append((bool(ok), message))
            entry[0].set()

    def request_photo(self, prompt: str, auto: bool, timeout: float, abandoned) -> tuple[bytes | None, str]:
        """Ask the phone to open its camera. Returns (JPEG bytes, "ok"), or (None, why): cancelled, timeout, interrupted, disconnected, ..."""
        return self._request_camera({"prompt": prompt[:300], "auto": bool(auto)}, timeout, abandoned)

    def request_video(self, prompt: str, max_seconds: int, max_frames: int, max_edge: int, max_fps: float, max_bytes: int, timeout: float, abandoned,
                      audio: bool = False, detail_frames: int = 0, detail_edge: int = 1280) -> tuple["PhoneVideo | None", str]:
        """Ask the phone to record a short clip and send back evenly spaced, already shrunken frames: (PhoneVideo, "ok") or (None, why)."""
        extra = {"prompt": prompt[:300], "auto": False, "mode": "video", "max_seconds": int(min(max(max_seconds, 3), MAX_VIDEO_SECONDS)),
                 "max_frames": int(min(max(max_frames, 2), MAX_VIDEO_FRAMES)), "max_edge": int(min(max(max_edge, 320), 1920)), "max_fps": float(max_fps),
                 "max_bytes": int(min(max(max_bytes, 500_000), MAX_VIDEO_BUDGET_BYTES)), "audio": bool(audio),
                 "detail_frames": int(min(max(detail_frames, 0), MAX_DETAIL_FRAMES)), "detail_edge": int(min(max(detail_edge, 640), 1920))}
        return self._request_camera(extra, timeout, abandoned)

    def _request_camera(self, extra: dict, timeout: float, abandoned):
        mid, event, box = uuid.uuid4().hex, threading.Event(), []
        with self._lock:
            self._cameras[mid] = (event, box)
        try:
            if not self.send({"type": "camera_request", "id": mid, **extra}):
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
                self._videos.pop(mid, None)

    def resolve_camera(self, mid: str, photo, reason: str) -> None:
        with self._lock:
            entry = self._cameras.get(mid)
        if entry:
            entry[1].append((photo, reason))
            entry[0].set()

    # ---- a clip arriving as separate small messages ----------------------------------------------------
    def video_begin(self, msg: dict) -> None:
        mid = str(msg.get("id", ""))
        try:
            count, duration = int(msg.get("count", 0)), float(msg.get("duration_ms", 0)) / 1000.0
        except (TypeError, ValueError):
            count, duration = 0, 0.0
        with self._lock:
            known = mid in self._cameras
        if not known:
            return
        if not 1 <= count <= MAX_VIDEO_FRAMES or not 0 < duration <= MAX_VIDEO_SECONDS * 4:
            self.resolve_camera(mid, None, "invalid")
            return
        with self._lock:
            detail_count = 0
            try:
                detail_count = min(max(int(msg.get("detail_count", 0)), 0), MAX_DETAIL_FRAMES)
            except (TypeError, ValueError):
                pass
            self._videos[mid] = {"count": count, "duration": duration, "frames": {}, "bytes": 0, "audio": bytearray(), "detail": {}, "detail_count": detail_count}

    def video_frame(self, msg: dict) -> None:
        mid = str(msg.get("id", ""))
        with self._lock:
            clip = self._videos.get(mid)
        if clip is None:
            return
        try:
            index, t = int(msg.get("index", -1)), float(msg.get("t_ms", 0)) / 1000.0
            data = base64.b64decode(str(msg.get("jpeg_b64", "")), validate=True)
        except (TypeError, ValueError):
            self.resolve_camera(mid, None, "invalid")
            return
        detail = bool(msg.get("detail"))
        limit = clip["detail_count"] if detail else clip["count"]
        if not 0 <= index < limit or not data.startswith(b"\xff\xd8\xff"):
            self.resolve_camera(mid, None, "invalid")
        elif len(data) > MAX_VIDEO_FRAME_BYTES or clip["bytes"] + len(data) > MAX_VIDEO_TOTAL_BYTES:
            self.resolve_camera(mid, None, "too_big")
        else:
            clip["detail" if detail else "frames"][index] = (t, data)
            clip["bytes"] += len(data)

    def video_audio(self, msg: dict) -> None:
        mid = str(msg.get("id", ""))
        with self._lock:
            clip = self._videos.get(mid)
        if clip is None:
            return
        try:
            data = base64.b64decode(str(msg.get("pcm_b64", "")), validate=True)
        except (TypeError, ValueError):
            data = b""
        if not data or len(data) % 2 or len(clip["audio"]) + len(data) > MAX_VIDEO_AUDIO_BYTES:
            clip["audio_bad"] = True  # sound that does not fit is dropped; the pictures still count
            return
        clip["audio"] += data

    def video_end(self, msg: dict) -> None:
        mid = str(msg.get("id", ""))
        with self._lock:
            clip = self._videos.pop(mid, None)
        if clip is None:
            return
        frames = [clip["frames"][i] for i in sorted(clip["frames"])]
        audio = bytes(clip["audio"]) if clip["audio"] and not clip.get("audio_bad") else None
        detail = [clip["detail"][i] for i in sorted(clip["detail"])] or None
        self.resolve_camera(mid, PhoneVideo(frames, clip["duration"], audio, detail) if frames else None, "ok" if frames else "invalid")

    def cancel_cameras(self, reason: str = "interrupted") -> None:
        with self._lock:
            ids = list(self._cameras)
        for mid in ids:
            self.resolve_camera(mid, None, reason)
            self.send({"type": "camera_close", "id": mid})

    def release(self) -> None:
        with self._lock:
            pending = list(self._pending.values()) + list(self._cameras.values()) + list(self._actions.values())
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
    def assistant_done(self, metrics):
        self._send({"type": "done", "summary": metrics.describe()})
        cards = self.server.cards
        card = cards.card() if cards is not None and self.server.client is not None else None
        log.debug("answer card: %s", "none" if card is None else f"{len(card['sources'])} sources, {len(card['_pending'])} pictures")
        if card is None:
            return
        pending = card.pop("_pending", [])
        self._send(card)  # the sources at once; pictures follow as an update of the same card, once downloaded
        if pending:
            threading.Thread(target=lambda: (pics := cards.pictures(pending)) and self._send({**card, "images": pics}), daemon=True).start()
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
        self.listeners: list = []  # called with {"event": "paired" | "connected" | "disconnected", ...} from the server's threads
        self._phone_events = _PhoneEvents(self)
        self.shares = None  # a phone_share.ShareReceiver: files shared from the phone's other apps
        self.cards = None  # an answer_cards.CardCollector: the sources and pictures shown under each reply on the phone
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
            # a phone that is asleep or out of reach never answers the closing handshake; don't wait the default 10 s for it
            self._server = self._serve(self._handler, host, self.cfg.remote_port, max_size=MAX_MESSAGE_BYTES, close_timeout=CLOSE_TIMEOUT_S)
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
        if self.shares is not None:
            self.shares.close()  # half-received files are removed

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
                "connected": client is not None and client.alive, "device": client.device_id if client else "",
                "pairing_seconds": self.devices.pairing_seconds_left()}

    def notify(self, event: dict) -> None:
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception:
                log.debug("a phone-link listener failed", exc_info=True)

    def begin_pairing(self) -> tuple[PairingOffer, str]:
        """Open a pairing and return it with the text for the QR code (the phone also accepts it pasted)."""
        offer = self.devices.begin_pairing()
        payload = {"v": 2, "host": self.bound_host, "port": self.port, "code": offer.code, "name": platform.node()}
        return offer, json.dumps(payload, separators=(",", ":"))

    def pairing_payload(self, name: str = "") -> dict:
        """What the pairing QR code carries. The token inside is shown once; only its hash is kept."""
        device_id, token = self.devices.pair(name)
        return {"v": PROTOCOL, "host": self.bound_host, "port": self.port, "token": token, "id": device_id, "name": platform.node()}

    # ---- connection -------------------------------------------------------------
    def _authenticate(self, ws) -> tuple[str | None, str | None]:
        try:
            if ws.request.headers.get("Origin"):  # a web page, not the phone app
                ws.close(1008, "origin not allowed")
                return None, None
        except Exception:
            pass
        token = code = None
        name = "Phone"
        try:
            hello = json.loads(ws.recv(timeout=HELLO_TIMEOUT_S))
            if isinstance(hello, dict) and hello.get("type") == "hello":
                token, code = hello.get("token"), hello.get("pair_code")
                name = str(hello.get("device") or "Phone")[:40]
        except Exception:
            pass
        device_id, minted = None, None
        if isinstance(code, str) and code:
            made = self.devices.redeem(code, name)
            if made is not None:
                device_id, minted = made
                self.notify({"event": "paired", "name": name})
            failure = ("bad_pair_code", "That pairing code is not valid or has expired. Show a new one on the PC.")
        else:
            device_id = self.devices.verify(token) if isinstance(token, str) and token else None
            failure = ("not_paired", "This phone is not paired with this PC.")
        if device_id is None:
            try:
                ws.send(json.dumps({"type": "error", "code": failure[0], "message": failure[1]}))
                ws.close(1008, failure[0])
            except Exception:
                pass
            return None, None
        self.devices.touch(device_id)
        return device_id, minted

    def _handler(self, ws) -> None:
        device_id, minted = self._authenticate(ws)
        if device_id is None:
            return
        client = _Client(ws, device_id)
        with self._lock:
            old, self.client = self.client, client
        if old is not None:
            old.close(1000, "replaced by a newer connection")
        if minted is not None:  # first connection of a newly paired phone: hand over its own permanent token
            client.send({"type": "paired", "token": minted, "device_id": device_id, "name": platform.node()})
        self.notify({"event": "connected", "device_id": device_id})
        client.send({"type": "ready", "protocol": PROTOCOL, "state": self.session.state.value, "name": platform.node(),
                     "output": self.cfg.remote_output})
        try:
            for raw in ws:
                self._on_message(client, raw)
        except Exception:
            log.debug("phone connection ended", exc_info=True)
        finally:
            self.notify({"event": "disconnected", "device_id": device_id})
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
        elif kind == "camera_video_begin":
            client.video_begin(msg)
        elif kind == "camera_video_frame":
            client.video_frame(msg)
        elif kind == "camera_video_audio":
            client.video_audio(msg)
        elif kind == "camera_video_end":
            client.video_end(msg)
        elif kind == "camera_cancel":
            client.resolve_camera(str(msg.get("id", "")), None, str(msg.get("reason") or "cancelled")[:80])
        elif kind == "camera_pending":
            if not msg.get("opened"):  # the screen is off: Android will not let the app open the camera by itself
                self.session.announce("Tap the notification on your phone to take the photo.")
        elif kind == "text":
            self.session.submit_text(str(msg.get("text", ""))[:2000])
        elif kind == "share_file":
            reply = self.shares.piece(msg) if self.shares is not None else {"type": "share_saved", "id": str(msg.get("id", ""))[:64], "ok": False,
                                                                            "message": "This PC cannot take shared files right now."}
            if reply is not None:
                client.send(reply)
        elif kind == "phone_action_result":
            client.resolve_action(str(msg.get("id", "")), bool(msg.get("ok")), str(msg.get("message", ""))[:MAX_ACTION_REPLY_CHARS])
        elif kind == "forget_me":  # the phone removed this PC: take the phone off the paired list too
            self.devices.revoke(client.device_id)
            client.close(1000, "forgotten")
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

    def request_video(self, prompt: str = "", max_seconds: int = 20, max_frames: int = 100, max_edge: int = 640, max_fps: float = 2.0, max_bytes: int = 5_000_000,
                      timeout: float | None = None, audio: bool = False, detail_frames: int = 0) -> tuple["PhoneVideo | None", str]:
        """Have the connected phone record a short clip and return its frames: (PhoneVideo, "ok") or (None, why)."""
        client = self.client
        if client is None or not client.alive:
            return None, "no_phone"
        abandoned = lambda: self.session.state in (State.IDLE, State.LISTENING)  # noqa: E731
        wait = timeout if timeout is not None else CAMERA_WAIT_S + max_seconds + 45 + max_bytes / 1_000_000 * 3  # a bigger budget takes longer to arrive
        return client.request_video(prompt, max_seconds, max_frames, max_edge, max_fps, max_bytes, wait, abandoned, audio, detail_frames)

    def request_action(self, action: str, args: dict, timeout: float = ACTION_WAIT_S) -> tuple[bool, str]:
        """Have the connected phone carry out an action after the user agrees there: (done, message), or (False, "no_phone" / "timeout" / ...)."""
        client = self.client
        if client is None or not client.alive:
            return False, "no_phone"
        abandoned = lambda: self.session.state in (State.IDLE, State.LISTENING)  # noqa: E731
        return client.request_action(action, args, timeout, abandoned)

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


def phone_link_checks(cfg, server: "RemoteServer | None", find_ip=tailscale_ip) -> list[dict]:
    """What the Phone page shows: each line says whether something works, and what to do when it does not. ok is True, False, or None (not applicable yet)."""
    ip = find_ip()
    checks = [{
        "id": "tailscale", "title": "Tailscale",
        "ok": ip is not None,
        "detail": f"Running, this PC is {ip}" if ip else "Not running on this PC.",
        "fix": "" if ip else "Install Tailscale from tailscale.com/download, sign in, then come back here.",
    }]
    running = server is not None and server.running
    if running:
        checks.append({"id": "link", "title": "Phone link", "ok": True, "detail": f"On, waiting at {server.bound_host}:{server.port}", "fix": ""})
    else:
        why = server.error if server is not None and server.error else "Off."
        checks.append({"id": "link", "title": "Phone link", "ok": False, "detail": why,
                       "fix": "Press \"Turn on the phone link\"." if ip else "Fix Tailscale first, then turn the phone link on."})
    if running:
        paired = len(server.devices.list())
        client = server.client
        if client is not None and client.alive:
            who = next((d["name"] for d in server.devices.list() if d["id"] == client.device_id), "A phone")
            checks.append({"id": "phone", "title": "Phone", "ok": True, "detail": f"{who} is connected.", "fix": ""})
        else:
            checks.append({"id": "phone", "title": "Phone", "ok": None,
                           "detail": ("No phone connected right now." if paired else "No phone paired yet."),
                           "fix": "" if paired else "Press \"Pair a new phone\" and scan the code with the app."})
    return checks
