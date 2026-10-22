"""Interpreter mode: every sentence the user says is answered with only its translation into another language (and, if asked, typed into the VRChat chatbox)."""
from __future__ import annotations

import threading

from .tools import Tool


class Interpreter:
    def __init__(self, say=None, on_change=lambda: None, chatbox_ok=lambda: True):
        self.lang = ""
        self.chatbox = False
        self._pending: tuple[str, bool] | None = None  # starts after the turn that asked for it, so the confirmation is not translated or typed
        self._buf: list[str] = []
        self._say, self._on_change, self._chatbox_ok = say, on_change, chatbox_ok
        self._lock = threading.Lock()

    @property
    def active(self) -> bool:
        return bool(self.lang)

    def prompt(self) -> str:
        if not self.active:
            return ""
        return (f"\n\nINTERPRETER MODE is on. Whatever the user says next, answer with ONLY its translation into {self.lang}: no commentary, no quotes, no tools, no explanations. "
                "If the user clearly asks to stop interpreting, call stop_interpreter instead.")

    def start(self, language: str, chatbox: bool = False) -> str:
        language = " ".join(str(language).split())[:40]
        if not language:
            return "Error: say which language to translate into."
        if chatbox and (self._say is None or not self._chatbox_ok()):
            return "Error: the VRChat chatbox is not switched on (Settings > VR)."
        with self._lock:
            self._pending = (language, bool(chatbox))
        return f"Interpreter mode will start after this reply: everything the user says will be translated into {language}" + (" and typed into the VRChat chatbox" if chatbox else "") + ". Tell them to say 'stop interpreting' to end it."

    def stop(self) -> str:
        with self._lock:
            was = self.active or self._pending is not None
            self.lang, self.chatbox, self._pending, self._buf = "", False, None, []
        self._on_change()
        return "Interpreter mode is off." if was else "Interpreter mode was not on."

    def feed(self, delta: str) -> None:
        if self.active:
            self._buf.append(delta)

    def finish(self) -> str:
        """Call when a reply ends. Starts a pending interpreter, or sends the finished translation to the chatbox. Returns the translation that was sent ('' if none)."""
        with self._lock:
            if self._pending is not None:
                self.lang, self.chatbox = self._pending
                self._pending, self._buf = None, []
                changed, text = True, ""
            else:
                changed, text = False, "".join(self._buf).strip()
                self._buf = []
        if changed:
            self._on_change()
            return ""
        if text and self.active and self.chatbox and self._say is not None and self._chatbox_ok():
            self._say(text)
            return text
        return ""


def build_interpreter_tools(it: Interpreter) -> list[Tool]:
    from .agent_tools import _obj, _str

    return [
        Tool("start_interpreter", "Start interpreter mode: from the next sentence on, translate everything the user says into a language and reply with only the translation. chatbox true also types each translation in the VRChat chatbox.",
             _obj({"language": _str, "chatbox": {"type": "boolean"}}, ["language"]), lambda language, chatbox=False: it.start(language, bool(chatbox))),
        Tool("stop_interpreter", "End interpreter mode.", _obj({}), it.stop),
    ]
