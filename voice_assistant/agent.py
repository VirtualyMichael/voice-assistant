"""Conversation state plus the model/tool loop. Yields text deltas as they stream."""
from __future__ import annotations

import threading
from typing import Callable, Iterator

from .llm import LLM
from .tools import ToolRegistry


class Agent:
    def __init__(
        self,
        llm: LLM,
        system_prompt: str,
        tools: ToolRegistry | None = None,
        max_rounds: int = 4,
        max_messages: int = 30,
        on_tool: Callable[[str], None] | None = None,
    ):
        self.llm, self.tools, self.max_rounds, self.max_messages = llm, tools, max_rounds, max_messages
        self.on_tool = on_tool or (lambda _msg: None)
        self.messages: list[dict] = [{"role": "system", "content": system_prompt}]

    def respond(self, user_text: str, cancel: threading.Event) -> Iterator[str]:
        self.messages.append({"role": "user", "content": user_text})
        self._trim()
        pending: list[str] = []  # streamed text of an unfinished round, kept if interrupted
        try:
            for _ in range(self.max_rounds + 1):
                calls = ()
                pending.clear()
                for ev in self.llm.stream_chat(self.messages, self.tools.schemas() if self.tools else None, cancel):
                    if cancel.is_set():
                        return
                    if ev.kind == "text":
                        pending.append(ev.text)
                        yield ev.text
                    else:
                        calls = ev.calls
                if not calls or not self.tools:
                    self.messages.append({"role": "assistant", "content": "".join(pending)})
                    pending.clear()
                    return
                self.messages.append(
                    {
                        "role": "assistant",
                        "content": "".join(pending) or None,
                        "tool_calls": [
                            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                            for c in calls
                        ],
                    }
                )
                pending.clear()
                for c in calls:
                    if cancel.is_set():
                        self.messages.append({"role": "tool", "tool_call_id": c.id, "content": "Cancelled."})
                        continue
                    self.on_tool(f"{c.name} {c.arguments}")
                    self.messages.append({"role": "tool", "tool_call_id": c.id, "content": self.tools.call(c.name, c.arguments)})
                if cancel.is_set():
                    return
            yield "I stopped because the task needed too many steps."
        finally:
            if pending:
                self.messages.append({"role": "assistant", "content": "".join(pending) + " [interrupted]"})

    def _trim(self) -> None:
        """Drop the oldest whole exchanges, never splitting a tool call from its results."""
        while len(self.messages) > self.max_messages:
            nxt = next((i for i in range(2, len(self.messages)) if self.messages[i]["role"] == "user"), None)
            if nxt is None:
                break
            del self.messages[1:nxt]
