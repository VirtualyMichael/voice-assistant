"""Files shared to the assistant from any app on the phone (Android's share sheet): pictures, PDFs, videos and text files.

The phone sends each file in pieces (share_file messages, each well under the link's message limit); they are written straight to a folder
on this PC and the finished file is attached to the chat like a file dropped on the window. The phone then sends the user's question as
ordinary text. Only a paired phone can send, and only these kinds of file are kept.
"""
from __future__ import annotations

import base64
import re
import threading
import time
from pathlib import Path
from typing import Callable

MAX_SHARE_BYTES = 300_000_000  # a few minutes of phone video; pictures and documents are far smaller
MAX_CHUNK_BYTES = 1_000_000  # one piece, before base64
MAX_OPEN_SHARES = 8
ALLOWED = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp",
    ".mp4", ".mov", ".m4v", ".mkv", ".webm",
    ".pdf", ".txt", ".md", ".csv", ".json", ".html", ".htm",
}
_MIME_EXT = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif", "video/mp4": ".mp4",
    "video/quicktime": ".mov", "video/webm": ".webm", "application/pdf": ".pdf", "text/plain": ".txt", "text/csv": ".csv",
    "text/html": ".html", "application/json": ".json", "text/markdown": ".md",
}


def safe_name(name: str, mime: str = "") -> str:
    """A file name that cannot leave the share folder, with an extension that says what it is."""
    base = re.sub(r"[^\w.\- ()]", "_", Path(str(name or "")).name).strip(" .")[:80] or "shared"
    if Path(base).suffix.lower() not in ALLOWED and mime in _MIME_EXT:
        base += _MIME_EXT[mime]
    return base


def _discard(path: Path) -> None:
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()  # its own share folder, now empty
    except OSError:
        pass


class ShareReceiver:
    """Puts the pieces of shared files back together on disk. attach(path) -> (name, error) hands a finished file to the chat."""

    def __init__(self, folder: Path, attach: Callable[[Path], tuple[str, str]], clock=time.monotonic):
        self.folder, self.attach, self.clock = Path(folder), attach, clock
        self._open: dict[str, dict] = {}
        self._lock = threading.Lock()

    def piece(self, msg: dict) -> dict | None:
        """One share_file message. Returns the reply for the phone once the file is finished or refused, else None."""
        sid = str(msg.get("id", ""))[:64]
        if not re.fullmatch(r"[\w-]{1,64}", sid):
            return {"type": "share_saved", "id": sid, "ok": False, "message": "That share was not understood."}
        try:
            seq, total, last = int(msg.get("seq", -1)), int(msg.get("total", -1)), bool(msg.get("last"))
            data = base64.b64decode(str(msg.get("data_b64", "")), validate=True)
        except (TypeError, ValueError):
            return self._fail(sid, "That share was not understood.")
        with self._lock:
            entry = self._open.get(sid)
            if entry is None:
                name = safe_name(msg.get("name", ""), str(msg.get("mime", "")))
                if seq != 0:
                    return self._fail(sid, "Part of the file went missing. Share it again.")
                if Path(name).suffix.lower() not in ALLOWED:
                    return self._fail(sid, f"The assistant cannot use {Path(name).suffix or 'that kind of'} files.")
                if not 0 < total <= MAX_SHARE_BYTES:
                    return self._fail(sid, f"That file is too large (the limit is {MAX_SHARE_BYTES // 1_000_000} MB).")
                self._drop_stale()
                if len(self._open) >= MAX_OPEN_SHARES:
                    return self._fail(sid, "Too many files at once. Share fewer.")
                box = self.folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{sid[:8]}"  # a folder per share, so the file keeps its own name
                box.mkdir(parents=True, exist_ok=True)
                path = box / name
                entry = {"path": path, "file": open(path, "wb"), "next": 0, "total": total, "got": 0, "at": self.clock(), "name": name}
                self._open[sid] = entry
            if seq != entry["next"] or len(data) > MAX_CHUNK_BYTES or entry["got"] + len(data) > entry["total"]:
                return self._fail(sid, "Part of the file went missing. Share it again.")
            entry["file"].write(data)
            entry["next"] += 1
            entry["got"] += len(data)
            entry["at"] = self.clock()
            if not last:
                return None
            self._open.pop(sid)
            entry["file"].close()
        if entry["got"] != entry["total"]:
            _discard(entry["path"])
            return {"type": "share_saved", "id": sid, "ok": False, "message": "Part of the file went missing. Share it again."}
        name, err = self.attach(entry["path"])
        if not name:
            _discard(entry["path"])
        return {"type": "share_saved", "id": sid, "ok": bool(name), "name": name or entry["name"], "message": err or "Attached."}

    def _fail(self, sid: str, message: str) -> dict:
        entry = self._open.pop(sid, None)
        if entry is not None:
            entry["file"].close()
            _discard(entry["path"])
        return {"type": "share_saved", "id": sid, "ok": False, "message": message}

    def _drop_stale(self, after_s: float = 300) -> None:
        for sid in [s for s, e in self._open.items() if self.clock() - e["at"] > after_s]:
            self._fail(sid, "")

    def close(self) -> None:
        with self._lock:
            for sid in list(self._open):
                self._fail(sid, "")
