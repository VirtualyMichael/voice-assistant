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


_STOP = {"the", "app", "application", "for", "windows", "my", "open", "launch", "start"}


def _norm(text: str) -> str:
    return " ".join(w for w in re.sub(r"[^a-z0-9]+", " ", text.lower()).split() if w not in _STOP)


def score(query: str, name: str) -> float:
    if query.strip().lower() == name.strip().lower():
        return 101.0  # exactly as written beats look-alikes such as Notepad vs Notepad++
    q, n = _norm(query), _norm(name)
    if not q or not n:
        return 0.0
    if q == n:
        return 100.0
    if q.replace(" ", "") == n.replace(" ", ""):
        return 98.0  # "steam vr" -> SteamVR
    if n.startswith(q):  # not the reverse: "vrchat companion" must not match plain VRChat
        return 90.0 - min(abs(len(n) - len(q)), 20) * 0.2
    qt, nt = set(q.split()), set(n.split())
    if qt <= nt:
        return 85.0 - min(len(nt - qt), 5)
    ratio = difflib.SequenceMatcher(None, q, n).ratio()
    return ratio * 75 if ratio >= 0.75 else 0.0


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
        if (best.score >= 100 and best.score - runner_up >= 1) or (best.score >= 80 and best.score - runner_up >= 8):
            return best, found
        return None, found


def launch_start_app(app_id: str) -> None:
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"])
