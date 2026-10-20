import json

import voice_assistant.agent_tools as AT
from voice_assistant.agent_tools import build_agent_tools
from voice_assistant.apps import AppCatalog, parse_start_apps, score
from voice_assistant.config import Config
from voice_assistant.permissions import FolderPolicy
from voice_assistant.tools import ToolRegistry

INSTALLED = {
    "YouTube Music": "chrome_proxy!YTM", "Steam": "Valve.Steam", "SteamVR": "Valve.SteamVR", "Steam Support Center": "Valve.Support",
    "Notepad": "MS.Notepad", "Notepad++": "Npp", "Google Chrome": "Chrome", "VRChat": "VRC", "VRChat Creator Companion": "VCC",
    "Visual Studio Code": "VSCode", "Calculator": "MS.Calc", "projectM Music Visualizer": "pm",
    "Uninstall VRChat Creator Companion": "unVCC",
}


def catalog(apps=None):
    c = AppCatalog(lambda: dict(apps if apps is not None else INSTALLED))
    c.refresh()
    return c


def test_parse_handles_list_single_object_and_empty():
    raw = json.dumps([{"Name": "A", "AppID": "a"}, {"Name": "", "AppID": "x"}, {"Name": "B", "AppID": "b"}])
    assert parse_start_apps(raw) == {"A": "a", "B": "b"}
    assert parse_start_apps(json.dumps({"Name": "Solo", "AppID": "s"})) == {"Solo": "s"}  # PowerShell unwraps one result
    assert parse_start_apps("") == {}


def test_matching_by_spoken_name():
    c = catalog()
    for said, expected in [("youtube music", "YouTube Music"), ("YouTube Music app", "YouTube Music"), ("chrome", "Google Chrome"),
                           ("vs code", None), ("visual studio code", "Visual Studio Code"), ("calculator", "Calculator")]:
        best, found = c.resolve(said)
        assert (best.name if best else None) == expected, (said, found)


def test_exact_spelling_beats_lookalikes_and_close_calls_are_ambiguous():
    c = catalog()
    assert c.resolve("Notepad")[0].name == "Notepad"  # not Notepad++
    assert c.resolve("steam")[0].name == "Steam"  # not SteamVR
    best, found = c.resolve("vrchat companion")
    assert best is not None and best.name == "VRChat Creator Companion"
    assert catalog({"SteamVR": "v", "Steam": "s"}).resolve("steam vr")[0].name == "SteamVR"
    ambiguous = catalog({"Photo Editor Pro": "a", "Photo Editor Lite": "b"})
    best, found = ambiguous.resolve("photo editor")
    assert best is None and {m.name for m in found} == {"Photo Editor Pro", "Photo Editor Lite"}


def test_nothing_matches_gibberish():
    assert catalog().resolve("zzzz qqqq")[0] is None and catalog().find("zzzz qqqq") == []
    assert score("", "x") == 0 and score("x", "") == 0


def registry(tmp_path, answers=True, apps=None, **cfg_kw):
    asked = []
    cfg = Config(**cfg_kw)
    tools = build_agent_tools(cfg, FolderPolicy([tmp_path]), lambda q: asked.append(q) or answers, lambda t: None, catalog=catalog(apps))
    return ToolRegistry(tools), asked, cfg


def call(reg, tool, **args):
    return reg.call(tool, json.dumps(args))


def test_launch_installed_app_goes_through_start_menu_after_confirm(tmp_path, monkeypatch):
    launched = []
    monkeypatch.setattr(AT, "launch_start_app", launched.append)
    reg, asked, _ = registry(tmp_path)
    assert "Opened YouTube Music" in call(reg, "launch_app", name="youtube music")
    assert launched == ["chrome_proxy!YTM"] and asked == ["Open YouTube Music?"]


def test_declined_and_unconfirmed_modes(tmp_path, monkeypatch):
    launched = []
    monkeypatch.setattr(AT, "launch_start_app", launched.append)
    reg, asked, _ = registry(tmp_path, answers=False)
    assert "declined" in call(reg, "launch_app", name="steam") and launched == []
    reg, asked, _ = registry(tmp_path, answers=False, confirm_launch=False)  # "ask before opening apps" switched off
    assert "Opened Steam" in call(reg, "launch_app", name="steam") and asked == [] and launched == ["Valve.Steam"]


def test_ambiguous_and_missing_apps_give_the_model_something_useful(tmp_path):
    reg, _, _ = registry(tmp_path, apps={"Photo Editor Pro": "a", "Photo Editor Lite": "b"})
    out = call(reg, "launch_app", name="photo editor")
    assert "Several apps match" in out and "Photo Editor Pro" in out and "Photo Editor Lite" in out
    out = call(reg, "launch_app", name="spotify")
    assert "No installed app matches 'spotify'" in out and "Settings > Apps" in out


def test_users_own_shortcut_wins_and_bad_paths_are_reported(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(AT.os, "startfile", started.append, raising=False)
    exe = tmp_path / "mygame.exe"
    exe.write_text("x")
    reg, asked, cfg = registry(tmp_path)
    cfg.app_shortcuts["Steam"] = str(exe)  # overrides the Start-menu entry of the same name
    assert "Opened Steam" in call(reg, "launch_app", name="steam") and started == [str(exe)]
    cfg.app_shortcuts["Ghost"] = str(tmp_path / "missing.exe")
    assert "no longer exists" in call(reg, "launch_app", name="ghost")


def test_list_apps_search_and_summary(tmp_path):
    reg, _, _ = registry(tmp_path)
    assert "YouTube Music" in call(reg, "list_apps", query="youtube")
    assert call(reg, "list_apps").startswith(f"{len(INSTALLED)} apps installed")
    assert "No installed app matches" in call(reg, "list_apps", query="zzzz")


def test_failed_scan_leaves_previous_list_intact():
    c = AppCatalog(lambda: {"A": "a"})
    c.refresh()
    c._scanner = lambda: (_ for _ in ()).throw(OSError("powershell missing"))
    c.refresh()
    assert c.names() == ["A"]


def test_transcript_punctuation_and_filler_words_do_not_break_matching():
    c = catalog()
    for said in ["YouTube Music.", "Open YouTube Music.", "open the youtube music app.", "\"youtube music\"", "youtube music!", "you tube music",
                 "YouTube music, please."]:
        best, found = c.resolve(said)
        assert best is not None and best.name == "YouTube Music", (said, [(m.name, m.score) for m in found])


def test_partial_names_extra_words_and_misheard_spellings_still_find_the_app():
    c = catalog()
    for said, expected in [("chrome browser", "Google Chrome"), ("the chrome browser please", "Google Chrome"), ("creator companion", "VRChat Creator Companion"),
                           ("calculater", "Calculator"), ("note pad", "Notepad"), ("vr chat.", "VRChat"), ("steam v r", "SteamVR"), ("youtube", "YouTube Music")]:
        best, found = c.resolve(said)
        assert best is not None and best.name == expected, (said, [(m.name, round(m.score)) for m in found])


def test_uninstallers_never_beat_the_real_app():
    c = catalog()
    best, found = c.resolve("vrchat creator companion")
    assert best.name == "VRChat Creator Companion"
    uninstall = next(m for m in found if m.name == "Uninstall VRChat Creator Companion")
    assert found[0].score - uninstall.score >= 20


def test_vague_word_lists_candidates_instead_of_guessing():
    best, found = catalog().resolve("music")
    assert best is None and {m.name for m in found} >= {"YouTube Music", "projectM Music Visualizer"}


def test_not_found_message_offers_close_names(tmp_path, monkeypatch):
    launched = []
    monkeypatch.setattr(AT, "launch_start_app", launched.append)  # never start a real program from a test
    reg, _, _ = registry(tmp_path)
    out = call(reg, "launch_app", name="Youtub Musik.")
    assert "YouTube Music" in out or "Opened" in out
    out = call(reg, "launch_app", name="whatsapp")
    assert "No installed app matches 'whatsapp'" in out and "Settings > Apps" in out


def test_one_letter_words_do_not_cause_false_matches():
    c = catalog({"Garry's Mod": "g", "Steam": "s"})
    assert c.find("spotify") == []


def test_launch_focuses_an_app_that_is_already_open_instead_of_starting_another(monkeypatch, tmp_path):
    import voice_assistant.agent_tools as AT

    focused, launched = [], []
    monkeypatch.setattr(AT, "list_windows", lambda: [(7, "VRChat", "VRChat"), (8, "Steam Community - Chrome", "chrome")])
    monkeypatch.setattr(AT, "focus_window", focused.append)
    monkeypatch.setattr(AT, "launch_start_app", launched.append)
    tools = {t.name: t for t in build_agent_tools(Config(), FolderPolicy([tmp_path]), lambda q: True, lambda t: None,
                                                  catalog=catalog({"VRChat": "VRC"}))}
    out = tools["launch_app"].handler("VRChat")
    assert "already open" in out and focused == [7] and not launched
    # a browser tab that merely mentions the name does not count as the app being open
    tools2 = {t.name: t for t in build_agent_tools(Config(), FolderPolicy([tmp_path]), lambda q: True, lambda t: None,
                                                   catalog=catalog({"Steam": "Valve.Steam"}))}
    assert "Opened Steam" in tools2["launch_app"].handler("Steam") and launched == ["Valve.Steam"]


def test_look_at_missing_window_tells_the_model_to_ask_before_opening():
    from voice_assistant.screen import build_screen_tools

    t = build_screen_tools(Config(), lambda p: True, lambda: None, lambda *a, **k: b"", lambda: [(1, "Discord", "Discord")], lambda h: (0, 0, 1, 1))[0].handler
    out = t("translate", "VRChat")
    assert "ask the user whether to open it" in out and "Discord" in out
