from voice_assistant.computer import build_computer_tools, keys_allowed, locate, window_is_blocked
from voice_assistant.config import Config

WINS = [(1, "YouTube Music", "chrome"), (2, "Windows PowerShell", "powershell"), (3, "Bitwarden", "Bitwarden"), (4, "Sign in - Bank - Edge", "msedge"), (5, "Voice Assistant", "python")]


class Hands:
    def __init__(self):
        self.log = []

    def click(self, x, y, button="left", double=False):
        self.log.append(("click", int(x), int(y), button, double))

    def move(self, x, y):
        self.log.append(("move", x, y))

    def scroll(self, n):
        self.log.append(("scroll", n))

    def type(self, text):
        self.log.append(("type", text))

    def keys(self, combo):
        self.log.append(("keys", combo))


def make(confirm=lambda p: True, cancelled=lambda: False, clock=lambda: 1000.0, hands=None):
    hands = hands or Hands()
    tools = build_computer_tools(Config(), confirm, lambda: None, windows=lambda: WINS, capture=lambda *a, **k: b"png", rect=lambda h: (100, 50, 800, 600),
                                 focus=lambda h: None, hands=hands, cancelled=cancelled, clock=clock, ask=lambda *a: "")
    return {t.name: t.handler for t in tools}, hands


def test_blocked_windows_and_keys():
    assert window_is_blocked("Windows PowerShell", "powershell") and window_is_blocked("x", "Bitwarden") and window_is_blocked("Voice Assistant", "anything")
    assert window_is_blocked("YouTube Music", "chrome") is None
    assert keys_allowed("ctrl+f") and not keys_allowed("win+r") and not keys_allowed("ctrl+shift+esc")


def test_terminals_password_managers_and_self_are_refused_without_even_asking():
    asked = []
    t, hands = make(lambda p: asked.append(p) or True)
    for w in ("powershell", "bitwarden", "voice assistant"):
        assert "Refused" in t["control_keys"]("enter", w)
    assert not asked and not hands.log


def test_asks_once_per_window_then_remembers_and_declines_do_nothing():
    asked = []
    t, hands = make(lambda p: asked.append(p) or True)
    t["control_keys"]("space", "youtube music")
    t["control_keys"]("space", "youtube music")
    assert len(asked) == 1 and hands.log == [("keys", "space"), ("keys", "space")]
    t2, h2 = make(lambda p: False)
    assert "declined" in t2["control_type"]("hi", "youtube music") and not h2.log


def test_approval_expires():
    clock = [1000.0]
    asked = []
    t, _ = make(lambda p: asked.append(p) or True, clock=lambda: clock[0])
    t["control_focus"]("youtube music")
    clock[0] += 700
    t["control_focus"]("youtube music")
    assert len(asked) == 2


def test_action_limit_and_stop_button():
    t, hands = make()
    for _ in range(40):
        t["control_keys"]("tab", "youtube music")
    assert "limit" in t["control_keys"]("tab", "youtube music")
    t2, h2 = make(cancelled=lambda: True)
    assert "Stopped" in t2["control_keys"]("tab", "youtube music") and not h2.log


def test_typing_into_a_sign_in_window_asks_again_with_the_text():
    asked = []
    t, hands = make(lambda p: asked.append(p) or True)
    t["control_type"]("hello", "sign in")
    assert len(asked) == 2 and "hello" in asked[1] and ("type", "hello") in hands.log


def test_missing_window_asks_the_model_to_check_with_the_user():
    t, _ = make()
    assert "Ask the user whether to open it" in t["control_click"]("x", "discord")


def test_click_uses_the_located_point_in_screen_coordinates(monkeypatch):
    import voice_assistant.computer as C

    monkeypatch.setattr(C, "locate", lambda llm, png, desc, ask=None: (200.0, 100.0))
    t, hands = make()
    out = t["control_click"]("the Search box", "youtube music")
    assert hands.log == [("click", 300, 150, "left", False)] and "Clicked" in out
    monkeypatch.setattr(C, "locate", lambda *a, **k: "none")
    assert "could not see" in t["control_click"]("the moon", "youtube music")


def test_locate_zooms_in_on_the_first_guess():
    from PySide6.QtGui import QImage
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from voice_assistant.computer import _to_png

    img = QImage(1600, 960, QImage.Format_ARGB32)
    img.fill(0xFF202020)
    answers = iter(["C3", "D4"])
    pos = locate(None, _to_png(img), "a thing", ask=lambda llm, q, png: next(answers))
    # first pass: column C (index 2) of 8, row 3 (index 2) of 6 -> cell 200x160 at (400, 320); zoom box (300..700, 240..560); second pass D4 -> col 3, row 3 of 6
    assert abs(pos[0] - (300 + 3.5 * 400 / 6)) < 1 and abs(pos[1] - (240 + 3.5 * 320 / 6)) < 1
