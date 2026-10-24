import json
from dataclasses import fields

import pytest

from test_remote import Setup, collect, has
from voice_assistant import remote_settings as rs
from voice_assistant.config import Config


def service(allow=False, devices=None, saved=None):
    saved = saved if saved is not None else []
    cfg = Config()
    svc = rs.SettingsService(cfg, lambda: allow, devices or (lambda kind: ["Mic A", "Mic B"]), saved.append)
    return svc, cfg, saved


# ---- nothing is forgotten ----
def test_every_setting_is_on_the_phone_or_deliberately_pc_only():
    assert rs.coverage_problems() == []
    assert len(rs.SETTINGS) + len(rs.PC_ONLY) == len(fields(Config))


def test_every_setting_has_a_label_and_help_a_known_section_and_a_matching_default():
    sections = {sid for sid, _, _ in rs.SECTIONS}
    defaults = Config()
    for s in rs.SETTINGS:
        assert s.label and s.help and s.section in sections, s.key
        value = getattr(defaults, s.key)
        if s.kind == "toggle":
            assert isinstance(value, bool), s.key
        elif s.kind == "number":
            assert not isinstance(value, bool) and isinstance(value, (int, float)), s.key
            assert s.lo <= value <= s.hi, f"{s.key} default {value} outside {s.lo}..{s.hi}"
            assert s.step and s.step > 0
        elif s.kind == "choice" and not s.device:
            assert value in {v for v, _ in s.choices} or s.allow_other, s.key
        elif s.kind == "text":
            assert isinstance(value, str) and len(value) <= s.maxlen or s.key == "system_prompt", s.key
        elif s.kind == "readonly":
            assert isinstance(value, (list, dict)), s.key


def test_the_default_system_prompt_fits_its_limit():
    assert len(Config().system_prompt) <= rs.BY_KEY["system_prompt"].maxlen


def test_settings_that_take_effect_at_once_are_the_audited_ones():
    live = {s.key for s in rs.SETTINGS if s.apply == rs.LIVE}
    assert live == set(rs.LIVE_VERIFIED)


def test_the_owners_switch_and_internal_values_cannot_be_changed_from_the_phone():
    svc, cfg, saved = service(allow=True)
    for key in ("remote_allow_safety_changes", "setup_done", "hotkey", "models_dir"):
        result = svc.change(key, True)
        assert not result["ok"] and "PC" in result["message"], key
    assert not cfg.remote_allow_safety_changes and not saved


def test_every_sensitive_setting_is_on_the_safety_list_or_is_about_where_data_goes_or_what_runs():
    sensitive = {s.key for s in rs.SETTINGS if s.sensitive}
    must_be_sensitive = {
        "allow_shell", "allow_web", "computer_control", "confirm_launch", "confirm_links", "confirm_screen", "confirm_clipboard",
        "confirm_routines", "cloud_ack", "system_prompt", "llm_provider", "llm_base_url", "remote_enabled", "delegate_enabled",
        "delegate_allow_edits", "odysseus_share_tools", "odysseus_auto_approve", "odysseus_launch", "chatterbox_launch", "delegate_cmd_claude",
    }
    assert must_be_sensitive <= sensitive


# ---- checking a change ----
def test_toggles_accept_only_on_or_off():
    svc, cfg, _ = service()
    assert svc.change("follow_up", False)["ok"] and cfg.follow_up is False
    for bad in ("yes", 1, None):
        assert not svc.change("follow_up", bad)["ok"]


def test_numbers_are_checked_against_their_range_and_type():
    svc, cfg, _ = service()
    assert svc.change("tts_speed", 1.25)["ok"] and cfg.tts_speed == 1.25
    assert svc.change("tts_speed", 2)["message"].startswith("That should be between")
    assert not svc.change("tts_speed", "fast")["ok"]
    assert not svc.change("tts_speed", True)["ok"]  # a toggle value is not a number
    assert svc.change("tts_threads", 4.0)["ok"] and cfg.tts_threads == 4 and isinstance(cfg.tts_threads, int)
    assert not svc.change("tts_threads", 4.5)["ok"]


def test_choices_must_be_one_of_the_options():
    svc, cfg, _ = service()
    assert svc.change("remote_output", "both")["ok"] and cfg.remote_output == "both"
    assert not svc.change("remote_output", "tv")["ok"] and cfg.remote_output == "both"


def test_a_choice_that_allows_other_values_accepts_a_short_typed_one_only():
    svc, cfg, _ = service()
    assert svc.change("main_language", "Swahili")["ok"] and cfg.main_language == "Swahili"
    assert not svc.change("main_language", "")["ok"]
    assert not svc.change("main_language", "a\nb")["ok"]


def test_audio_device_choices_come_from_this_pc():
    svc, cfg, _ = service()
    assert svc.change("input_device", "Mic B")["ok"] and cfg.input_device == "Mic B"
    assert svc.change("input_device", "")["ok"]  # the system default
    assert not svc.change("input_device", "Nonexistent mic")["ok"]
    broken, _, _ = service(devices=lambda kind: (_ for _ in ()).throw(OSError("no audio system")))
    assert broken.change("input_device", "")["ok"]


def test_text_has_a_length_limit_and_one_line_unless_multiline():
    svc, cfg, _ = service()
    assert svc.change("tool_filler", "Hold on.")["ok"] and cfg.tool_filler == "Hold on."
    assert not svc.change("tool_filler", "x" * 500)["ok"]
    assert not svc.change("tool_filler", "two\nlines")["ok"]
    svc, cfg, _ = service(allow=True)
    assert svc.change("system_prompt", "Line one.\nLine two.")["ok"]


def test_lists_and_tables_are_read_only():
    svc, cfg, _ = service(allow=True)
    for key in ("approved_folders", "mcp_servers", "routines", "app_shortcuts"):
        result = svc.change(key, ["C:/"])
        assert not result["ok"] and "edited on the PC" in result["message"], key


def test_unknown_settings_are_refused():
    svc, _, saved = service(allow=True)
    assert not svc.change("no_such_setting", 1)["ok"] and not saved


# ---- the safety lock ----
def test_safety_settings_are_locked_until_the_owner_allows_them():
    svc, cfg, saved = service(allow=False)
    result = svc.change("allow_shell", True)
    assert not result["ok"] and "Let my phone change safety settings" in result["message"]
    assert cfg.allow_shell is False and not saved
    unlocked, cfg2, saved2 = service(allow=True)
    assert unlocked.change("allow_shell", True)["ok"] and cfg2.allow_shell is True and saved2


def test_the_snapshot_marks_what_is_locked_and_never_leaks_a_locked_value_change():
    svc, cfg, _ = service(allow=False)
    items = {i["key"]: i for sec in svc.snapshot()["sections"] for i in sec["items"]}
    assert items["allow_shell"]["locked"] and items["allow_shell"]["sensitive"]
    assert not items["tts_speed"]["locked"] and not items["tts_speed"]["sensitive"]
    assert svc.snapshot()["allow_safety"] is False
    unlocked, _, _ = service(allow=True)
    assert not {i["key"]: i for sec in unlocked.snapshot()["sections"] for i in sec["items"]}["allow_shell"]["locked"]


# ---- apply, save, restart ----
def test_a_live_change_saves_and_needs_no_restart():
    svc, cfg, saved = service()
    result = svc.change("follow_up_s", 4)
    assert result == {"ok": True, "key": "follow_up_s", "value": 4.0, "restart": False, "message": "Saved."}
    assert saved == [cfg] and not svc.restart_pending


def test_a_startup_setting_saves_and_marks_a_restart():
    svc, cfg, saved = service()
    result = svc.change("tts_speed", 1.2)
    assert result["ok"] and result["restart"] and "restarts" in result["message"]
    assert svc.restart_pending and svc.snapshot()["restart_pending"] is True


def test_the_snapshot_reports_current_values_defaults_and_summaries():
    svc, cfg, _ = service()
    cfg.tts_speed = 1.3
    cfg.approved_folders = ["C:/a", "C:/b"]
    items = {i["key"]: i for sec in svc.snapshot()["sections"] for i in sec["items"]}
    assert items["tts_speed"]["value"] == 1.3 and items["tts_speed"]["default"] == 1.0
    assert items["tts_speed"]["min"] == 0.6 and items["tts_speed"]["max"] == 1.6
    assert items["approved_folders"]["summary"] == "2 items" and items["approved_folders"]["items"] == ["C:/a", "C:/b"]
    assert "value" not in items["approved_folders"]
    assert json.dumps(svc.snapshot())  # everything is plain JSON


def test_a_value_set_outside_the_choices_still_appears_so_the_phone_can_show_it():
    svc, cfg, _ = service()
    cfg.input_device = "Old USB mic"
    item = next(i for sec in svc.snapshot()["sections"] for i in sec["items"] if i["key"] == "input_device")
    assert {"value": "Old USB mic", "label": "Old USB mic"} in item["choices"]


def test_no_api_key_or_secret_value_is_ever_in_the_snapshot(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-super-secret-value")
    svc, cfg, _ = service(allow=True)
    assert "sk-super-secret-value" not in json.dumps(svc.snapshot())  # only the NAME of the variable is a setting


# ---- over the phone link ----
@pytest.fixture
def linked(tmp_path):
    s = Setup(tmp_path)
    saved = []
    s.server.settings._save = saved.append
    s.server.settings.device_names = lambda kind: ["Mic A"]
    yield s, saved
    s.close()


def test_the_phone_can_read_and_change_settings(linked):
    s, saved = linked
    ws = s.connect()
    collect(ws, has("ready"))
    import json as j

    ws.send(j.dumps({"type": "get_settings"}))
    snap = next(m for m in collect(ws, has("settings")) if m["type"] == "settings")
    assert snap["allow_safety"] is False and snap["sections"][0]["id"] == "voice"
    ws.send(j.dumps({"type": "set_setting", "key": "follow_up", "value": False}))
    result = next(m for m in collect(ws, has("setting_result")) if m["type"] == "setting_result")
    assert result["ok"] and result["key"] == "follow_up" and s.session.cfg.follow_up is False and saved


def test_the_phone_cannot_unlock_the_safety_settings_itself(linked):
    s, saved = linked
    ws = s.connect()
    collect(ws, has("ready"))
    ws.send(json.dumps({"type": "set_setting", "key": "remote_allow_safety_changes", "value": True}))
    refused = next(m for m in collect(ws, has("setting_result")) if m["type"] == "setting_result")
    assert not refused["ok"] and s.server.cfg.remote_allow_safety_changes is False
    ws.send(json.dumps({"type": "set_setting", "key": "allow_shell", "value": True}))
    locked = next(m for m in collect(ws, lambda ms: len([x for x in ms if x["type"] == "setting_result"]) >= 1) if m["type"] == "setting_result")
    assert not locked["ok"] and s.server.cfg.allow_shell is False and not saved
