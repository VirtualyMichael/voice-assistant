"""Short UI sounds, synthesised so no audio files are needed."""
from __future__ import annotations

import numpy as np

SAMPLE_RATE = 24000


def _blip(freq: float, dur: float, sr: int, volume: float) -> np.ndarray:
    t = np.arange(int(sr * dur)) / sr
    env = np.minimum(1.0, np.minimum(t / 0.01, (dur - t) / 0.04))  # fast attack, soft release: no clicks
    return (volume * env * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def listening_chime(sr: int = SAMPLE_RATE, volume: float = 0.25) -> np.ndarray:
    """Two rising notes: 'go ahead, I'm listening'. About 0.24 s."""
    gap = np.zeros(int(sr * 0.02), np.float32)
    return np.concatenate([_blip(660, 0.1, sr, volume), gap, _blip(990, 0.12, sr, volume)])


def duration(samples: np.ndarray, sr: int = SAMPLE_RATE) -> float:
    return len(samples) / sr
