"""The sound of a video as words: split it at the pauses, transcribe each stretch, and say what the rest of the sound seems to be.

The audio arrives as 16 kHz mono 16-bit samples (the phone sends it that way; for a dropped video the helper process decodes it, see video_look.extract_audio).
Transcription uses the recognizer already on this PC; nothing is sent anywhere by this module. Speech recognizers invent words for music, so the
transcript is marked as automatic and the model is told that lyrics and noise can look like speech.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

SAMPLE_RATE = 16000
MAX_AUDIO_BYTES = 8_000_000  # four minutes at 16 kHz, 16-bit mono
FRAME_S = 0.03
AUDIBLE = 0.004  # RMS of a 30 ms frame that counts as sound at all (about -48 dB below full scale); quiet music from a TV across a room is above it


@dataclass
class Utterance:
    start: float
    end: float
    text: str


@dataclass
class SoundReport:
    seconds: float
    loud_fraction: float  # share of the clip with clearly audible sound
    utterances: list[Utterance]
    level_db: float = -99.0  # overall loudness, dB below full scale (0 is as loud as it gets)
    bpm: float = 0.0  # a steady beat in the sound, if there is one (music, game audio, a metronome)
    beat_strength: float = 0.0


def to_float(pcm: bytes) -> np.ndarray:
    usable = len(pcm) - (len(pcm) % 2)
    return np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32) / 32768.0


def _energy(samples: np.ndarray, frame_s: float = FRAME_S) -> np.ndarray:
    n = int(SAMPLE_RATE * frame_s)
    frames = len(samples) // n
    if frames == 0:
        return np.zeros(0, np.float32)
    return np.sqrt(np.mean(samples[: frames * n].reshape(frames, n) ** 2, axis=1))


def _threshold(floor: float) -> float:
    """What counts as sound: well above the quiet background, but never so high that a steady loud sound (music, a tone) is treated as silence."""
    return max(min(floor * 3.0, 0.05), 0.008)


def speech_segments(samples: np.ndarray, min_pause_s: float = 0.45, max_segment_s: float = 14.0, min_segment_s: float = 0.35) -> list[tuple[float, float]]:
    """Stretches of sound separated by pauses, each at most `max_segment_s` long (split at the quietest moment near the limit)."""
    e = _energy(samples)
    if len(e) == 0:
        return []
    floor = float(np.percentile(e, 20))
    threshold = _threshold(floor)
    active = e > threshold
    pause_frames = max(int(min_pause_s / FRAME_S), 1)
    segs: list[list[int]] = []
    i = 0
    while i < len(active):
        if active[i]:
            j, quiet = i, 0
            while j < len(active) and quiet < pause_frames:
                quiet = 0 if active[j] else quiet + 1
                j += 1
            segs.append([i, j - quiet])
            i = j
        else:
            i += 1
    out: list[tuple[float, float]] = []
    limit = int(max_segment_s / FRAME_S)
    for a, b in segs:
        while b - a > limit:
            lo, hi = a + limit // 2, a + limit
            cut = lo + int(np.argmin(e[lo:hi]))
            out.append((a * FRAME_S, cut * FRAME_S))
            a = cut
        if (b - a) * FRAME_S >= min_segment_s:
            out.append((a * FRAME_S, b * FRAME_S))
    return out


def tempo(samples: np.ndarray) -> tuple[float, float]:
    """(beats per minute, strength 0..1) of the steadiest beat in the loudness over time, or (0, 0) when there is none. Looks for 60 to 200 beats a minute."""
    hop = 0.01
    e = _energy(samples, hop)
    if len(e) < int(4 / hop):
        return 0.0, 0.0
    onset = np.maximum(np.diff(e), 0.0)
    onset = onset - onset.mean()
    if not np.any(onset):
        return 0.0, 0.0
    ac = np.correlate(onset, onset, mode="full")[len(onset) - 1:]
    if ac[0] <= 0:
        return 0.0, 0.0
    ac = ac / ac[0]
    lo, hi = int(60.0 / 200.0 / hop), int(60.0 / 60.0 / hop)
    lag = lo + int(np.argmax(ac[lo:hi]))
    return 60.0 / (lag * hop), float(max(ac[lag], 0.0))


def transcribe(pcm: bytes, recognize: Callable[[np.ndarray], str], offset_s: float = 0.0) -> SoundReport:
    """Words with their times. `recognize` turns a stretch of float samples into text (the offline recognizer's transcribe, or a streaming one)."""
    samples = to_float(pcm[:MAX_AUDIO_BYTES])
    seconds = len(samples) / SAMPLE_RATE
    e = _energy(samples)
    loud = float(np.mean(e > AUDIBLE)) if len(e) else 0.0  # by absolute level: steady quiet music has no quiet moments to compare with
    rms = float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0
    level = 20.0 * float(np.log10(max(rms, 1e-6)))
    bpm, strength = tempo(samples)
    words: list[Utterance] = []
    for start, end in speech_segments(samples):
        pad = int(0.15 * SAMPLE_RATE)
        part = samples[max(int(start * SAMPLE_RATE) - pad, 0): int(end * SAMPLE_RATE) + pad]
        try:
            text = recognize(part).strip()
        except Exception:
            text = ""
        if text:
            words.append(Utterance(start + offset_s, end + offset_s, text))
    return SoundReport(seconds, loud, words, level, bpm, strength)


def clock(seconds: float) -> str:
    s = max(int(round(seconds)), 0)
    return f"{s // 60}:{s % 60:02d}"


def _loudness(db: float) -> str:
    return "loud" if db > -20 else "moderate" if db > -35 else "quiet"


def _beat(report: SoundReport) -> str:
    return f"a steady beat around {report.bpm:.0f} beats a minute" if report.bpm and report.beat_strength >= 0.3 else ""


def describe(report: SoundReport) -> str:
    """The sound as text for the timeline: what is said, and what the rest of the sound seems to be (a beat means music, game audio or a machine)."""
    if report.seconds < 0.5:
        return "Sound: none recorded."
    if report.loud_fraction < 0.05:
        return "Sound: almost silent."
    beat = _beat(report)
    feel = f"{_loudness(report.level_db)} sound for about {int(report.loud_fraction * 100)}% of the clip" + (f", with {beat}" if beat else "")
    if not report.utterances:
        meaning = "that points to music or game audio" if beat and report.loud_fraction > 0.5 else "it may be music, game audio or background noise"
        return f"Sound: {feel}; no recognisable speech ({meaning}). Only speech is turned into words, so music without words cannot be described further."
    lines = [f"[{clock(u.start)}\u2013{clock(u.end)}] {u.text}" for u in report.utterances]
    return f"Sound ({feel}). Automatic speech transcript; speech recognisers can mishear, and music or game audio may appear as odd words:\n" + "\n".join(lines)
