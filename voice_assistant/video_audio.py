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


def to_float(pcm: bytes) -> np.ndarray:
    usable = len(pcm) - (len(pcm) % 2)
    return np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32) / 32768.0


def _energy(samples: np.ndarray) -> np.ndarray:
    n = int(SAMPLE_RATE * FRAME_S)
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


def transcribe(pcm: bytes, recognize: Callable[[np.ndarray], str], offset_s: float = 0.0) -> SoundReport:
    """Words with their times. `recognize` turns a stretch of float samples into text (the offline recognizer's transcribe, or a streaming one)."""
    samples = to_float(pcm[:MAX_AUDIO_BYTES])
    seconds = len(samples) / SAMPLE_RATE
    e = _energy(samples)
    loud = float(np.mean(e > _threshold(float(np.percentile(e, 20))))) if len(e) else 0.0
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
    return SoundReport(seconds, loud, words)


def clock(seconds: float) -> str:
    s = max(int(round(seconds)), 0)
    return f"{s // 60}:{s % 60:02d}"


def describe(report: SoundReport) -> str:
    """The sound as text for the timeline."""
    if report.seconds < 0.5:
        return "Sound: none recorded."
    if report.loud_fraction < 0.05:
        return "Sound: almost silent."
    if not report.utterances:
        return f"Sound: audible for about {int(report.loud_fraction * 100)}% of the clip, but no recognisable speech (it may be music, game or background noise)."
    lines = [f"[{clock(u.start)}–{clock(u.end)}] {u.text}" for u in report.utterances]
    return ("Sound (automatic speech transcript; speech recognisers can mishear, and music or game audio may appear as odd words):\n" + "\n".join(lines))
