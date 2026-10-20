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


def png_of(color):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from PySide6.QtGui import QImage
    from voice_assistant.computer import _to_png

    img = QImage(1600, 960, QImage.Format_ARGB32)
    img.fill(color)
    return _to_png(img)


FAKE_TEXT = [{"text": "Search box", "x": 600, "y": 20, "w": 100, "h": 20}, {"text": "Play", "x": 1200, "y": 260, "w": 40, "h": 20}]


def make_task(script, colors=None, ocr=lambda png: FAKE_TEXT, media_status=lambda: "", **kw):
    """A computer_task tool whose 'model' replies from `script` and whose screen shows a new colour after every action."""
    hands = Hands()
    shots = iter(colors or [0xFF000000 + 0x101010 * i for i in range(40)])
    decisions = iter(script)
    notes = []
    tools = build_computer_tools(Config(), lambda p: True, lambda: None, windows=lambda: WINS, capture=lambda *a, **k: png_of(next(shots)), rect=lambda h: (100, 50, 1600, 960),
                                 focus=lambda h: None, hands=hands, ask=lambda *a: "C3", progress=notes.append, sleep=lambda s: None,
                                 decide=lambda llm, system, prompt, png: next(decisions), ocr=ocr, media_status=media_status, **kw)
    return {t.name: t.handler for t in tools}, hands, notes


def test_parse_action_accepts_fences_and_chatter():
    from voice_assistant.computer import parse_action

    assert parse_action('```json\n{"action":"key","keys":"enter"}\n```') == {"action": "key", "keys": "enter"}
    assert parse_action('Sure! {"action": "click", "target": "box", "cell": "B2"} hope that helps')["cell"] == "B2"
    assert parse_action("no json here") is None and parse_action('{"nothing": 1}') is None


def test_task_loop_clicks_types_presses_and_finishes():
    script = ['{"action":"click","target":"search box","cell":"D1"}', '{"action":"type","text":"billions and billions","enter":true}',
              '{"action":"double_click","target":"first song","cell":"B3"}', '{"action":"done","summary":"playing Billions and Billions"}']
    t, hands, notes = make_task(script)
    out = t["computer_task"]("play billions and billions", "youtube music")
    assert out == "Done: playing Billions and Billions"
    kinds = [h[0] for h in hands.log]
    assert kinds == ["click", "type", "keys", "click"] and hands.log[3][4] is True  # the last click was a double click
    assert len(notes) == 4 and "search box" in notes[0]


def test_task_loop_zooms_into_the_chosen_cell_before_clicking():
    t, hands, _ = make_task(['{"action":"click","target":"x","cell":"D1"}', '{"action":"done","summary":"ok"}'])
    t["computer_task"]("g", "youtube music")
    x, y = hands.log[0][1], hands.log[0][2]
    # cell D1 of 8x6 on 1600x960 is x 600-800, y 0-160; zoom box x 500-900, y 0-240; fake zoom answer C3 -> col 2,row 2 of 6
    assert x == 100 + int(500 + 2.5 * 400 / 6) and y == 50 + int(0 + 2.5 * 240 / 6)


def test_task_loop_gives_up_when_nothing_changes_and_on_garbage():
    same = [0xFF303030] * 10
    t, _, _ = make_task(['{"action":"key","keys":"tab"}'] * 10, colors=same)
    assert "stopped changing" in t["computer_task"]("g", "youtube music")
    t2, _, _ = make_task(["blah", "blah", "blah", "blah"])
    assert "could not decide" in t2["computer_task"]("g", "youtube music")


def test_task_loop_respects_blocks_stop_and_step_limit():
    t, hands, _ = make_task(['{"action":"key","keys":"win+r"}', '{"action":"key","keys":"a"}', '{"action":"done","summary":"x"}'])
    t["computer_task"]("g", "youtube music")
    assert [h for h in hands.log if h[0] == "keys"] == [("keys", "a")]  # the Windows-key shortcut never ran
    assert "Refused" in make_task([])[0]["computer_task"]("g", "powershell")
    t3, h3, _ = make_task(['{"action":"key","keys":"a"}'] * 3, cancelled=lambda: True)
    assert "Stopped" in t3["computer_task"]("g", "youtube music") and not h3.log
    t4, _, _ = make_task(['{"action":"wait","seconds":1}'] * 5)
    assert "step limit" in t4["computer_task"]("g", "youtube music", 3)


def test_clicking_by_ocr_id_hits_the_exact_text_centre_and_prompt_lists_the_text():
    prompts = []
    hands = Hands()
    decisions = iter(['{"action":"click","target":"the Play button","id":2}', '{"action":"done","summary":"ok"}'])

    def decide(llm, system, prompt, png):
        prompts.append(prompt)
        return next(decisions)

    shots = iter([0xFF101010, 0xFF202020, 0xFF303030])
    tools = build_computer_tools(Config(), lambda p: True, lambda: None, windows=lambda: WINS, capture=lambda *a, **k: png_of(next(shots)), rect=lambda h: (100, 50, 1600, 960),
                                 focus=lambda h: None, hands=hands, sleep=lambda s: None, decide=decide, ocr=lambda png: FAKE_TEXT, media_status=lambda: "")
    out = {t.name: t.handler for t in tools}["computer_task"]("play it", "youtube music")
    assert out.startswith("Done") and hands.log == [("click", 100 + 1220, 50 + 270, "left", False)]
    assert '[2] "Play" @G2' in prompts[0] and '[1] "Search box"' in prompts[0]


def test_parse_ocr_output_and_description():
    from voice_assistant.computer import describe_text, parse_ocr

    lines = parse_ocr("94|8|80|9|YouTube Music" + chr(10) + "bad line" + chr(10) + "1228|261|26|14|Play" + chr(10) + "5|5|x|y|broken" + chr(10))
    assert lines == [{"text": "YouTube Music", "x": 94, "y": 8, "w": 80, "h": 9}, {"text": "Play", "x": 1228, "y": 261, "w": 26, "h": 14}]
    assert describe_text(lines, 1920, 1032).splitlines()[1] == '[2] "Play" @F2'


def test_task_finishes_by_itself_when_windows_reports_the_requested_song_playing():
    t, hands, _ = make_task([], media_status=lambda: "Chrome. is now playing: Billions and Billions by Stellardrone.")
    out = t["computer_task"]("Search for Billions and Billions by Stellardrone and play it", "youtube music")
    assert out.startswith("Done:") and not hands.log
    t2, h2, _ = make_task(['{"action":"done","summary":"x"}'], media_status=lambda: "Chrome. is now playing: Something Else by Someone.")
    assert t2["computer_task"]("play Billions and Billions", "youtube music") == "Done: x"
    t3, h3, _ = make_task(['{"action":"done","summary":"y"}'], media_status=lambda: "Chrome. is now playing: Billions and Billions by Stellardrone.")
    assert t3["computer_task"]("open the Explore page", "youtube music") == "Done: y"  # no music words in the goal: the status is ignored


def test_type_with_replace_selects_the_old_text_first():
    t, hands, _ = make_task(['{"action":"type","text":"new query","enter":true,"replace":true}', '{"action":"type","text":"plain"}', '{"action":"done","summary":"x"}'])
    t["computer_task"]("g", "youtube music")
    assert hands.log == [("keys", "ctrl+a"), ("type", "new query"), ("keys", "enter"), ("type", "plain")]


def test_where_clicks_the_picture_above_a_title_and_clamps_inside_the_window():
    from voice_assistant.computer import offset_point

    ln = {"text": "Beautiful Mistakes", "x": 400, "y": 700, "w": 200, "h": 20}
    assert offset_point(500, 710, ln, "above") == (500, 600) and offset_point(500, 710, ln, "left") == (340, 710)
    assert offset_point(500, 710, ln, "right") == (660, 710) and offset_point(500, 710, ln, "below") == (500, 760) and offset_point(500, 710, ln, None) == (500, 710)
    t, hands, _ = make_task(['{"action":"click","target":"cover","id":2,"where":"above"}', '{"action":"done","summary":"ok"}'])
    t["computer_task"]("g", "youtube music")
    assert hands.log == [("click", 100 + 1220, 50 + 160, "left", False)]  # the Play text line sits at y=260; the picture is 100 px above it


def test_repeated_failures_are_fed_back_to_the_model():
    prompts = []
    hands = Hands()
    decisions = iter(['{"action":"click","target":"the title","id":1}'] * 3 + ['{"action":"done","summary":"x"}'])

    def decide(llm, system, prompt, png):
        prompts.append(prompt)
        return next(decisions)

    shots = iter([0xFF000000 + 0x101010 * i for i in range(10)])
    tools = build_computer_tools(Config(), lambda p: True, lambda: None, windows=lambda: WINS, capture=lambda *a, **k: png_of(next(shots)), rect=lambda h: (0, 0, 1600, 960),
                                 focus=lambda h: None, hands=hands, sleep=lambda s: None, decide=decide, ocr=lambda png: FAKE_TEXT, media_status=lambda: "")
    {t.name: t.handler for t in tools}["computer_task"]("g", "youtube music")
    assert "did not work" not in prompts[1] and "did not work" in prompts[2] and "the title (x2)" in prompts[2]


def test_a_song_started_by_the_very_last_step_still_counts_as_done():
    states = iter(["", "Chrome. is now playing: Cataclysm by Sibewest."])  # nothing at the start of step 1; playing after the last step
    t, hands, _ = make_task(['{"action":"key","keys":"a"}'], media_status=lambda: next(states))
    out = t["computer_task"]("Play the song Cataclysm by Sibewest", "youtube music", 1)
    assert out.startswith("Done:") and "Cataclysm" in out


def test_risky_clicks_and_keys_need_a_yes():
    from voice_assistant.computer import risky_click, risky_keys

    assert risky_click("the Delete button") and risky_click("button", "Confirm purchase") and risky_click("Send") and not risky_click("the Play button", "Play")
    assert risky_keys("delete") and risky_keys("alt+f4") and risky_keys("ctrl+w") and not risky_keys("enter") and not risky_keys("ctrl+f")


def run_with_confirm(script, answer, text_lines=None):
    asked, hands = [], Hands()
    decisions = iter(script)
    shots = iter([0xFF000000 + 0x101010 * i for i in range(20)])
    tools = build_computer_tools(Config(), lambda p: asked.append(p) or (answer if "wants to" in p else True), lambda: None, windows=lambda: WINS,
                                 capture=lambda *a, **k: png_of(next(shots)), rect=lambda h: (0, 0, 1600, 960), focus=lambda h: None, hands=hands,
                                 sleep=lambda s: None, decide=lambda *a: next(decisions), ocr=lambda png: text_lines or FAKE_TEXT, media_status=lambda: "",
                                 foreground=lambda: 1, verify=lambda *a: True, ask=lambda *a: "C3")
    return {t.name: t.handler for t in tools}["computer_task"]("g", "youtube music"), asked, hands


def test_a_risky_click_is_not_made_when_declined_and_is_when_allowed():
    out, asked, hands = run_with_confirm(['{"action":"click","target":"the Delete button","cell":"A1"}'], False)
    assert "NOT DONE" in out and not hands.log and any("wants to click" in a for a in asked)
    out2, _, hands2 = run_with_confirm(['{"action":"click","target":"the Delete button","cell":"A1"}', '{"action":"done","summary":"x"}'], True)
    assert out2.startswith("Done") and hands2.log[0][0] == "click"
    out3, asked3, hands3 = run_with_confirm(['{"action":"key","keys":"alt+f4"}'], False)
    assert "NOT DONE" in out3 and not hands3.log


def test_done_needs_a_second_look_and_the_window_must_really_be_in_front():
    decisions = iter(['{"action":"done","summary":"early"}', '{"action":"done","summary":"early again"}', '{"action":"done","summary":"third"}'])
    answers = iter([False, False])
    tools = build_computer_tools(Config(), lambda p: True, lambda: None, windows=lambda: WINS, capture=lambda *a, **k: png_of(0xFF303030), rect=lambda h: (0, 0, 1600, 960),
                                 focus=lambda h: None, hands=Hands(), sleep=lambda s: None, decide=lambda *a: next(decisions), ocr=lambda png: [], media_status=lambda: "",
                                 foreground=lambda: 1, verify=lambda *a: next(answers, True))
    out = {t.name: t.handler for t in tools}["computer_task"]("g", "youtube music")
    assert out == "Done: third"  # two early 'done's were rejected by the second look, the third passed
    blocked = build_computer_tools(Config(), lambda p: True, lambda: None, windows=lambda: WINS, capture=lambda *a, **k: b"", rect=lambda h: (0, 0, 1, 1), focus=lambda h: None,
                                   hands=Hands(), foreground=lambda: 999)
    out2 = {t.name: t.handler for t in blocked}["control_keys"]("tab", "youtube music")
    assert "did not come to the front" in out2 and "NOT DONE" in out2


def test_failures_are_labelled_not_done_and_time_is_capped():
    t, _, _ = make_task(["blah"] * 6)
    assert t["computer_task"]("g", "youtube music").startswith("NOT DONE")
    ticks = iter(range(0, 10000, 100))
    tools = build_computer_tools(Config(), lambda p: True, lambda: None, windows=lambda: WINS, capture=lambda *a, **k: png_of(0xFF303030), rect=lambda h: (0, 0, 1600, 960),
                                 focus=lambda h: None, hands=Hands(), sleep=lambda s: None, decide=lambda *a: '{"action":"wait","seconds":1}', ocr=lambda png: [], media_status=lambda: "",
                                 foreground=lambda: 1, clock=lambda: next(ticks))
    assert "ran out of time" in {t.name: t.handler for t in tools}["computer_task"]("g", "youtube music", 20)
