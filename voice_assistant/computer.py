"""Simple computer control (experimental): focus an app window, click what you describe, type, press keys, scroll.

How it finds things to click: it screenshots the window and asks the vision model you already run to pick the cell of a labelled grid that holds the
described element, then zooms in on that cell and asks again (two steps are far more accurate than one). That works well for big, clear targets and
less well for tiny ones, and a stronger vision model (Claude, GPT) is better than a small local one. The assistant is told to look again after acting.

Safety: off by default; asks before it first touches each window (then remembers for a few minutes); refuses terminals, the assistant itself, password
managers and Windows security prompts; blocks Windows-key shortcuts; caps the number of actions per approval; stops when you press Stop."""
from __future__ import annotations

import ctypes
import re
import time

from .screen import ask_about_image, capture_png, focus_window, list_windows, match_window, window_rect
from .tools import Tool

BLOCKED_PROCESSES = {"powershell", "pwsh", "cmd", "conhost", "windowsterminal", "wt", "bash", "mintty", "wsl", "python", "pythonw", "voiceassistant",
                     "keepass", "keepassxc", "bitwarden", "1password", "lastpass", "dashlane", "consent", "credentialuibroker", "securityhealthsystray",
                     "windowsdefender", "regedit", "taskmgr", "mmc"}
BLOCKED_TITLES = ("windows security", "user account control", "voice assistant", "windows powershell", "command prompt")
SENSITIVE_TITLE = re.compile(r"password|sign in|log in|login|bank|payment|checkout|credit|paypal|wallet", re.I)
APPROVAL_S = 600
MAX_ACTIONS = 40
ARROW_KEYS = {"up": 0x26, "down": 0x28}


def window_is_blocked(title: str, exe: str) -> str | None:
    if exe.lower() in BLOCKED_PROCESSES:
        return f"'{exe}' is off limits (terminals, security prompts, password managers and this assistant cannot be controlled this way)"
    if any(b in title.lower() for b in BLOCKED_TITLES):
        return f"the window '{title}' is off limits"
    return None


def keys_allowed(keys: str) -> bool:
    k = keys.lower().replace(" ", "")
    return not any(w in k for w in ("win+", "windows+", "ctrl+alt+del", "ctrl+shift+esc"))


def _png_to_qimage(png: bytes):
    from PySide6.QtGui import QImage

    return QImage.fromData(png)


def _to_png(img) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(data)


def _grid(img, cols: int, rows: int, width: int):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPen

    img = img.scaledToWidth(width, Qt.SmoothTransformation)
    cw, ch = img.width() / cols, img.height() / rows
    p = QPainter(img)
    p.setPen(QPen(QColor(255, 255, 0, 170), 1))
    p.setFont(QFont("Arial", 10, QFont.Bold))
    for c in range(cols):
        for r in range(rows):
            p.drawRect(int(c * cw), int(r * ch), int(cw), int(ch))
            p.drawText(int(c * cw) + 3, int(r * ch) + 13, f"{chr(65 + c)}{r + 1}")
    p.end()
    return img


def _ask_cell(llm, img, cols: int, rows: int, desc: str, width: int, ask=ask_about_image):
    q = (f"The image has a yellow grid with {cols} columns (letters A-{chr(64 + cols)}) and {rows} rows (numbers 1-{rows}); each cell is labelled with its own letter and "
         f"number in its top-left corner. Which single cell contains the CENTER of: {desc}? If it is not visible at all, answer NONE. Answer with only the cell label, like C4.")
    ans = ask(llm, q, _to_png(_grid(img, cols, rows, width))).upper()
    if "NONE" in ans:
        return "none"
    m = re.search(r"\b([A-Z])\s*(\d{1,2})\b", ans)
    if not m:
        return None
    c, r = ord(m[1]) - 65, int(m[2]) - 1
    return (c, r) if 0 <= c < cols and 0 <= r < rows else None


def locate(llm, png: bytes, desc: str, ask=ask_about_image):
    """(x, y) in the image's pixels of the described element, 'none' if it is not on screen, or None if the model gave no usable answer."""
    img = _png_to_qimage(png)
    W, H = img.width(), img.height()
    c1 = _ask_cell(llm, img, 8, 6, desc, 1280, ask)
    if c1 in (None, "none"):
        return c1
    cw, ch = W / 8, H / 6
    x0, y0 = max(0, (c1[0] - 0.5) * cw), max(0, (c1[1] - 0.5) * ch)  # the chosen cell plus half a cell around it
    x1, y1 = min(W, (c1[0] + 1.5) * cw), min(H, (c1[1] + 1.5) * ch)
    c2 = _ask_cell(llm, img.copy(int(x0), int(y0), int(x1 - x0), int(y1 - y0)), 6, 6, desc, 900, ask)
    if c2 in (None, "none"):
        return ((c1[0] + 0.5) * cw, (c1[1] + 0.5) * ch)
    return (x0 + (c2[0] + 0.5) * (x1 - x0) / 6, y0 + (c2[1] + 0.5) * (y1 - y0) / 6)


class Hands:
    """The real mouse and keyboard (replaced by a fake in tests)."""

    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> None:
        u = ctypes.windll.user32
        u.SetCursorPos(int(x), int(y))
        down, up = (0x0008, 0x0010) if button == "right" else (0x0002, 0x0004)
        for _ in range(2 if double else 1):
            u.mouse_event(down, 0, 0, 0, 0)
            u.mouse_event(up, 0, 0, 0, 0)
            time.sleep(0.05)

    def move(self, x: int, y: int) -> None:
        ctypes.windll.user32.SetCursorPos(int(x), int(y))

    def scroll(self, clicks: int) -> None:
        ctypes.windll.user32.mouse_event(0x0800, 0, 0, int(clicks * 120), 0)  # positive = up

    def type(self, text: str) -> None:
        import keyboard

        keyboard.write(text, delay=0.01)

    def keys(self, combo: str) -> None:
        import keyboard

        keyboard.send(combo)


def build_computer_tools(cfg, confirm, get_llm, windows=list_windows, capture=capture_png, rect=window_rect, focus=focus_window, hands=None,
                         cancelled=lambda: False, clock=time.time, ask=ask_about_image) -> list[Tool]:
    from .agent_tools import _obj, _str

    hands = hands or Hands()
    approved: dict[int, float] = {}  # hwnd -> when approval ends
    used = {"n": 0}

    def prepare(window: str, sensitive_text: str = ""):
        """Find and vet the window, get approval, bring it forward. Returns (hwnd, title, region) or an error string."""
        if cancelled():
            return "Stopped by the user."
        found = match_window(windows(), window or "", loose=True)
        if found is None:
            names = sorted({w[2] or w[1] for w in windows() if w[1]})[:20]
            return f"No open window matches '{window}'. Open windows: {', '.join(names)}. Ask the user whether to open it."
        hwnd, title, exe = found
        why = window_is_blocked(title, exe)
        if why:
            return f"Refused: {why}."
        now = clock()
        if approved.get(hwnd, 0) < now:
            if not confirm(f"Let the assistant control '{title}' with the mouse and keyboard for the next {APPROVAL_S // 60} minutes?\n\n"
                           f"It can click, type and press keys there. Press Stop at any time."):
                return "The user declined this action."
            approved[hwnd] = now + APPROVAL_S
            used["n"] = 0
        if used["n"] >= MAX_ACTIONS:
            return f"Action limit reached ({MAX_ACTIONS}) for this approval. Tell the user and stop."
        if sensitive_text and SENSITIVE_TITLE.search(title) and not confirm(f"Type this into '{title}'?\n\n{sensitive_text[:200]}"):
            return "The user declined this action."
        used["n"] += 1
        focus(hwnd)
        time.sleep(0.4)
        return hwnd, title, rect(hwnd)

    def control_click(target: str, window: str, button: str = "left", double: bool = False) -> str:
        r = prepare(window)
        if isinstance(r, str):
            return r
        hwnd, title, region = r
        png = capture(1, 4000, region=region)
        pos = locate(get_llm(), png, target, ask)
        if pos == "none":
            return f"I could not see '{target}' in '{title}'. Describe it differently, scroll, or look at the screen first."
        if pos is None:
            return "The vision model did not give a usable answer. It may not support images."
        if cancelled():
            return "Stopped by the user."
        x, y = region[0] + pos[0], region[1] + pos[1]
        hands.click(x, y, "right" if button == "right" else "left", bool(double))
        return f"Clicked '{target}' in '{title}' at ({int(x)}, {int(y)}). Look at the screen to check it worked before continuing."

    def control_type(text: str, window: str, press_enter: bool = False) -> str:
        r = prepare(window, text)
        if isinstance(r, str):
            return r
        hands.type(text)
        if press_enter:
            hands.keys("enter")
        return f"Typed {len(text)} characters{' and pressed Enter' if press_enter else ''} in '{r[1]}'."

    def control_keys(keys: str, window: str) -> str:
        if not keys_allowed(keys):
            return "Refused: Windows-key and system shortcuts are not allowed."
        r = prepare(window)
        if isinstance(r, str):
            return r
        hands.keys(keys)
        return f"Pressed {keys} in '{r[1]}'."

    def control_scroll(direction: str, window: str, amount: int = 3) -> str:
        r = prepare(window)
        if isinstance(r, str):
            return r
        hwnd, title, region = r
        hands.move(region[0] + region[2] // 2, region[1] + region[3] // 2)
        hands.scroll(int(amount) if direction.lower().startswith("up") else -int(amount))
        return f"Scrolled {direction} in '{title}'."

    def control_focus(window: str) -> str:
        r = prepare(window)
        return r if isinstance(r, str) else f"'{r[1]}' is in front."

    return [
        Tool("control_click", "Click something in an app window by describing it ('the Search box', 'the Play button in the bottom bar'). window = the app or game name. "
             "It uses the vision model to find the spot, so describe where it is. After acting, call look_at_screen to check before the next step. Experimental.",
             _obj({"target": _str, "window": _str, "button": _str, "double": {"type": "boolean"}}, ["target", "window"]), control_click),
        Tool("control_type", "Type text into the focused field of an app window (click the field first). Never type passwords or payment details.",
             _obj({"text": _str, "window": _str, "press_enter": {"type": "boolean"}}, ["text", "window"]), control_type),
        Tool("control_keys", "Press a key or shortcut in an app window, such as 'enter', 'tab', 'ctrl+f', 'space', 'esc'.", _obj({"keys": _str, "window": _str}, ["keys", "window"]), control_keys),
        Tool("control_scroll", "Scroll the middle of an app window up or down.", _obj({"direction": _str, "window": _str, "amount": {"type": "integer"}}, ["direction", "window"]), control_scroll),
        Tool("control_focus", "Bring an app window to the front.", _obj({"window": _str}, ["window"]), control_focus),
    ]
