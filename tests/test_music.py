from voice_assistant.config import Config
from voice_assistant.music import build_music_tools, is_ytm_window, play_in_ytm, ytm_windows


def test_only_app_windows_count_not_browser_tabs():
    assert is_ytm_window("YouTube Music", "chrome") and is_ytm_window("Maps - YouTube Music", "chrome")
    assert not is_ytm_window("YouTube Music - Google Chrome", "chrome") and not is_ytm_window("YouTube Music", "notepad")
    assert ytm_windows([(1, "YouTube Music", "chrome"), (2, "Discord", "Discord"), (3, "YouTube Music - Google Chrome", "chrome")]) == [1]


def run(before_windows, after_windows, closer=lambda h: True, fg=0, playing=lambda *a: True, nudge=None):
    state = {"w": list(before_windows)}
    launched, closed = [], []

    def launch(url):
        launched.append(url)
        state["w"] = list(after_windows)

    def close(h):
        closed.append(h)
        return closer(h)

    msg = play_in_ytm("ID123", lambda: list(state["w"]), launch, close, wait=lambda s: None, timeout=3, clock=iter(range(0, 100, 1)).__next__,
                      foreground=lambda: fg, playing=playing, nudge=nudge or (lambda h: None))
    return msg, launched, closed


def test_new_window_opens_and_old_one_closes():
    old, new = (1, "YouTube Music", "chrome"), (2, "YouTube Music", "chrome")
    msg, launched, closed = run([old], [old, new], fg=2)
    assert launched == ["https://music.youtube.com/watch?v=ID123"] and closed == [1] and "closed 1 extra" in msg


def test_the_new_window_holding_the_song_is_kept_even_if_the_old_one_is_in_front():
    old, new = (1, "YouTube Music", "chrome"), (2, "YouTube Music", "chrome")
    _, _, closed = run([old], [old, new], fg=1)
    assert closed == [1]


def test_old_songs_playing_does_not_count_as_started():
    from voice_assistant.music import is_playing
    import voice_assistant.extras as X

    X_orig = X.media_session
    X.media_session = lambda a: "Chrome is now playing: Lord of Nothing by Sub Urban."
    try:
        assert is_playing("Lord of Nothing") and not is_playing("Clouds")
    finally:
        X.media_session = X_orig


def test_chrome_reusing_the_window_needs_no_closing():
    only = (1, "YouTube Music", "chrome")
    msg, launched, closed = run([only], [only], fg=1)
    assert closed == [] and msg == "Playing in YouTube Music."


def test_first_window_needs_no_closing():
    msg, launched, closed = run([], [(5, "YouTube Music", "chrome")], fg=5)
    assert closed == [] and msg.startswith("Playing in YouTube Music")


def test_paused_song_gets_a_play_press_in_the_kept_window():
    old, new = (1, "YouTube Music", "chrome"), (2, "YouTube Music", "chrome")
    pressed, states = [], iter([False, True])
    msg, launched, closed = run([old], [old, new], fg=2, playing=lambda *a: next(states), nudge=pressed.append)
    assert pressed == [2] and len(launched) == 1 and msg.startswith("Playing in YouTube Music")  # no second launch


def test_already_playing_is_never_toggled():
    pressed = []
    msg, _, _ = run([], [(5, "YouTube Music", "chrome")], fg=5, playing=lambda *a: True, nudge=pressed.append)
    assert pressed == [] and msg.startswith("Playing")


def test_still_not_playing_after_the_nudge_says_so():
    pressed = []
    msg, _, _ = run([], [(5, "YouTube Music", "chrome")], fg=5, playing=lambda *a: False, nudge=pressed.append)
    assert pressed == [5] and "press play" in msg


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
        t = build_music_tools(Config(), lambda p: asked.append(p) or True, lambda: list(state["w"]), launcher=lambda: ["app.exe", "--app-id=x"],
                              resolver=lambda q: ("VID", "Maps by Maroon 5"), close=lambda h: True, wait=lambda s: None,
                              foreground=lambda: 9, playing=lambda *a: True, nudge=lambda h: None)[0].handler
        out = t("maps by maroon 5")
    finally:
        M.subprocess.Popen = orig
    assert launches == [["app.exe", "--app-id=x", "https://music.youtube.com/watch?v=VID"]] and "Maps by Maroon 5" in asked[0] and "Playing" in out


def test_play_music_unknown_song_and_declined():
    t = build_music_tools(Config(), lambda p: False, lambda: [], resolver=lambda q: None)[0].handler
    assert "Could not find" in t("zzz")
    t2 = build_music_tools(Config(), lambda p: False, lambda: [], resolver=lambda q: ("V", "Song"))[0].handler
    assert "declined" in t2("song")


def test_app_launcher_reads_the_start_menu_shortcut(tmp_path):
    import subprocess

    from voice_assistant.music import app_launcher

    exe = tmp_path / "chrome_proxy.exe"
    exe.write_text("x")

    class P:
        stdout = "C:/missing/other.exe|--app-id=zzz" + chr(10) + str(exe) + "| --profile-directory=Default --app-id=abc123" + chr(10)

    cmd = app_launcher(run=lambda *a, **k: P())
    assert cmd == [str(exe), "--profile-directory=Default", "--app-id=abc123"]
    P.stdout = ""
    assert app_launcher(run=lambda *a, **k: P()) is None
    assert app_launcher(run=lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("p", 1))) is None


def test_real_start_menu_lookup_does_not_crash():
    from voice_assistant.music import app_launcher

    r = app_launcher()
    assert r is None or "--app-id" in " ".join(r)
