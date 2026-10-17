"""Per-turn latency measurement (all timestamps from time.monotonic)."""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field


@dataclass
class TurnMetrics:
    mode: str  # "local" | "cloud"
    t_speech_end: float  # endpoint detected (includes the recognizer's trailing-silence rule)
    t_first_text: float | None = None
    t_first_audio: float | None = None
    t_done: float | None = None

    @property
    def first_text_ms(self) -> float | None:
        return None if self.t_first_text is None else (self.t_first_text - self.t_speech_end) * 1000

    @property
    def first_audio_ms(self) -> float | None:
        return None if self.t_first_audio is None else (self.t_first_audio - self.t_speech_end) * 1000

    def describe(self) -> str:
        def f(v):
            return "n/a" if v is None else f"{v:.0f} ms"

        return f"[{self.mode}] first text {f(self.first_text_ms)} | first audio {f(self.first_audio_ms)}"


@dataclass
class MetricsLog:
    turns: list[TurnMetrics] = field(default_factory=list)

    def record(self, m: TurnMetrics) -> None:
        self.turns.append(m)

    def summary(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for mode in {t.mode for t in self.turns}:
            row = {}
            for key in ("first_text_ms", "first_audio_ms"):
                vals = [v for t in self.turns if t.mode == mode and (v := getattr(t, key)) is not None]
                if vals:
                    row[key + "_median"] = statistics.median(vals)
                    row[key + "_max"] = max(vals)
            out[mode] = row
        return out
