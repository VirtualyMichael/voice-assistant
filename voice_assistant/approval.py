"""Spoken yes/no for confirmation prompts (optional: the on-screen dialog still works and the first answer wins)."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

YES = {"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "confirmed", "approve", "approved", "proceed", "go", "affirmative", "do", "please"}
NO = {"no", "nope", "nah", "cancel", "stop", "deny", "denied", "decline", "don't", "dont", "negative", "never", "abort", "not"}


def parse_yes_no(text: str) -> bool | None:
    """True/False for a clear answer, None when unclear. A "no" word anywhere wins ("yes... no wait"), so risky actions err on declining."""
    words = re.findall(r"[a-z']+", text.lower())
    if not words or len(words) > 8:  # a long sentence is not an answer
        return None
    if any(w in NO for w in words):
        return False
    if any(w in YES for w in words):
        return True
    return None


def speakable(prompt: str, limit: int = 220) -> str:
    """The prompt as one short spoken sentence group: lines joined, trimmed."""
    flat = ". ".join(p.strip() for p in prompt.splitlines() if p.strip())
    return (flat[:limit].rsplit(" ", 1)[0] + " ..." if len(flat) > limit else flat) + " Say yes or no."


@dataclass
class Approval:
    done: threading.Event = field(default_factory=threading.Event)
    result: bool | None = None
    listening: bool = False
