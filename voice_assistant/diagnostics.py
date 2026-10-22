"""'Why didn't that work?': a short memory of what each tool call returned, so the assistant can explain the last failure in plain words."""
from __future__ import annotations

import time
from collections import deque

from .tools import Tool

FAIL_MARKERS = ("error", "could not", "couldn't", "not connected", "did not", "didn't", "no such", "failed", "unavailable", "not found", "no matching", "cannot")
KEEP = 40


def looks_failed(result: str) -> bool:
    head = result.strip().lower()[:200]
    return head.startswith("error") or any(m in head for m in FAIL_MARKERS) and "declined" not in head


class CallLog:
    """Attach to a ToolRegistry: `registry.log = CallLog()`; the registry records every call here."""

    def __init__(self, clock=time.time):
        self.items: deque = deque(maxlen=KEEP)
        self.clock = clock

    def record(self, name: str, arguments: str, result: str) -> None:
        self.items.append({"at": self.clock(), "tool": name, "args": (arguments or "")[:160], "result": result[:400], "failed": looks_failed(result)})

    def recent(self, only_failed: bool = True, limit: int = 5) -> list[dict]:
        rows = [i for i in self.items if i["failed"] or not only_failed]
        return rows[-limit:]


def build_diagnostic_tools(log: CallLog, warnings=lambda: [], clock=time.time) -> list[Tool]:
    from .agent_tools import _obj

    def what_went_wrong() -> str:
        rows = log.recent(only_failed=True, limit=4)
        notes = [w for w in warnings()]
        if not rows and not notes:
            return "Nothing has failed recently: no tool call returned an error. Ask what the user expected to happen and check that tool's result."
        lines = [f"{max(0, int(clock() - r['at']))} s ago, {r['tool']}({r['args']}) answered: {r['result']}" for r in rows]
        lines += [f"At startup: {n}" for n in notes[:4]]
        return "Recent problems (newest last):\n" + "\n".join(lines) + "\nExplain the most recent one to the user in plain words, say what they can do about it, and do not claim the task worked."

    return [Tool("what_went_wrong", "Look up what recently failed (the last tool errors and any startup problems). Call it when the user asks why something did not work, or says 'that didn't work' / 'what happened?'.", _obj({}), what_went_wrong)]
