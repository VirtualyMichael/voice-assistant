"""Saves the conversation so the next session continues where this one stopped."""
from __future__ import annotations

import json
import os
import time
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
