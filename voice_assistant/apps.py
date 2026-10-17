"""Finds the apps installed on this PC (Start menu entries) so the assistant can open them by name."""
from __future__ import annotations

import difflib
import json
import re
import subprocess
import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class AppMatch:
    name: str
    app_id: str  # a Start-menu AppID; launched through shell:AppsFolder, never typed by the model
    score: float


def parse_start_apps(raw: str) -> dict[str, str]:
    """Output of `Get-StartApps | ConvertTo-Json` -> {display name: AppID}. PowerShell prints one object unwrapped."""
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    return {d["Name"].strip(): d["AppID"] for d in data if d.get("Name") and d.get("AppID")}


def _scan_start_menu() -> dict[str, str]:
    cmd = "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"
    out = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace",
    ).stdout
    return parse_start_apps(out)


# Words people say around an app name that are not part of it. Speech recognition also adds punctuation, which _norm drops.
_STOP = {"the", "a", "an", "app", "application", "program", "software", "browser", "player", "for", "windows", "my",
         "open", "launch", "start", "run", "please"}
_JUNK = re.compile(r"\b(uninstall|readme|release notes|documentation|license|help|support center|bug report|updater?)\b", re.I)
_TRIM = " \t\n.,!?;:\"'`"


def clean_query(text: str) -> str:
    """Strip the punctuation and quotes that transcripts and AI tool calls tend to add around a name."""
    return text.strip(_TRIM)


def _norm(text: str) -> str:
    return " ".join(w for w in re.sub(r"[^a-z0-9]+", " ", text.lower()).split() if w not in _STOP)


def _token_sim(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if min(len(a), len(b)) >= 3 and (b.startswith(a) or a.startswith(b)):
        return 0.9
    r = difflib.SequenceMatcher(None, a, b).ratio()
    return r if r >= 0.8 else 0.0  # tolerates mis-hearings such as "calculater"


def score(query: str, name: str) -> float:
    """How well a spoken/typed `query` names the installed app `name` (0 = no match, 101 = written exactly)."""
    query = clean_query(query)
    if query.lower() == name.strip().lower():
        return 101.0  # exactly as written beats look-alikes such as Notepad vs Notepad++
    q, n = _norm(query), _norm(name)
    if not q or not n:
        return 0.0
    # names with symbols (Notepad++) lose very narrow ties; uninstallers, help files and the like never beat the app itself
    penalty = (0.5 if re.search(r"[^A-Za-z0-9 \-']", name) else 0.0) + (30.0 if _JUNK.search(name) and not _JUNK.search(query) else 0.0)
    if q == n:
        return 100.0 - penalty
    if q.replace(" ", "") == n.replace(" ", ""):
        return 98.0 - penalty  # "steam vr" -> SteamVR
    qt, nt = q.split(), n.split()
    # every spoken word is matched to the closest word of the app name
    sims = [max(_token_sim(w, x) for x in nt) for w in qt]
    q_cov = sum(sims) / len(qt)
    if q_cov < 0.5:
        ratio = difflib.SequenceMatcher(None, q, n).ratio()
        return max(ratio * 75 - penalty, 0.0) if ratio >= 0.75 else 0.0
    n_cov = sum(max(_token_sim(x, w) for w in qt) for x in nt) / len(nt)  # how much of the app's name was said
    return max(100.0 * (0.65 * q_cov + 0.35 * n_cov) - penalty, 0.0)


class AppCatalog:
    """Cached list of installed apps. Scanning takes a second or two, so it runs in the background."""

    def __init__(self, scanner=_scan_start_menu):
        self._scanner, self._apps = scanner, {}
        self._lock = threading.Lock()
        self.scanned = threading.Event()

    def refresh(self) -> int:
        try:
            apps = self._scanner()
        except Exception:
            apps = {}
        with self._lock:
            if apps or not self._apps:
                self._apps = apps
        self.scanned.set()
        return len(self._apps)

    def refresh_async(self) -> None:
        threading.Thread(target=self.refresh, daemon=True).start()

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._apps, key=str.lower)

    def __len__(self) -> int:
        return len(self._apps)

    def find(self, query: str, limit: int = 5) -> list[AppMatch]:
        """Best matches first (score 0 entries dropped)."""
        if not self.scanned.is_set():
            self.scanned.wait(8)  # first request right after start-up: let the scan finish
        with self._lock:
            ranked = [AppMatch(n, i, score(query, n)) for n, i in self._apps.items()]
        return sorted((m for m in ranked if m.score > 0), key=lambda m: -m.score)[:limit]

    def resolve(self, query: str) -> tuple[AppMatch | None, list[AppMatch]]:
        """(unique confident match, otherwise the candidates to ask the user about)."""
        found = self.find(query)
        if not found:
            return None, []
        best = found[0]
        runner_up = found[1].score if len(found) > 1 else 0
        gap = best.score - runner_up
        if (best.score >= 100 and gap >= 1) or (best.score >= 95 and gap >= 0.4) or (best.score >= 80 and gap >= 8):
            return best, found
        return None, found

    def suggest(self, query: str, limit: int = 3) -> list[str]:
        """Loose 'did you mean' names for a query that matched nothing."""
        q = _norm(clean_query(query))
        with self._lock:
            names = list(self._apps)
        ranked = sorted(((difflib.SequenceMatcher(None, q, _norm(n)).ratio(), n) for n in names if q), reverse=True)
        return [n for r, n in ranked[:limit] if r >= 0.6 and not _JUNK.search(n)]


def launch_start_app(app_id: str) -> None:
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])
