from voice_assistant.config import Config
from voice_assistant.music import build_music_tools, is_ytm_window, play_in_ytm, ytm_windows


def test_only_app_windows_count_not_browser_tabs():
    assert is_ytm_window("YouTube Music", "chrome") and is_ytm_window("Maps - YouTube Music", "chrome")
    assert not is_ytm_window("YouTube Music - Google Chrome", "chrome") and not is_ytm_window("YouTube Music", "notepad")
    assert ytm_windows([(1, "YouTube Music", "chrome"), (2, "Discord", "Discord"), (3, "YouTube Music - Google Chrome", "chrome")]) == [1]


def run(before_windows, after_windows, closer=lambda h: True, fg=0, playing=lambda: True):
    state = {"w": list(before_windows)}
    launched, closed = [], []

    def launch(url):
        launched.append(url)
        state["w"] = list(after_windows)

    def close(h):
        closed.append(h)
        return closer(h)

    msg = play_in_ytm("ID123", lambda: list(state["w"]), launch, close, wait=lambda s: None, timeout=3, clock=iter(range(0, 100, 1)).__next__,
                      foreground=lambda: fg, playing=playing)
    return msg, launched, closed


def test_new_window_opens_and_old_one_closes():
    old, new = (1, "YouTube Music", "chrome"), (2, "YouTube Music", "chrome")
    msg, launched, closed = run([old], [old, new], fg=2)
    assert launched == ["https://music.youtube.com/watch?v=ID123"] and closed == [1] and "closed 1 extra" in msg


def test_front_window_is_kept_even_if_it_is_the_old_one():
    old, new = (1, "YouTube Music", "chrome"), (2, "YouTube Music", "chrome")
    _, _, closed = run([old], [old, new], fg=1)
    assert closed == [2]


def test_chrome_reusing_the_window_needs_no_closing():
    only = (1, "YouTube Music", "chrome")
    msg, launched, closed = run([only], [only], fg=1)
    assert closed == [] and msg == "Playing in YouTube Music."


def test_first_window_needs_no_closing():
    msg, launched, closed = run([], [(5, "YouTube Music", "chrome")], fg=5)
    assert closed == [] and msg.startswith("Playing in YouTube Music")


def test_music_is_restarted_if_closing_stopped_it():
    old, new = (1, "YouTube Music", "chrome"), (2, "YouTube Music", "chrome")
    msg, launched, closed = run([old], [old, new], fg=2, playing=lambda: False)
    assert len(launched) == 2 and "restarted" in msg


def test_window_that_will_not_close_is_reported_and_the_kept_one_never_closed():
    old, new = (1, "YouTube Music", "chrome"), (2, "YouTube Music", "chrome")
    msg, _, closed = run([old], [old, new], closer=lambda h: False, fg=2)
    assert closed == [1] and "would not close" in msg


def test_no_window_at_all():
    msg, _, closed = run([], [])
    assert closed == [] and "no YouTube Music window" in msg


def test_play_music_tool_resolves_confirms_and_launches():
    state = {"w": []}
    launches, asked = [], []
    import voice_assistant.music as M

    class P:
        def __init__(self, cmd):
            launches.append(cmd)
            state["w"] = [(9, "YouTube Music", "chrome")]

    orig = M.subprocess.Popen
    M.subprocess.Popen = P
    try:
        import voice_assistant.music as MM
        MM.foreground_window, MM.is_playing = lambda: 9, lambda: True
        t = build_music_tools(Config(), lambda p: asked.append(p) or True, lambda: list(state["w"]), launcher=lambda: ["app.exe", "--app-id=x"],
                              resolver=lambda q: ("VID", "Maps by Maroon 5"), close=lambda h: True, wait=lambda s: None)[0].handler
        out = t("maps by maroon 5")
    finally:
        M.subprocess.Popen = orig
    assert launches == [["app.exe", "--app-id=x", "https://music.youtube.com/watch?v=VID"]] and "Maps by Maroon 5" in asked[0] and "Playing" in out


def test_play_music_unknown_song_and_declined():
    t = build_music_tools(Config(), lambda p: False, lambda: [], resolver=lambda q: None)[0].handler
    assert "Could not find" in t("zzz")
    t2 = build_music_tools(Config(), lambda p: False, lambda: [], resolver=lambda q: ("V", "Song"))[0].handler
    assert "declined" in t2("song")
