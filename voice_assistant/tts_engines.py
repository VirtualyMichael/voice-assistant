"""More lifelike voice engines: Pocket TTS (local, copies a voice from a short recording), any OpenAI-compatible speech server, ElevenLabs.

Each engine has the same shape as the built-in ones (engine, voices, speed, set_voice, __call__(text) -> (float32 samples, rate)) plus a voice-id
prefix, so MultiSynth can route "pocket:...", "server:..." and "eleven:..." ids. FallbackSynth puts the built-in voice behind each of them so a
failing engine never leaves a sentence silent. Nothing here touches the network at import time; keys come from Windows Credential Manager.
"""
from __future__ import annotations

import logging
import re
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

from .models import ModelsMissing, Voice

log = logging.getLogger(__name__)

PCM_RATE = 24000  # OpenAI "pcm" and ElevenLabs "pcm_24000": 24 kHz, 16-bit, mono, little-endian
SERVER_KEY, ELEVEN_KEY = "tts_server", "elevenlabs"  # names in Credential Manager (service "VoiceAssistant")
ELEVEN_API = "https://api.elevenlabs.io/v1"
OPENAI_VOICES = "alloy, ash, ballad, coral, echo, fable, nova, onyx, sage, shimmer, verse"
MAX_REFERENCE_S = 30.0


def _timeout():
    import httpx

    return httpx.Timeout(30.0, connect=5.0)


# ---- audio decoding -------------------------------------------------------------
def parse_wav(data: bytes) -> tuple[np.ndarray, int]:
    """A RIFF/WAVE file as mono float32 in -1..1. Integer PCM (8/16/24/32-bit) and 32-bit float. A data size of 0 or 0xFFFFFFFF (written by
    servers that stream the file before they know its length) means "to the end"."""
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a WAV file")
    fmt, pos = None, 12
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], "little")
        body = pos + 8
        if cid == b"fmt ":
            tag, ch, sr = int.from_bytes(data[body:body + 2], "little"), int.from_bytes(data[body + 2:body + 4], "little"), int.from_bytes(data[body + 4:body + 8], "little")
            bits = int.from_bytes(data[body + 14:body + 16], "little")
            if tag == 0xFFFE and size >= 26:  # WAVE_FORMAT_EXTENSIBLE: the real format is the first two bytes of the sub-format GUID
                tag = int.from_bytes(data[body + 24:body + 26], "little")
            fmt = (tag, max(ch, 1), sr, bits)
        elif cid == b"data":
            if fmt is None:
                raise ValueError("WAV data before its format")
            end = len(data) if size in (0, 0xFFFFFFFF) else min(body + size, len(data))
            return _pcm(data[body:end], *fmt)
        pos = body + size + (size & 1)
    raise ValueError("WAV file has no sound data")


def _pcm(raw: bytes, tag: int, ch: int, sr: int, bits: int) -> tuple[np.ndarray, int]:
    width = bits // 8
    raw = raw[: len(raw) - len(raw) % (width * ch)] if width else b""
    if tag == 3 and bits == 32:
        x = np.frombuffer(raw, "<f4").astype(np.float32)
    elif tag == 1 and bits == 8:
        x = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif tag == 1 and bits == 16:
        x = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    elif tag == 1 and bits == 24:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        x = ((b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)) << 8 >> 8).astype(np.float32) / 8388608.0
    elif tag == 1 and bits == 32:
        x = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"unsupported WAV format (type {tag}, {bits}-bit)")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return np.ascontiguousarray(x, dtype=np.float32), sr


def decode_audio(data: bytes, rate: int = PCM_RATE) -> tuple[np.ndarray, int]:
    """A speech reply: a WAV file when it starts with a RIFF header, otherwise raw 16-bit little-endian mono PCM at `rate`."""
    if data[:4] == b"RIFF":
        return parse_wav(data)
    n = len(data) - len(data) % 2
    return np.frombuffer(data[:n], "<i2").astype(np.float32) / 32768.0, rate


# ---- keys -------------------------------------------------------------------------
def _get(provider: str, env: str = "") -> str:
    from .providers import get_key

    return get_key(provider, env)


def is_openai(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() == "api.openai.com"


def server_key(url: str, openai_env: str = "OPENAI_API_KEY", get=_get) -> str:
    """The speech server's own saved key; for api.openai.com without one, the OpenAI key the chat already uses (saved, then its environment variable)."""
    return get(SERVER_KEY, "") or (get("openai", openai_env) if is_openai(url) else "")


def eleven_key(get=_get) -> str:
    return get(ELEVEN_KEY, "ELEVENLABS_API_KEY")


# ---- engines --------------------------------------------------------------------
def _pick_part(folder: Path, key: str, ext: str) -> str:
    exact = folder / f"{key}{ext}"
    if exact.exists():
        return str(exact)
    found = sorted(folder.rglob(f"*{key}*{ext}"))
    if not found:
        raise ModelsMissing(f"No *{key}*{ext} in {folder}")
    int8 = [f for f in found if "int8" in f.name]
    return str((int8 or found)[0])


def make_pocket_tts(folder: Path, threads: int):
    """The sherpa-onnx Pocket TTS model in `folder`. File names are matched by the config's field names (lm_flow, lm_main, ...)."""
    import sherpa_onnx

    pc = sherpa_onnx.OfflineTtsPocketModelConfig(
        lm_flow=_pick_part(folder, "lm_flow", ".onnx"), lm_main=_pick_part(folder, "lm_main", ".onnx"),
        encoder=_pick_part(folder, "encoder", ".onnx"), decoder=_pick_part(folder, "decoder", ".onnx"),
        text_conditioner=_pick_part(folder, "text_conditioner", ".onnx"),
        vocab_json=_pick_part(folder, "vocab", ".json"), token_scores_json=_pick_part(folder, "token_scores", ".json"),
    )
    cfg = sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(pocket=pc, num_threads=threads, provider="cpu"))
    if not cfg.validate():  # an invalid config can abort the whole program inside sherpa-onnx instead of raising
        raise ModelsMissing(f"The Pocket TTS files in {folder} are incomplete")
    return sherpa_onnx.OfflineTts(cfg)


def _voice_name(stem: str) -> str:
    return re.sub(r"[_\-]+", " ", stem).strip() or stem


def pocket_voice_files(folder: Path, user_dir: Path | None) -> dict[str, Path]:
    """id -> recording: the model's own sample recordings, then the ones you added (a name already taken keeps the first)."""
    by_name = lambda p: p.stem.lower()  # noqa: E731
    files = sorted(folder.rglob("*.wav"), key=by_name) if folder.is_dir() else []
    if user_dir is not None and user_dir.is_dir():
        files += sorted(user_dir.glob("*.wav"), key=by_name)
    out: dict[str, Path] = {}
    for f in files:
        out.setdefault(f"pocket:{f.stem}", f)
    return out


def add_voice_file(src: Path, user_dir: Path) -> Path:
    """Copy a recording into the voices folder (checked to be a readable WAV with at least a second of sound) under a free name."""
    try:
        samples, sr = parse_wav(src.read_bytes())
    except (OSError, ValueError) as e:
        raise ValueError(f"That file can't be used as a voice: {e}") from e
    if sr <= 0 or len(samples) < sr:
        raise ValueError("That recording is shorter than a second. Use 5 to 15 seconds of clear speech.")
    user_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w\- ]+", "", src.stem).strip() or "voice"
    dest, n = user_dir / f"{stem}.wav", 2
    while dest.exists():
        dest, n = user_dir / f"{stem}-{n}.wav", n + 1
    shutil.copyfile(src, dest)
    return dest


class PocketSynth:
    """Pocket TTS by Kyutai through sherpa-onnx: speaks in the voice of a short recording. The model loads on first use (or load_quietly)."""

    engine, prefix = "pocket", "pocket:"

    def __init__(self, folder: Path, user_dir: Path | None, speed: float = 1.0, threads: int = 4, make=make_pocket_tts):
        self.folder, self.user_dir, self.speed, self.threads, self._make = folder, user_dir, speed, threads, make
        self._tts, self._ref, self._cur = None, None, None
        self._lock = threading.Lock()
        self.refresh_voices()

    def refresh_voices(self) -> None:
        self._files = pocket_voice_files(self.folder, self.user_dir)
        self.voices = [Voice(vid, f"{_voice_name(p.stem)} (Pocket)", "other", n) for n, (vid, p) in enumerate(self._files.items())]

    def load(self):
        with self._lock:
            if self._tts is None:
                self._tts = self._make(self.folder, self.threads)
            return self._tts

    def load_quietly(self) -> None:
        try:
            self.load()
        except Exception:
            log.warning("could not load the Pocket voices", exc_info=True)

    def set_voice(self, voice_id: str) -> Voice:
        v = next((v for v in self.voices if v.id == voice_id), self.voices[0] if self.voices else Voice(voice_id, "Pocket", "other", 0))
        if v is not self._cur:
            self._cur, self._ref = v, None
        return v

    def _reference(self) -> tuple[list, int]:
        if self._ref is None:
            if self._cur is None:
                self.set_voice("")
            path = self._files.get(self._cur.id) if self._cur else None
            if path is None:
                raise ModelsMissing("No Pocket voice recording. Add one with 'Add a voice from a recording'.")
            samples, sr = parse_wav(path.read_bytes())
            self._ref = (samples[: int(sr * MAX_REFERENCE_S)].tolist(), sr)
        return self._ref

    def __call__(self, text: str) -> tuple[np.ndarray, int]:
        import sherpa_onnx

        tts = self.load()
        ref, sr = self._reference()
        g = sherpa_onnx.GenerationConfig()
        g.reference_audio, g.reference_sample_rate, g.speed = ref, sr, self.speed
        audio = tts.generate(text, g)
        return np.asarray(audio.samples, dtype=np.float32), audio.sample_rate


class SpeechServerSynth:
    """Any server with OpenAI's POST /v1/audio/speech: OpenAI itself, or Kokoro-FastAPI, Orpheus-FastAPI, Chatterbox-TTS-API, Fish Speech on this PC."""

    engine, prefix = "server", "server:"

    def __init__(self, url: str, model: str, voices: str, instructions: str = "", key: str = "", speed: float = 1.0, client=None):
        import httpx

        base = url.rstrip("/")
        self.url = base if base.endswith("/audio/speech") else base + "/audio/speech"
        self.model, self.instructions, self.speed = model.strip(), instructions.strip(), speed
        self._headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._client = client or httpx.Client(timeout=_timeout())
        names = [n.strip() for n in voices.split(",") if n.strip()]
        self.voices = [Voice(f"server:{n}", f"{n} (Speech server)", "other", i) for i, n in enumerate(names)]
        self.voice = names[0] if names else ""

    def set_voice(self, voice_id: str) -> Voice:
        v = next((v for v in self.voices if v.id == voice_id), None)
        if v is None and voice_id.startswith(self.prefix):  # a voice the server has that is not in the list
            v = Voice(voice_id, f"{voice_id[len(self.prefix):]} (Speech server)", "other", 0)
        v = v or (self.voices[0] if self.voices else Voice("server:", "Speech server", "other", 0))
        self.voice = v.id[len(self.prefix):]
        return v

    def request(self, text: str) -> dict:
        body = {"model": self.model, "input": text, "voice": self.voice, "response_format": "pcm"}
        if self.instructions:
            body["instructions"] = self.instructions
        if abs(self.speed - 1.0) > 1e-3:
            body["speed"] = round(self.speed, 2)
        return body

    def __call__(self, text: str) -> tuple[np.ndarray, int]:
        r = self._client.post(self.url, json=self.request(text), headers=self._headers)
        r.raise_for_status()
        return decode_audio(r.content)


class ElevenLabsSynth:
    """ElevenLabs (online): your account's voices, listed once in the background. Reply text is sent to ElevenLabs."""

    engine, prefix = "elevenlabs", "eleven:"

    def __init__(self, key: str, model: str = "eleven_flash_v2_5", speed: float = 1.0, client=None, on_voices=None, fetch: bool = True):
        import httpx

        self.model, self.speed, self._on_voices = model.strip() or "eleven_flash_v2_5", speed, on_voices
        self._headers = {"xi-api-key": key}
        self._client = client or httpx.Client(timeout=_timeout())
        self._voices: list[Voice] = []
        self._loaded = threading.Event()
        self.voice_id = ""
        if fetch:
            threading.Thread(target=self.fetch_voices, daemon=True, name="elevenlabs-voices").start()

    @property
    def voices(self) -> list[Voice]:
        return list(self._voices)

    def fetch_voices(self) -> None:
        import httpx

        try:
            r = self._client.get(f"{ELEVEN_API}/voices", headers=self._headers)
            r.raise_for_status()
            items = [v for v in r.json().get("voices", []) if isinstance(v, dict) and v.get("voice_id")]
            self._voices = [Voice(f"eleven:{v['voice_id']}", f"{v.get('name') or v['voice_id']} (ElevenLabs)", "other", i) for i, v in enumerate(items)]
        except (httpx.HTTPError, ValueError, AttributeError) as e:
            code = getattr(getattr(e, "response", None), "status_code", "")
            log.warning("Could not list your ElevenLabs voices (%s %s)", e.__class__.__name__, code)
            self._voices = []
        finally:
            self._loaded.set()
            if self._on_voices:
                self._on_voices()

    def wait_voices(self, timeout: float) -> bool:
        return self._loaded.wait(timeout)

    def set_voice(self, voice_id: str) -> Voice:
        v = next((v for v in self._voices if v.id == voice_id), None)
        if v is None and voice_id.startswith(self.prefix):  # chosen earlier; the list may still be loading
            v = Voice(voice_id, "ElevenLabs voice", "other", 0)
        v = v or (self._voices[0] if self._voices else Voice("eleven:", "ElevenLabs", "other", 0))
        self.voice_id = v.id[len(self.prefix):]
        return v

    def __call__(self, text: str) -> tuple[np.ndarray, int]:
        vid = self.voice_id or (self._voices[0].id[len(self.prefix):] if self._voices else "")
        if not vid:
            raise ModelsMissing("No ElevenLabs voice available")
        r = self._client.post(f"{ELEVEN_API}/text-to-speech/{vid}/stream", params={"output_format": f"pcm_{PCM_RATE}"},
                              json={"text": text, "model_id": self.model}, headers=self._headers)
        r.raise_for_status()
        return decode_audio(r.content, PCM_RATE)


class FallbackSynth:
    """An engine with the built-in voice behind it: a sentence the engine cannot speak (error or timeout) is spoken by the backup instead.
    After `max_failures` failures in a row the backup speaks everything for `pause_s` seconds, so a dead server does not cost a timeout per sentence."""

    def __init__(self, primary, backup, clock=time.monotonic, max_failures: int = 3, pause_s: float = 60.0):
        self.primary, self.backup, self._clock = primary, backup, clock
        self.max_failures, self.pause_s = max_failures, pause_s
        self.failures, self.paused_until = 0, 0.0

    def __getattr__(self, name):  # engine, voices, prefix, refresh_voices, wait_voices, load_quietly...
        return getattr(self.primary, name)

    @property
    def speed(self) -> float:
        return self.primary.speed

    @speed.setter
    def speed(self, value: float) -> None:
        self.primary.speed = value

    def set_voice(self, voice_id: str) -> Voice:
        return self.primary.set_voice(voice_id)

    def __call__(self, text: str) -> tuple[np.ndarray, int]:
        if self.backup is not None and self._clock() < self.paused_until:
            return self.backup(text)
        try:
            out = self.primary(text)
        except Exception as e:
            self.failures += 1
            name = getattr(self.primary, "engine", "voice")
            if self.failures >= self.max_failures:
                self.failures, self.paused_until = 0, self._clock() + self.pause_s
                log.warning("The %s voice failed %d times in a row (%s); using the built-in voice for %d seconds", name, self.max_failures, e.__class__.__name__, self.pause_s)
            else:
                log.warning("The %s voice failed (%s); this sentence uses the built-in voice", name, e.__class__.__name__)
            if self.backup is None:
                raise
            return self.backup(text)
        self.failures = 0
        return out


def voice_engines(cfg, models_dir: Path, voices_dir: Path | None, on_voices=None, get=_get) -> list:
    """The extra engines that are set up: Pocket when its model folder exists, the speech server when it has an address (and a key for
    api.openai.com), ElevenLabs when its key is saved. An engine that is not set up is simply absent."""
    out: list = []
    pocket = Path(models_dir) / "pocket"
    if pocket.is_dir():
        p = PocketSynth(pocket, voices_dir, cfg.tts_speed, cfg.tts_threads)
        if p.voices:
            out.append(p)
    url = (cfg.tts_server_url or "").strip()
    if url:
        key = server_key(url, cfg.openai_key_env, get)
        if key or not is_openai(url):
            out.append(SpeechServerSynth(url, cfg.tts_server_model, cfg.tts_server_voices, cfg.tts_server_instructions, key, cfg.tts_speed))
    key = eleven_key(get)
    if key:
        out.append(ElevenLabsSynth(key, cfg.elevenlabs_model, cfg.tts_speed, on_voices=on_voices))
    return out
