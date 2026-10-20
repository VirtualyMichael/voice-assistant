"""Model discovery, loading (sherpa-onnx) and opt-in download.

Layout under models_dir:  asr/  tts/  kws/   (each holds one extracted model's files)
"""
from __future__ import annotations

import re
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
    ModelSpec("asr2", "Parakeet TDT 0.6B v2 (accurate recognizer)", f"{REL}/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8.tar.bz2", "far fewer mis-heard words (recommended)", size_mb=460, core=False),
    ModelSpec("kws", "zipformer gigaspeech 3.3M", f"{REL}/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2", "wake word (optional)", size_mb=17, core=False),
    ModelSpec("kokoro", "Kokoro English v0.19 (11 natural voices)", f"{REL}/tts-models/kokoro-en-v0_19.tar.bz2", "natural-sounding voices (recommended)", size_mb=305, core=False),
    ModelSpec("supertonic", "Supertonic v3 (natural voices)", f"{REL}/tts-models/sherpa-onnx-supertonic-3-tts-int8-2026-05-11.tar.bz2", "more natural voices; MIT licence for the code, check the model card", size_mb=128, core=False),
    ModelSpec("piper_kusal", "Piper Kusal (US male)", f"{REL}/tts-models/vits-piper-en_US-kusal-medium-int8.tar.bz2", "a natural male voice", size_mb=20, core=False),
    ModelSpec("kokoro2", "Kokoro v1.0 (53 voices, better male voices)", f"{REL}/tts-models/kokoro-multi-lang-v1_0.tar.bz2", "newer Kokoro: more and better male voices", size_mb=340, core=False),
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


def wake_variants(phrase: str) -> list[str]:
    """A one-word phrase is short and easy to miss, so also listen for "hey <word>" and "ok <word>" (measured: 82% -> 100% for "Hey Jarvis")."""
    p = clean_phrase(phrase)
    return [p] if " " in p else [p, f"HEY {p}", f"OK {p}"]


def clean_phrase(phrase: str) -> str:
    """Letters, digits, apostrophes and single spaces only, upper case. A comma in "Hey, assistant" is not a token the model knows,
    and sherpa-onnx then aborts the whole program instead of raising an error."""
    return " ".join(re.sub(r"[^A-Za-z0-9' ]+", " ", phrase).upper().split())


def write_keywords(folder: Path, phrase: str, boost: float = 1.5, threshold: float = 0.12) -> Path:
    """Tokenise a wake phrase with the model's BPE model into sherpa's keywords format.
    boost 1.5 / threshold 0.12 won a grid test on synthetic speech; a higher boost lowered detection."""
    import sentencepiece as spm

    bpe = folder / "bpe.model"
    if not bpe.exists():
        raise ModelsMissing(f"Missing {bpe}")
    if not phrase.strip():
        raise ValueError("wake_phrase is empty")
    sp = spm.SentencePieceProcessor(model_file=str(bpe))
    tok = folder / "tokens.txt"
    known = {ln.split()[0] for ln in tok.read_text(encoding="utf-8").splitlines() if ln.strip()} if tok.exists() else None
    lines = []
    for p in wake_variants(phrase):
        pieces = sp.encode(p, out_type=str)
        if known is not None and any(t not in known for t in pieces):
            raise ValueError(f"The wake phrase '{p}' contains sounds the wake model has no token for; use plain words")
        lines.append(f"{' '.join(pieces)} :{boost} #{threshold} @{p.replace(' ', '_')}\n")
    out = folder / "keywords_custom.txt"
    out.write_text("".join(lines), encoding="utf-8")
    return out


class AccurateRecognizer:
    """Offline (whole-sentence) recognizer used to re-check what the fast streaming model heard."""

    SAMPLE_RATE = 16000

    def __init__(self, folder: Path, num_threads: int = 4, feature_dim: int = 128):
        import sherpa_onnx

        d = _need(folder, "asr2")
        self._r = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=_pick(d, "encoder"), decoder=_pick(d, "decoder"), joiner=_pick(d, "joiner"), tokens=_tokens(d),
            num_threads=num_threads, sample_rate=self.SAMPLE_RATE, feature_dim=feature_dim,
            decoding_method="greedy_search", model_type="nemo_transducer",
        )

    def transcribe(self, samples: np.ndarray) -> str:
        s = self._r.create_stream()
        s.accept_waveform(self.SAMPLE_RATE, samples.astype(np.float32, copy=False))
        self._r.decode_stream(s)
        return s.result.text.strip()


class SherpaWakeWord:
    """Keyword spotting for a custom phrase (BPE-based KWS model in <models>/kws)."""

    SAMPLE_RATE = 16000

    def __init__(self, folder: Path, phrase: str, num_threads: int = 1, threshold: float = 0.12):
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
_V10 = [  # kokoro-multi-lang-v1_0 speaker table (English voices; ids from the sherpa-onnx docs)
    ("af_alloy", "Alloy (US)", "female"), ("af_aoede", "Aoede (US)", "female"), ("af_bella", "Bella (US)", "female"), ("af_heart", "Heart (US)", "female"),
    ("af_jessica", "Jessica (US)", "female"), ("af_kore", "Kore (US)", "female"), ("af_nicole", "Nicole (US)", "female"), ("af_nova", "Nova (US)", "female"),
    ("af_river", "River (US)", "female"), ("af_sarah", "Sarah (US)", "female"), ("af_sky", "Sky (US)", "female"),
    ("am_adam", "Adam (US)", "male"), ("am_echo", "Echo (US)", "male"), ("am_eric", "Eric (US)", "male"), ("am_fenrir", "Fenrir (US)", "male"),
    ("am_liam", "Liam (US)", "male"), ("am_michael", "Michael (US)", "male"), ("am_onyx", "Onyx (US)", "male"), ("am_puck", "Puck (US)", "male"),
    ("am_santa", "Santa (US)", "male"), ("bf_alice", "Alice (UK)", "female"), ("bf_emma", "Emma (UK)", "female"), ("bf_isabella", "Isabella (UK)", "female"),
    ("bf_lily", "Lily (UK)", "female"), ("bm_daniel", "Daniel (UK)", "male"), ("bm_fable", "Fable (UK)", "male"), ("bm_george", "George (UK)", "male"),
    ("bm_lewis", "Lewis (UK)", "male"),
]
KOKORO_V10 = [Voice(i, label, g, n) for n, (i, label, g) in enumerate(_V10)]
PIPER_AMY = [Voice("amy", "Amy (US)", "female", 0)]


class SherpaSynth:
    """Kokoro (natural, many voices) when <dir>/voices.bin exists, otherwise a Piper VITS voice."""

    def __init__(self, folder: Path, speed: float = 1.0, num_threads: int = 4, voice: str = "", custom: bool = False, voices: list | None = None):
        import sherpa_onnx

        d = _need(folder, "tts")
        espeak = str(d / "espeak-ng-data") if (d / "espeak-ng-data").exists() else ""
        if (d / "voices.bin").exists():
            v10 = (d / "lexicon-us-en.txt").exists()  # the newer multi-language Kokoro needs its English lexicon
            kokoro = sherpa_onnx.OfflineTtsKokoroModelConfig(
                model=_pick(d, "model"), voices=str(d / "voices.bin"), tokens=_tokens(d), data_dir=espeak,
                **({"lexicon": str(d / "lexicon-us-en.txt")} if v10 else {}),
            )
            mc = sherpa_onnx.OfflineTtsModelConfig(kokoro=kokoro, num_threads=num_threads, provider="cpu")
            self.voices, self.engine = (KOKORO_V10 if v10 else KOKORO_EN_V019), "kokoro"
        else:
            model = sorted(d.glob("*.onnx"))
            if not model:
                raise ModelsMissing(f"No .onnx voice in {d}")
            lexicon = d / "lexicon.txt"
            vits = sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(model[0]), tokens=_tokens(d), data_dir=espeak, lexicon=str(lexicon) if lexicon.exists() else "",
            )
            mc = sherpa_onnx.OfflineTtsModelConfig(vits=vits, num_threads=num_threads, provider="cpu")
            self.voices, self.engine = (voices or ([Voice("custom", "Your voice", "male", 0)] if custom else PIPER_AMY)), "piper"
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


SUPERTONIC_VOICES = [Voice(f"st{i}", f"Supertonic {i}", "male" if i in (1, 3, 7) else "other", i) for i in range(10)]  # 1, 3 and 7 were picked by ear as the male-sounding ones


class SupertonicSynth:
    """Supertonic v3 (10 voices, English here): natural, runs on the CPU. Needs <folder>/{duration_predictor,text_encoder,vector_estimator,vocoder}.int8.onnx etc."""

    engine = "supertonic"

    def __init__(self, folder: Path, speed: float = 1.0, num_threads: int = 4, voice: str = "", steps: int = 8):
        import sherpa_onnx

        d = _need(folder, "supertonic")
        st = sherpa_onnx.OfflineTtsSupertonicModelConfig(
            duration_predictor=str(d / "duration_predictor.int8.onnx"), text_encoder=str(d / "text_encoder.int8.onnx"),
            vector_estimator=str(d / "vector_estimator.int8.onnx"), vocoder=str(d / "vocoder.int8.onnx"),
            tts_json=str(d / "tts.json"), unicode_indexer=str(d / "unicode_indexer.bin"), voice_style=str(d / "voice.bin"),
        )
        self._tts = sherpa_onnx.OfflineTts(sherpa_onnx.OfflineTtsConfig(model=sherpa_onnx.OfflineTtsModelConfig(supertonic=st, num_threads=num_threads, provider="cpu")))
        self.voices, self.speed, self.steps, self.sid = SUPERTONIC_VOICES, speed, steps, 0
        self.set_voice(voice)

    def set_voice(self, voice_id: str) -> Voice:
        v = next((v for v in self.voices if v.id == voice_id), self.voices[1])
        self.sid = v.sid
        return v

    def __call__(self, text: str) -> tuple[np.ndarray, int]:
        import sherpa_onnx

        g = sherpa_onnx.GenerationConfig()
        g.sid, g.num_steps, g.speed = self.sid, self.steps, self.speed
        g.extra["lang"] = "en"
        audio = self._tts.generate(text, g)
        return np.asarray(audio.samples, dtype=np.float32), audio.sample_rate


class MultiSynth:
    """Several voice engines behind one voice list; picking a voice switches to its engine."""

    def __init__(self, engines: list):
        self.engines = engines
        self.voices = [v for e in engines for v in e.voices]
        self.engine = "+".join(e.engine for e in engines)
        self._cur = engines[0]
        self._speed = getattr(engines[0], "speed", 1.0)

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        self._speed = value
        for e in self.engines:
            e.speed = value

    def set_voice(self, voice_id: str) -> Voice:
        for e in self.engines:
            if any(v.id == voice_id for v in e.voices):
                self._cur = e
                return e.set_voice(voice_id)
        self._cur = self.engines[0]
        return self._cur.set_voice(voice_id)

    def __call__(self, text: str) -> tuple[np.ndarray, int]:
        return self._cur(text)


def load_synth(models_dir: Path, speed: float, threads: int, voice: str, custom_dir: str = ""):
    """The user's own voice folder if set, else the Kokoro model (models_dir/kokoro), else the Piper voice (models_dir/tts)."""
    if custom_dir and Path(custom_dir).is_dir():
        return SherpaSynth(Path(custom_dir), speed, threads, voice, custom=True)
    engines = []
    slot = next((s for s in ("kokoro2", "kokoro") if (models_dir / s).is_dir()), None)  # newest Kokoro first
    if slot:
        engines.append(SherpaSynth(models_dir / slot, speed, threads, voice))
    if (models_dir / "supertonic").is_dir():
        engines.append(SupertonicSynth(models_dir / "supertonic", speed, threads, voice))
    if (models_dir / "piper_kusal").is_dir():
        engines.append(SherpaSynth(models_dir / "piper_kusal", speed, threads, voice, voices=[Voice("kusal", "Kusal (US)", "male", 0)]))
    if not engines:
        return SherpaSynth(models_dir / "tts", speed, threads, voice)
    synth = engines[0] if len(engines) == 1 else MultiSynth(engines)
    synth.set_voice(voice)
    return synth


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
