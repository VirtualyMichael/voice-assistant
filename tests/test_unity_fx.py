import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from test_vrchat import FakeVRChat, make
from test_vrchat_menu import project

from voice_assistant.unity_fx import analyze_project, clip_active
from voice_assistant.vrchat_menu import AvatarNotes


def curve(attr, path, value):
    return f"""  - curve:
      serializedVersion: 2
      m_Curve:
      - serializedVersion: 3
        time: 0
        value: {value}
        inSlope: 0
      - serializedVersion: 3
        time: 0.0166
        value: {value}
        inSlope: 0
      m_PreInfinity: 2
    attribute: {attr}
    path: {path}
    classID: 1
"""


def clip(*curves):
    return "%YAML 1.1\n--- !u!74 &7400000\nAnimationClip:\n  m_Name: c\n  m_FloatCurves:\n" + "".join(curves) + "  m_PositionCurves: []\n"


def state(fid, name, guid):
    return f"--- !u!1102 &{fid}\nAnimatorState:\n  m_Name: {name}\n  m_Motion: {{fileID: 7400000, guid: {guid}, type: 2}}\n"


def transition(fid, mode, param, dst):
    return f"--- !u!1101 &{fid}\nAnimatorStateTransition:\n  m_Name: \n  m_Conditions:\n  - m_ConditionMode: {mode}\n    m_ConditionEvent: {param}\n    m_EventTreshold: 0\n  m_DstStateMachine: {{fileID: 0}}\n  m_DstState: {{fileID: {dst}}}\n"


def build(tmp_path):
    """An avatar whose Hat toggle works normally, whose Shirt toggle works backwards (the ON state hides it), whose Socks use a 'Hide Socks' blend shape, and whose Glow only changes colour."""
    root = tmp_path / "Game"
    (root / "ProjectSettings").mkdir(parents=True)
    anim = root / "Assets" / "Shared" / "Anims"
    ctl = root / "Assets" / "Avatar" / "Controllers"
    anim.mkdir(parents=True)
    ctl.mkdir(parents=True)
    clips = {"a" * 32: ("Hat On", clip(curve("m_IsActive", "Hat", 1))), "b" * 32: ("Hat Off", clip(curve("m_IsActive", "Hat", 0))),
             "c" * 32: ("Shirt Gone", clip(curve("m_IsActive", "Body/Shirt", 0))), "d" * 32: ("Shirt Shown", clip(curve("m_IsActive", "Body/Shirt", 1))),
             "e" * 32: ("Socks Hidden", clip(curve("blendShape.Hide Socks", "Accessories", 100))), "f" * 32: ("Socks Shown", clip(curve("blendShape.Hide Socks", "Accessories", 0))),
             "1" * 32: ("Glow", clip(curve("material._Color.r", "Glow", 1))), "2" * 32: ("Dummy", clip(curve("m_IsActive", "_Empty", 0)))}
    for guid, (name, text) in clips.items():
        (anim / f"{name}.anim").write_text(text, encoding="utf-8")
        (anim / f"{name}.anim.meta").write_text(f"fileFormatVersion: 2\nguid: {guid}\n", encoding="utf-8")
    body = ("%YAML 1.1\n" + state("101", "Hat On", "a" * 32) + state("102", "Hat Off", "b" * 32) + transition("201", 1, "hatToggle", "101") + transition("202", 2, "hatToggle", "102")
            + state("103", "Shirt X", "c" * 32) + state("104", "Shirt Y", "d" * 32) + transition("203", 1, "shirtToggle", "103") + transition("204", 2, "shirtToggle", "104")
            + state("105", "S1", "e" * 32) + state("106", "S2", "f" * 32) + transition("205", 1, "socksToggle", "105") + transition("206", 2, "socksToggle", "106")
            + state("107", "G", "1" * 32) + transition("207", 1, "glowToggle", "107") + state("108", "D", "2" * 32) + transition("208", 1, "dummyToggle", "108"))
    (ctl / "Avatar FX.controller").write_text(body, encoding="utf-8")
    (ctl / "Gesture.controller").write_text("%YAML 1.1\n" + state("101", "x", "b" * 32) + transition("201", 1, "gestureOnly", "101"), encoding="utf-8")
    return root / "Assets" / "Avatar"


def test_the_animations_say_which_switches_work_backwards(tmp_path):
    folder = build(tmp_path)
    found = analyze_project(folder, {"hatToggle", "shirtToggle", "socksToggle", "glowToggle", "dummyToggle", "gestureOnly"})
    assert {k: v["inverted"] for k, v in found.items()} == {"hatToggle": False, "shirtToggle": True, "socksToggle": True}  # colour and placeholder-only switches say nothing; Gesture controllers are not read
    assert "hides Shirt" in found["shirtToggle"]["why"] and "Hide Socks" in found["socksToggle"]["why"] and "Avatar FX.controller" in found["hatToggle"]["why"]


def test_the_controller_with_most_of_the_avatars_parameters_is_the_one_read(tmp_path):
    folder = build(tmp_path)
    other = folder / "Controllers" / "Other FX.controller"
    other.write_text("%YAML 1.1\n" + state("1", "a", "a" * 32) + transition("2", 1, "shirtToggle", "1"), encoding="utf-8")  # another avatar's: same name, says normal
    found = analyze_project(folder, {"shirtToggle", "hatToggle", "socksToggle"})
    assert found["shirtToggle"]["inverted"] is True and "Avatar FX.controller" in found["shirtToggle"]["why"]


def test_clip_reading_uses_the_last_key_and_hide_show_blend_shapes(tmp_path):
    p = tmp_path / "x.anim"
    p.write_text(clip(curve("blendShape.Show Wings", "Body", 100), curve("blendShape.Hide Horns", "Body", 100), curve("blendShape.Chubby", "Body", 100), curve("m_IsActive", "A/_Dummy", 1)), encoding="utf-8")
    assert clip_active(p) == {"Body: Show Wings": True, "Body: Hide Horns": False}


def test_importing_stores_polarity_from_unity_but_never_over_what_was_taught(tmp_path):
    params = {"hatToggle": ("T", False, 3), "shirtToggle": ("T", False, 3), "socksToggle": ("T", False, 3), "wolfToggle": ("T", False, 3), "tailSway": ("T", False, 3), "CatEarTwitch": ("i", 0, 3)}
    vrc = FakeVRChat(params=params)
    vr, sent, now = make(tmp_path, vrc=vrc)
    vr.note_switch("hatToggle", True, "I said so")  # taught: backwards, while Unity says normal
    folder = build(tmp_path)
    out = vr.import_menu(str(folder))
    assert "2 switch(es) work backwards" in out and "shirtToggle" in out and "disagree" in out and "hatToggle" in out
    sw = vr.notes.switch("avtr_worn")
    assert sw["shirtToggle"]["inverted"] and sw["shirtToggle"]["source"] == "unity" and sw["hatToggle"]["source"] == "taught" and sw["hatToggle"]["inverted"]
    vr.set_param("shirtToggle", "hide")
    assert vrc.params["shirtToggle"][1] is True  # hiding a backwards switch turns it on
    vr.set_param("socksToggle", "show")
    assert vrc.params["socksToggle"][1] is False
    assert "(read from Avatar FX.controller)" in vr.listing("shirt") or "BACKWARDS" in vr.listing("shirt")


def test_each_avatar_gets_its_own_file_that_can_be_edited_by_hand(tmp_path):
    notes = AvatarNotes(tmp_path / "avatars")
    notes.remember("avtr_1", "Nawtesy", "Animal Parts", ["wolfToggle"])
    notes.note_switches("avtr_1", "Nawtesy", ["shirtToggle"], True, "x")
    notes.remember("avtr_2", "Other: avatar?", "Hat", ["hat"])
    files = notes.files()
    assert sorted(n for n, _ in files) == ["Nawtesy", "Other: avatar?"] and all(p.suffix == ".json" for _, p in files)
    f = notes.path_for("avtr_1")
    assert f.name == "Nawtesy [avtr_1].json" and ":" not in notes.path_for("avtr_2").name
    data = json.loads(f.read_text(encoding="utf-8"))
    data["entries"][0]["params"] = ["tailSway"]  # a hand edit
    f.write_text(json.dumps(data), encoding="utf-8")
    assert notes.entries("avtr_1")[0]["params"] == ["tailSway"] and notes.switch("avtr_1")["shirtToggle"]["inverted"]
    f.write_text("{ not json", encoding="utf-8")  # a broken edit is kept, not overwritten, and does not crash anything
    assert notes.entries("avtr_1") == [] and "not valid JSON" in " ".join(n for n, _ in notes.files())
    notes.remember("avtr_1", "Nawtesy", "Hat", ["hat"])
    assert (tmp_path / "avatars" / "Nawtesy [avtr_1].broken").read_text(encoding="utf-8") == "{ not json"


def test_the_old_single_file_is_split_once(tmp_path):
    legacy = tmp_path / "vrchat_avatars.json"
    legacy.write_text(json.dumps({"avtr_a": {"name": "A", "entries": [{"label": "X", "params": ["x"], "values": {}, "kind": "taught", "source": "taught"}]}, "avtr_b": {"name": "B", "entries": []}}), encoding="utf-8")
    notes = AvatarNotes(tmp_path / "avatars", legacy_file=legacy)
    assert notes.entries("avtr_a")[0]["label"] == "X" and notes.path_for("avtr_b") and not legacy.exists() and (tmp_path / "vrchat_avatars.json.migrated").exists()
    AvatarNotes(tmp_path / "avatars", legacy_file=legacy)  # nothing left to do the second time


def test_hand_written_junk_in_a_notes_file_does_not_break_matching(tmp_path):
    notes = AvatarNotes(tmp_path)
    notes.remember("avtr_1", "N", "Hat", ["hat"])
    f = notes.path_for("avtr_1")
    data = json.loads(f.read_text(encoding="utf-8"))
    data["entries"] += ["junk", {"label": "NoParams"}, {"params": ["x"]}]
    data["switches"] = {"a": "junk", "b": {"inverted": True}}
    f.write_text(json.dumps(data), encoding="utf-8")
    assert [e["label"] for e in notes.entries("avtr_1")] == ["Hat"] and list(notes.switch("avtr_1")) == ["b"]


@pytest.fixture
def win(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    import voice_assistant.app as A
    from voice_assistant.config import Config

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(A, "save_config", lambda *a, **k: None)
    monkeypatch.setattr("voice_assistant.app.config_dir", lambda: tmp_path)
    w = A.MainWindow(Config(models_dir=str(tmp_path / "none"), setup_done=True))
    yield w
    w.close()


def test_the_settings_list_each_avatars_file_and_edit_opens_it(win, tmp_path, monkeypatch):
    assert win.avatar_box.itemText(0) == "(nothing saved yet)"
    AvatarNotes(tmp_path / "avatars").remember("avtr_1", "Nawtesy", "Hat", ["hat"])
    win._refresh_avatar_files()
    assert win.avatar_box.count() == 1 and win.avatar_box.itemText(0) == "Nawtesy"
    opened = []
    monkeypatch.setattr("os.startfile", lambda *a: opened.append(a), raising=False)
    win._edit_avatar_file()
    win._open_avatar_folder()
    assert opened[0][0].endswith("Nawtesy [avtr_1].json") and opened[1] == (tmp_path / "avatars",)
