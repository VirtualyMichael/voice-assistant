import json

from test_vrchat import FakeVRChat, make, parse

from voice_assistant.vrchat import VRCHAT_TOOL_NAMES, build_vrchat_tools
from voice_assistant.vrchat_menu import match_entries, parse_menu

AVATAR = {
    "shirtToggle": ("T", False, 3), "wolfToggle": ("T", False, 3), "tailSway": ("T", False, 3), "CatEarTwitch": ("i", 0, 3),
    "hueShift": ("f", 0.25, 3), "glassesToggle": ("T", True, 3), "Go/Float": ("f", 0.25, 3),
}


def worn(tmp_path, **kw):
    vrc = FakeVRChat(params=dict(AVATAR))
    vr, sent, now = make(tmp_path, vrc=vrc, **kw)
    return vr, vrc, sent


def test_a_name_that_is_not_a_switch_is_a_dead_end_no_more(tmp_path):
    vr, vrc, sent = worn(tmp_path)
    out = vr.listing("animal parts")
    assert "wolfToggle" in out and "guesses" in out and "vrchat_remember" in out  # related words found the likely switches
    out = vr.listing("zzz nothing")
    assert "EVERY switch" in out and "shirtToggle" in out and "glassesToggle" in out  # and when nothing fits, the whole list is given


def test_taught_names_work_by_voice_and_are_checked(tmp_path):
    vr, vrc, sent = worn(tmp_path)
    assert "Not saved" in vr.remember("Animal Parts", "wolfToggle, nothingHere")
    assert "Remembered" in vr.remember("Animal Parts", "wolf toggle, tailSway")
    out = vr.set_param("animal parts", "on")
    assert vrc.params["wolfToggle"][1] is True and vrc.params["tailSway"][1] is True and "VRChat confirms every one" in out
    vr.set_group("Animal Parts", "off")
    assert vrc.params["wolfToggle"][1] is False and vrc.params["tailSway"][1] is False
    assert "Animal Parts = wolfToggle, tailSway" in vr.listing("animal")


def test_remembered_names_belong_to_the_avatar_that_taught_them(tmp_path):
    vr, vrc, sent = worn(tmp_path)
    vr.remember("Animal Parts", "wolfToggle")
    vrc.avatar = "avtr_other"
    out = vr.set_param("animal parts", "on")
    assert vrc.params["wolfToggle"][1] is False  # another avatar: the nickname does not apply
    assert "Animal Parts" not in vr.notes_report()
    vrc.avatar = "avtr_worn"
    assert "Animal Parts = wolfToggle" in vr.notes_report() and "tailSway" in vr.notes_report()  # tailSway still has no name


def test_an_exact_switch_name_beats_a_nickname_and_forget_works(tmp_path):
    vr, vrc, sent = worn(tmp_path)
    vr.remember("shirt toggle", "wolfToggle")
    vr.set_param("shirtToggle", "on")
    assert vrc.params["shirtToggle"][1] is True and vrc.params["wolfToggle"][1] is False
    assert "Forgot" in vr.forget("shirt toggle") and "Nothing taught" in vr.forget("shirt toggle")


def test_unclear_names_ask_instead_of_guessing(tmp_path):
    vr, vrc, sent = worn(tmp_path)
    vr.remember("Hat Ears", "wolfToggle")
    vr.remember("Cat Ears", "CatEarTwitch")
    out = vr.set_param("ears", "on")
    assert "more than one" in out and "Hat Ears" in out and "Cat Ears" in out and vrc.params["wolfToggle"][1] is False


def test_flash_shows_the_change_then_restores_every_switch(tmp_path):
    vr, vrc, sent = worn(tmp_path)
    before = {k: v[1] for k, v in vrc.params.items()}
    out = vr.flash("wolfToggle, glassesToggle, hueShift", 2)
    assert "wolfToggle" in out and "Skipped sliders: hueShift" in out and "WARNING" not in out
    assert {k: v[1] for k, v in vrc.params.items()} == before
    values = [parse(m)[1][0] for m in sent if parse(m)[0].endswith("wolfToggle")]
    assert values == [True, False]  # on for a moment, then back


def test_menu_and_notes_need_vrchat_to_know_the_avatar(tmp_path):
    vr, sent, now = make(tmp_path)
    for out in (vr.remember("x", "y"), vr.notes_report(), vr.flash("y"), vr.import_menu(str(tmp_path))):
        assert "VRChat running" in out


def test_matching_prefers_the_folder_over_its_contents_and_ignores_plurals():
    entries = [
        {"label": "Accessories/Animal Parts", "params": ["wolfToggle", "tailSway"], "kind": "folder", "source": "unity"},
        {"label": "Accessories/Animal Parts/Ears", "params": ["wolfToggle"], "kind": "toggle", "source": "unity"},
        {"label": "Accessories/Glasses", "params": ["glassesToggle"], "kind": "toggle", "source": "unity"},
    ]
    assert match_entries(entries, "animal part")[0]["label"] == "Accessories/Animal Parts"
    assert match_entries(entries, "accessories animal parts")[0]["label"] == "Accessories/Animal Parts"
    assert match_entries(entries, "ears")[0]["params"] == ["wolfToggle"]
    assert match_entries(entries, "the glasses toggle")[0]["label"] == "Accessories/Glasses"
    assert match_entries(entries, "banana") == (None, [])


MENU_ROOT = """%YAML 1.1
--- !u!114 &11400000
MonoBehaviour:
  m_Name: MainMenu
  m_Script: {fileID: -1506855854, guid: 7d4c6a4a5d4e4d0aa5b3f8f3b1e0c9d1, type: 3}
  controls:
  - name: Shirt
    icon: {fileID: 0}
    type: 102
    parameter:
      name: shirtToggle
    value: 1
    style: 0
    subMenu: {fileID: 0}
    subParameters: []
    labels: []
  - name: Animal Parts
    icon: {fileID: 2800000, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, type: 3}
    type: 103
    parameter:
      name:
    value: 1
    style: 0
    subMenu: {fileID: 11400000, guid: 22222222222222222222222222222222, type: 2}
    subParameters:
    - name:
    labels: []
  - name: "Cat Ear Style"
    icon: {fileID: 0}
    type: 102
    parameter:
      name: CatEarTwitch
    value: 3
    style: 0
    subMenu: {fileID: 0}
    subParameters: []
    labels: []
"""
MENU_SUB = """%YAML 1.1
--- !u!114 &11400000
MonoBehaviour:
  m_Name: AnimalMenu
  controls:
  - name: Wolf Ears
    icon: {fileID: 0}
    type: 102
    parameter:
      name: wolfToggle
    value: 1
    style: 0
    subMenu: {fileID: 0}
    subParameters: []
    labels: []
  - name: Tail Sway
    icon: {fileID: 0}
    type: 102
    parameter:
      name: tailSway
    value: 1
    style: 0
    subMenu: {fileID: 0}
    subParameters: []
    labels: []
"""
MENU_OTHER = MENU_SUB.replace("wolfToggle", "someOtherAvatar").replace("tailSway", "alsoOther")


def project(tmp_path):
    a = tmp_path / "Proj" / "Assets" / "Menus"
    a.mkdir(parents=True)
    for name, guid, text in (("Main", "11111111111111111111111111111111", MENU_ROOT), ("Animals", "22222222222222222222222222222222", MENU_SUB), ("Other", "33333333333333333333333333333333", MENU_OTHER)):
        (a / f"{name}.asset").write_text(text, encoding="utf-8")
        (a / f"{name}.asset.meta").write_text(f"fileFormatVersion: 2\nguid: {guid}\n", encoding="utf-8")
    junk = tmp_path / "Proj" / "Library"
    junk.mkdir()
    (junk / "Skipped.asset").write_text(MENU_SUB, encoding="utf-8")
    return tmp_path / "Proj"


def test_a_unity_menu_file_is_read_with_its_sub_menu_and_values():
    controls = parse_menu(MENU_ROOT)
    assert [(c["name"], c["type"], c["param"], c["value"]) for c in controls] == [("Shirt", 102, "shirtToggle", 1.0), ("Animal Parts", 103, "", 1.0), ("Cat Ear Style", 102, "CatEarTwitch", 3.0)]
    assert controls[1]["sub"] == "2" * 32 and parse_menu("nothing to see") is None


def test_importing_the_project_menu_makes_menu_names_work_by_voice(tmp_path):
    vr, vrc, sent = worn(tmp_path)
    out = vr.import_menu(str(project(tmp_path)))
    assert "Imported" in out and "Animal Parts" in out
    vr.set_param("animal parts", "on")
    assert vrc.params["wolfToggle"][1] is True and vrc.params["tailSway"][1] is True
    vr.set_param("Wolf Ears", "off")
    assert vrc.params["wolfToggle"][1] is False and vrc.params["tailSway"][1] is True
    vr.set_param("cat ear style", "on")
    assert vrc.params["CatEarTwitch"][1] == 3  # the menu's own number for that item, not a plain 1
    vr.set_param("cat ear style", "off")
    assert vrc.params["CatEarTwitch"][1] == 0
    assert "menu imported from" in vr.notes_report() and "Animal Parts" in vr.notes_report()


def test_importing_picks_the_menu_that_belongs_to_the_worn_avatar_and_reports_a_wrong_folder(tmp_path):
    vr, vrc, sent = worn(tmp_path)
    assert "Error" in vr.import_menu(str(tmp_path / "nowhere"))
    empty = tmp_path / "Empty"
    empty.mkdir()
    assert "No avatar menu" in vr.import_menu(str(empty))
    vrc.params = {"unrelated": ("T", False, 3)}
    assert "none uses a parameter the worn avatar has" in vr.import_menu(str(project(tmp_path)))


def test_taught_names_survive_a_reimport_and_win_over_imported_ones(tmp_path):
    vr, vrc, sent = worn(tmp_path)
    vr.remember("Animal Parts", "CatEarTwitch")
    vr.import_menu(str(project(tmp_path)))
    saved = json.loads((tmp_path / "vrchat_avatars.json").read_text(encoding="utf-8"))["avtr_worn"]
    assert saved["entries"][0]["source"] == "taught" and saved["imported_from"]
    vr.set_param("animal parts", "on")
    assert vrc.params["CatEarTwitch"][1] == 1 and vrc.params["wolfToggle"][1] is False  # what the user taught wins


def test_the_new_tools_are_offered_and_disabled_with_vrchat_off(tmp_path):
    vr, vrc, sent = worn(tmp_path)
    from voice_assistant.config import Config

    names = {t.name for t in build_vrchat_tools(Config(vrchat_enabled=True), vr)}
    assert {"vrchat_remember", "vrchat_forget", "vrchat_notes", "vrchat_flash", "vrchat_import_menu"} <= names == set(VRCHAT_TOOL_NAMES)
    assert build_vrchat_tools(Config(vrchat_enabled=False), vr) == []
