"""Model discovery, loading (sherpa-onnx) and opt-in download.

Layout under models_dir:  asr/  tts/  kws/   (each holds one extracted model's files)
"""
from __future__ import annotations

import shutil
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

REL = "https://github.com/k2-fsa/sherpa-onnx/releases/download"


@dataclass(frozen=True)
class ModelSpec:
    slot: str
    name: str
    url: str
    note: str
    core: bool = True  # fetched by default; optional models only when named
    size_mb: int = 0


# Check each model's license before distributing a packaged build.
MODEL_SPECS = [
    ModelSpec("asr", "streaming zipformer en 20M", f"{REL}/asr-models/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17.tar.bz2", "speech recognition", size_mb=122),
    ModelSpec("tts", "piper en_US amy low", f"{REL}/tts-models/vits-piper-en_US-amy-low.tar.bz2", "basic voice (fallback)", size_mb=64),
    ModelSpec("kws", "zipformer gigaspeech 3.3M", f"{REL}/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2", "wake word (optional)", size_mb=17, core=False),
    ModelSpec("kokoro", "Kokoro English v0.19 (11 natural voices)", f"{REL}/tts-models/kokoro-en-v0_19.tar.bz2", "natural-sounding voices (recommended)", size_mb=305, core=False),
]


class ModelsMissing(RuntimeError):
    pass


def _pick(folder: Path, prefix: str) -> str:
    files = sorted(folder.glob(f"{prefix}*.onnx"))
    if not files:
        raise ModelsMissing(f"No {prefix}*.onnx in {folder}")
    int8 = [f for f in files if "int8" in f.name]
    return str((int8 or files)[0])


def _tokens(folder: Path) -> str:
    t = folder / "tokens.txt"
    if not t.exists():
        raise ModelsMissing(f"Missing {t}")
    return str(t)


def _need(folder: Path, slot: str) -> Path:
    if not folder.is_dir():
        raise ModelsMissing(f"{slot} model not found at {folder}. Run: python -m voice_assistant fetch-models")
    return folder


def _norm(text: str) -> str:
    text = text.strip().lower()
    return text[:1].upper() + text[1:]


class SherpaRecognizer:
    SAMPLE_RATE = 16000

    def __init__(self, folder: Path, num_threads: int = 2, end_silence_s: float = 0.8):
        import sherpa_onnx

        d = _need(folder, "asr")
        self._r = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=_tokens(d), encoder=_pick(d, "encoder"), decoder=_pick(d, "decoder"), joiner=_pick(d, "joiner"),
            num_threads=num_threads, sample_rate=self.SAMPLE_RATE, feature_dim=80, decoding_method="greedy_search",
            enable_endpoint_detection=True, rule1_min_trailing_silence=2.4,
            rule2_min_trailing_silence=end_silence_s, rule3_min_utterance_length=20.0,
        )
        self._s = self._r.create_stream()

    def accept(self, samples: np.ndarray) -> None:
        self._s.accept_waveform(self.SAMPLE_RATE, samples)
        while self._r.is_ready(self._s):
            self._r.decode_stream(self._s)

    def text(self) -> str:
        res = self._r.get_result(self._s)
        return _norm(res if isinstance(res, str) else res.text)

    def is_endpoint(self) -> bool:
        return self._r.is_endpoint(self._s)

    def reset(self) -> None:
        self._r.reset(self._s)


def write_keywords(folder: Path, phrase: str, boost: float = 2.0, threshold: float = 0.25) -> Path:
    """Tokenise a wake phrase with the model's BPE model into sherpa's keywords format."""
    import sentencepiece as spm

    bpe = folder / "bpe.model"
    if not bpe.exists():
        raise ModelsMissing(f"Missing {bpe}")
    phrase = " ".join(phrase.upper().split())
    if not phrase:
        raise ValueError("wake_phrase is empty")
    pieces = " ".join(spm.SentencePieceProcessor(model_file=str(bpe)).encode(phrase, out_type=str))
    out = folder / "keywords_custom.txt"
    out.write_text(f"{pieces} :{boost} #{threshold} @{phrase.replace(' ', '_')}\n", encoding="utf-8")
    return out


class SherpaWakeWord:
    """Keyword spotting for a custom phrase (BPE-based KWS model in <models>/kws)."""

    SAMPLE_RATE = 16000

    def __init__(self, folder: Path, phrase: str, num_threads: int = 1, threshold: float = 0.25):
        import sherpa_onnx

        d = _need(folder, "kws")
        kw = write_keywords(d, phrase, threshold=threshold)
        self._k = sherpa_onnx.KeywordSpotter(
            tokens=_tokens(d), encoder=_pick(d, "encoder"), decoder=_pick(d, "decoder"), joiner=_pick(d, "joiner"),
            keywords_file=str(kw), num_threads=num_threads,
        )
        self._s = self._k.create_stream()

    def accept(self, samples: np.ndarray) -> bool:
        self._s.accept_waveform(self.SAMPLE_RATE, samples)
        hit = False
        while self._k.is_ready(self._s):
            self._k.decode_stream(self._s)
            if self._k.get_result(self._s):
                hit = True
                self._k.reset_stream(self._s)
        return hit


@dataclass(frozen=True)
class Voice:
    id: str
    label: str
    gender: str  # "female" | "male"
    sid: int


KOKORO_EN_V019 = [
    Voice("af", "Default (US)", "female", 0), Voice("af_bella", "Bella (US)", "female", 1),
    Voice("af_nicole", "Nicole (US)", "female", 2), Voice("af_sarah", "Sarah (US)", "female", 3),
    Voice("af_sky", "Sky (US)", "female", 4), Voice("bf_emma", "Emma (UK)", "female", 7),
    Voice("bf_isabella", "Isabella (UK)", "female", 8),
    Voice("am_adam", "Adam (US)", "male", 5), Voice("am_michael", "Michael (US)", "male", 6),
    Voice("bm_george", "George (UK)", "male", 9), Voice("bm_lewis", "Lewis (UK)", "male", 10),
]
PIPER_AMY = [Voice("amy", "Amy (US)", "female", 0)]


class SherpaSynth:
    """Kokoro (natural, many voices) when <dir>/voices.bin exists, otherwise a Piper VITS voice."""

    def __init__(self, folder: Path, speed: float = 1.0, num_threads: int = 4, voice: str = ""):
        import sherpa_onnx

        d = _need(folder, "tts")
        espeak = str(d / "espeak-ng-data") if (d / "espeak-ng-data").exists() else ""
        if (d / "voices.bin").exists():
            kokoro = sherpa_onnx.OfflineTtsKokoroModelConfig(
                model=_pick(d, "model"), voices=str(d / "voices.bin"), tokens=_tokens(d), data_dir=espeak,
            )
            mc = sherpa_onnx.OfflineTtsModelConfig(kokoro=kokoro, num_threads=num_threads, provider="cpu")
            self.voices, self.engine = KOKORO_EN_V019, "kokoro"
        else:
            model = sorted(d.glob("*.onnx"))
            if not model:
                raise ModelsMissing(f"No .onnx voice in {d}")
            lexicon = d / "lexicon.txt"
            vits = sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(model[0]), tokens=_tokens(d), data_dir=espeak, lexicon=str(lexicon) if lexicon.exists() else "",
            )
            mc = sherpa_onnx.OfflineTtsModelConfig(vits=vits, num_threads=num_threads, provider="cpu")
            self.voices, self.engine = PIPER_AMY, "piper"
        self._tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=mc, max_num_sentences=1))
        self.speed, self.sid = speed, 0
        self.set_voice(voice)
        self("Hello.")  # warm-up so the first real sentence is not slow

    def set_voice(self, voice_id: str) -> Voice:
        v = next((v for v in self.voices if v.id == voice_id), self.voices[0])
        self.sid = v.sid
        return v

    def __call__(self, text: str) -> tuple[np.ndarray, int]:
        audio = self._tts.generate(text, sid=self.sid, speed=self.speed)
        return np.asarray(audio.samples, dtype=np.float32), audio.sample_rate


def load_synth(models_dir: Path, speed: float, threads: int, voice: str) -> SherpaSynth:
    """Prefer the Kokoro model (models_dir/kokoro) and fall back to the Piper voice (models_dir/tts)."""
    slot = "kokoro" if (models_dir / "kokoro").is_dir() else "tts"
    return SherpaSynth(models_dir / slot, speed, threads, voice)


def fetch_model(spec: ModelSpec, models_dir: Path, progress=None) -> Path:
    """Download and extract one model into models_dir/<slot>. Caller must have the user's consent."""
    dest = models_dir / spec.slot
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "m.tar.bz2"
        hook = (lambda blocks, size, total: progress(min(blocks * size / total, 1.0)) if total > 0 else None) if progress else None
        urllib.request.urlretrieve(spec.url, archive, hook)  # noqa: S310 - fixed https URLs above
        out = Path(tmp) / "x"
        with tarfile.open(archive) as tf:
            tf.extractall(out, filter="data")
        top = [p for p in out.iterdir()]
        src = top[0] if len(top) == 1 and top[0].is_dir() else out
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), dest)
    return dest
