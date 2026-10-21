"""Your YouTube Music library on this PC: every playlist and every track in it (title, artist), saved by the browser extension on request.

What it is for: the exact spelling of the things you listen to. A spoken "Continuum by SLVM Lord" becomes "Continuum by SLVMLORD" before the search, so the
right song is found and a different artist's song with the same title is never mistaken for it. It lives in ytm-library.json in the app's settings folder."""
from __future__ import annotations

import json
import time
from pathlib import Path

from .config import config_dir
from .ytm_browser import YtmError, kind_of, rank, split_request, tokens

STALE_S = 24 * 3600


class Library:
    def __init__(self, path: Path | None = None):
        self.path = path or config_dir() / "ytm-library.json"
        self.data: dict | None = None
        self._cands: list[dict] | None = None
        self.load()

    def load(self) -> None:
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = None
        self._cands = None

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.data, self._cands = data, None

    # ---- facts -------------------------------------------------------------------------
    def counts(self) -> tuple[int, int]:
        """(playlists, distinct songs)."""
        if not self.data:
            return 0, 0
        return len(self.data.get("playlists", [])), len(self.candidates())

    def updated(self) -> float:
        return float((self.data or {}).get("fetchedAt", 0)) / 1000.0

    def stale(self, now: float | None = None) -> bool:
        return self.data is None or (now or time.time()) - self.updated() > STALE_S

    def summary(self) -> str:
        p, t = self.counts()
        if not p:
            return "No library saved yet."
        age = int((time.time() - self.updated()) / 3600)
        return f"{t:,} songs in {p} playlists saved ({'updated just now' if age < 1 else f'updated {age} h ago'})."

    # ---- matching ----------------------------------------------------------------------
    def candidates(self) -> list[dict]:
        if self._cands is None:
            seen: set[tuple[str, str]] = set()
            out = []
            for pl in (self.data or {}).get("playlists", []):
                for t in pl.get("tracks", []):
                    key = ("".join(tokens(t.get("title", ""))), "".join(tokens(t.get("artist", ""))))
                    if not key[0] or key in seen:
                        continue
                    seen.add(key)
                    out.append({"kind": "lib", "i": len(out), "title": t["title"], "sub": f"Song • {t.get('artist', '')}", "artist": t.get("artist", ""), "playlist": pl.get("title", "")})
            self._cands = out
        return self._cands

    def resolve(self, query: str) -> str | None:
        """The request in the library's own spelling ('Title by Artist') when it clearly names a song you have saved, else None."""
        cands = self.candidates()
        if not cands:
            return None
        _, artist_q = split_request(query)
        ranked = rank(query, cands)
        if not ranked:
            return None
        score, best = ranked[0]
        if best["_title_hit"] < 0.85 or (artist_q and best["_artist_hit"] < 0.75):
            return None
        if len(ranked) > 1:
            second = ranked[1][1]
            tied = ranked[1][0] >= score - 0.15 and (second["title"], second.get("artist")) != (best["title"], best.get("artist"))
            if tied and not artist_q:  # two different songs with this title and no artist given: let the search page decide
                return None
        return f"{best['title']} by {kind_of(best['sub'])[1]}" if best.get("artist") else best["title"]

    def playlist_titles(self) -> list[str]:
        return [p.get("title", "") for p in (self.data or {}).get("playlists", [])]

    # ---- refreshing ---------------------------------------------------------------------
    def refresh(self, bridge, timeout: float = 300.0) -> str:
        """Ask the browser extension for the whole library (it reads it from your signed-in YouTube Music page) and save it."""
        res = bridge.request("library", timeout=timeout)
        data = res.get("library") or {}
        if not data.get("playlists"):
            raise YtmError("YouTube Music returned no playlists. Make sure you are signed in there.")
        failed = [p["title"] for p in data["playlists"] if p.get("error")]
        self.save(data)
        return self.summary() + (f" {len(failed)} playlist(s) could not be read." if failed else "")
