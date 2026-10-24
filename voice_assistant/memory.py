"""Saves the conversation so the next session continues where this one stopped."""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import config_dir


def default_path() -> Path:
    return config_dir() / "conversation.json"


class ConversationStore:
    """Keeps only what is needed to continue talking: your messages and the assistant's final replies.

    Tool calls and their raw results (file contents, web pages, clipboard text) are not written to disk.
    """

    def __init__(self, path: Path | None = None, max_messages: int = 40):
        self.path = path or default_path()
        self.max_messages = max_messages

    @staticmethod
    def _keep(m: dict) -> bool:
        return m.get("role") in ("user", "assistant") and bool(m.get("content")) and not m.get("tool_calls")

    def load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            msgs = [{"role": m["role"], "content": m["content"]} for m in data["messages"] if self._keep(m)]
        except (OSError, ValueError, KeyError, TypeError):
            return []  # missing or damaged file: start fresh rather than fail
        return msgs[-self.max_messages :]

    def save(self, messages: list[dict]) -> None:
        keep = [{"role": m["role"], "content": m["content"], "ts": int(time.time())} for m in messages if self._keep(m)]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "messages": keep[-self.max_messages :]}, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic: a crash mid-write cannot corrupt the saved conversation

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


# ---- many chats: the history sidebar ------------------------------------------------------------
_CHAT_ID = re.compile(r"^[0-9a-f]{12}$")
TITLE_CHARS = 48


@dataclass
class ChatInfo:
    id: str
    title: str
    updated: float
    count: int


def _title_from(messages: list[dict]) -> str:
    first = next((m["content"] for m in messages if m.get("role") == "user" and str(m.get("content", "")).strip()), "New chat")
    one_line = " ".join(str(first).split())
    return one_line if len(one_line) <= TITLE_CHARS else one_line[: TITLE_CHARS - 1].rstrip() + "…"


def merge_messages(stored: list[dict], current: list[dict]) -> list[dict]:
    """`current` is the tail the model still holds (older messages are trimmed away) plus new ones: append only what the
    saved chat does not have yet, matching on the longest overlap between the end of `stored` and the start of `current`."""
    same = lambda a, b: a.get("role") == b.get("role") and a.get("content") == b.get("content")  # noqa: E731
    for k in range(min(len(stored), len(current)), 0, -1):
        if all(same(stored[len(stored) - k + i], current[i]) for i in range(k)):
            return stored + current[k:]
    return stored + current


class ChatLibrary:
    """Every chat is its own small file in one folder, so one can be opened, renamed or deleted without touching the rest.

    Like ConversationStore it keeps only your messages and the assistant's final replies, never tool output.
    """

    def __init__(self, directory: Path | None = None, max_messages: int = 400, legacy: Path | None = None):
        self.dir = directory or (config_dir() / "chats")
        self.max_messages = max_messages
        self.legacy = legacy

    def _path(self, chat_id: str) -> Path:
        if not _CHAT_ID.match(chat_id or ""):
            raise ValueError("bad chat id")
        return self.dir / f"{chat_id}.json"

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]

    def _read(self, chat_id: str) -> dict | None:
        try:
            data = json.loads(self._path(chat_id).read_text(encoding="utf-8"))
            if not isinstance(data.get("messages"), list):
                return None
            data["messages"] = [{"role": m["role"], "content": m["content"]} for m in data["messages"] if ConversationStore._keep(m)]
            return data
        except (OSError, ValueError, KeyError, TypeError):
            return None  # missing or damaged file: ignore it rather than fail

    def load(self, chat_id: str) -> list[dict]:
        data = self._read(chat_id)
        return data["messages"] if data else []

    def list(self, query: str = "") -> list[ChatInfo]:
        out: list[ChatInfo] = []
        needle = query.strip().lower()
        for p in self.dir.glob("*.json") if self.dir.is_dir() else []:
            data = self._read(p.stem) if _CHAT_ID.match(p.stem) else None
            if not data or not data["messages"]:
                continue
            if needle and needle not in str(data.get("title", "")).lower() and not any(needle in str(m["content"]).lower() for m in data["messages"]):
                continue
            out.append(ChatInfo(p.stem, str(data.get("title") or _title_from(data["messages"])), float(data.get("updated", p.stat().st_mtime)), len(data["messages"])))
        return sorted(out, key=lambda c: -c.updated)

    def save(self, chat_id: str, messages: list[dict]) -> bool:
        """Add the new messages to this chat (creating it on the first message). Returns False when there was nothing to keep."""
        keep = [{"role": m["role"], "content": m["content"]} for m in messages if ConversationStore._keep(m)]
        if not keep:
            return False
        old = self._read(chat_id) or {}
        merged = merge_messages(old.get("messages", []), keep)[-self.max_messages:]
        title = old.get("title") if old.get("custom_title") else _title_from(merged)
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(chat_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 2, "id": chat_id, "title": title, "custom_title": bool(old.get("custom_title")),
                                   "created": old.get("created", time.time()), "updated": time.time(), "messages": merged}, indent=1), encoding="utf-8")
        os.replace(tmp, path)  # atomic: a crash mid-write cannot corrupt a saved chat
        return True

    def rename(self, chat_id: str, title: str) -> None:
        data = self._read(chat_id)
        title = " ".join(title.split())[:80]
        if data and title:
            data.update(title=title, custom_title=True)
            tmp = self._path(chat_id).with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
            os.replace(tmp, self._path(chat_id))

    def delete(self, chat_id: str) -> None:
        try:
            self._path(chat_id).unlink()
        except FileNotFoundError:
            pass

    def delete_all(self) -> int:
        n = 0
        for p in self.dir.glob("*.json") if self.dir.is_dir() else []:
            if _CHAT_ID.match(p.stem):
                p.unlink(missing_ok=True)
                n += 1
        return n

    def migrate_legacy(self) -> str | None:
        """Turn the old single conversation.json into the first chat (once), then remove the old file."""
        if not self.legacy or not self.legacy.exists() or self.list():
            return None
        msgs = ConversationStore(self.legacy, 10_000).load()
        if not msgs:
            return None
        cid = self.new_id()
        if self.save(cid, msgs):
            self.legacy.unlink(missing_ok=True)
            return cid
        return None
