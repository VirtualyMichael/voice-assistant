"""Turns a token stream into speakable chunks."""
from __future__ import annotations

import re

_BOUNDARY = re.compile(r"[.!?]+[\"')\]]*\s+|\n+")
_SOFT = re.compile(r"[,;:]\s+")


class SentenceChunker:
    """Emits sentence-sized chunks; the first chunk may break at a comma to cut latency."""

    def __init__(self, min_chars: int = 20, first_soft_limit: int = 50, max_chars: int = 220):
        self.min_chars = min_chars
        self.max_chars = max_chars  # TTS models truncate very long inputs, so never emit more than this
        self.first_soft_limit = first_soft_limit
        self._buf = ""
        self._emitted = 0

    def feed(self, delta: str) -> list[str]:
        self._buf += delta
        out: list[str] = []
        while (cut := self._find_cut()) is not None:
            chunk, self._buf = self._buf[:cut].strip(), self._buf[cut:]
            if chunk:
                out.append(chunk)
                self._emitted += 1
        return out

    def flush(self) -> str | None:
        tail, self._buf = self._buf.strip(), ""
        return tail or None

    def _find_cut(self) -> int | None:
        for m in _BOUNDARY.finditer(self._buf):
            if m.end() >= self.min_chars:
                return m.end()
        if self._emitted == 0 and len(self._buf) >= self.first_soft_limit:
            for m in _SOFT.finditer(self._buf):
                if m.end() >= self.min_chars:
                    return m.end()
        if len(self._buf) >= self.max_chars:  # no sentence end in sight: break at the last comma, else the last space
            window = self._buf[: self.max_chars]
            for sep in (", ", "; ", " "):
                k = window.rfind(sep)
                if k >= self.min_chars:
                    return k + len(sep)
            return self.max_chars
        return None


_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_CODE_FENCE = re.compile(r"```.*?```", re.S)


def clean_for_speech(text: str, pronunciations: dict[str, str] | None = None) -> str:
    text = _CODE_FENCE.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = re.sub(r"[*_`#>~|]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    from .normalize import normalize_for_speech

    return normalize_for_speech(re.sub(r"\s+", " ", text).strip(), pronunciations)
