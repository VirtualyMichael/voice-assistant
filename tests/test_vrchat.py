import json
import struct

from voice_assistant.config import Config
from voice_assistant.vrchat import BUILT_IN, CHATBOX_MAX, VRChat, build_vrchat_tools, osc_message, words


def parse(data: bytes):
    """Decode one OSC message back into (address, [args]) so the tests check what VRChat would receive."""
    end = data.index(b"\0")
    addr = data[:end].decode()
    pos = (end + 4) & ~3
    tend = data.index(b"\0", pos)
    tags = data[pos + 1:tend].decode()
    pos = (tend + 4) & ~3
    args = []
    for t in tags:
        if t == "T":
            args.append(True)
        elif t == "F":
            args.append(False)
        elif t == "i":
            args.append(struct.unpack(">i", data[pos:pos + 4])[0])
            pos += 4
        elif t == "f":
            args.append(round(struct.unpack(">f", data[pos:pos + 4])[0], 4))
            pos += 4
        elif t == "s":
            e = data.index(b"\0", pos)
            args.append(data[pos:e].decode())
            pos = (e + 4) & ~3
    return addr, args


def make(tmp_path, avatars=None, **cfg):
    sent = []
    root = tmp_path / "OSC"
    for n, (aid, params) in enumerate(avatars or []):
        d = root / "usr_1" / "Avatars"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{aid}.json").write_text(json.dumps({"id": aid, "name": aid, "parameters": [
            {"name": name, "input": {"address": f"/avatar/parameters/{name}", "type": typ}, "output": {"address": f"/avatar/parameters/{name}", "type": typ}} for name, typ in params]}), encoding="utf-8")
        import os
        os.utime(d / f"{aid}.json", (1000 + n, 1000 + n))  # later avatars are newer
    now = [100.0]
    vr = VRChat(Config(vrchat_enabled=True, **cfg), send=sent.append, config_root=root, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))
    return vr, sent, now


def test_osc_messages_are_encoded_the_way_vrchat_expects():
    raw = osc_message("/chatbox/input", "hi", True, False)
    assert len(raw) % 4 == 0 and parse(raw) == ("/chatbox/input", ["hi", True, False])
    assert parse(osc_message("/avatar/parameters/Wings", 1.0)) == ("/avatar/parameters/Wings", [1.0])
    assert parse(osc_message("/avatar/parameters/Emote", 3)) == ("/avatar/parameters/Emote", [3])
    assert parse(osc_message("/chatbox/input", "héllo ✨", True, True))[1][0] == "héllo ✨"
    assert osc_message("/input/Jump", 1) == b"/input/Jump\0,i\0\0\0\0\0\x01"  # byte-exact per the OSC spec: 11 characters + the null fill 12 bytes, the tag string pads to 4, the int is big-endian


def test_chatbox_is_cut_to_144_and_spaced_out(tmp_path):
    vr, sent, now = make(tmp_path)
    out = vr.say("x" * 300)
    assert parse(sent[0]) == ("/chatbox/input", ["x" * CHATBOX_MAX, True, False]) and "cut" in out and "does not confirm" in out
    start = now[0]
    vr.say("second")
    assert now[0] - start >= 1.5 and len(sent) == 2  # a second message waits instead of tripping VRChat's rate limit
    assert vr.say("   ").startswith("Error")


def test_parameters_are_found_by_the_name_the_user_says(tmp_path):
    vr, sent, _ = make(tmp_path, [("avtr_old", [("Cape", "Bool"), ("Hat", "Bool")]),
                                  ("avtr_new", [("HatToggle", "Bool"), ("Wing_Size", "Float"), ("Outfit", "Int"), ("Voice", "Bool"), ("VelocityX", "Float")])])
    assert [p["name"] for p in vr.parameters()][:5] == ["HatToggle", "Wing_Size", "Outfit", "Cape", "Hat"] and "VelocityX" not in vr.listing() and "Voice" not in vr.listing()
    assert vr.find_param("hat toggle")[0]["name"] == "HatToggle" and vr.find_param("wing size")[0]["name"] == "Wing_Size"
    assert vr.find_param("hat")[0]["name"] == "Hat"  # an exact name beats the longer one that contains it
    assert vr.find_param("capee")[0]["name"] == "Cape"  # speech slips
    p, closest = vr.find_param("tail")
    assert p is None and closest


def test_setting_and_toggling_send_the_right_types(tmp_path):
    vr, sent, _ = make(tmp_path, [("avtr_a", [("Hat", "Bool"), ("Wing_Size", "Float"), ("Outfit", "Int")])])
    vr.set_param("hat", "on")
    vr.set_param("hat", "toggle")  # was on, so it turns off
    vr.set_param("hat", "toggle")
    vr.set_param("wing size", "0.5")
    vr.set_param("outfit", "2")
    got = [parse(m) for m in sent]
    assert got == [("/avatar/parameters/Hat", [True]), ("/avatar/parameters/Hat", [False]), ("/avatar/parameters/Hat", [True]),
                   ("/avatar/parameters/Wing_Size", [0.5]), ("/avatar/parameters/Outfit", [2])]
    assert vr.set_param("hat", "banana").startswith("Error")
    assert "closest" in vr.set_param("tail", "on") and len(sent) == 5  # an unknown name sends nothing


def test_without_a_saved_config_an_exact_name_still_works_but_is_marked_unchecked(tmp_path):
    vr, sent, _ = make(tmp_path)
    out = vr.set_param("Dance", "on")
    assert parse(sent[0]) == ("/avatar/parameters/Dance", [True]) and "unchecked" in out
    assert "No avatar parameters found" in vr.listing() and "OSC" in vr.listing()
    assert vr.set_param("../../bad name!", "on").startswith("Error") and len(sent) == 1


def test_buttons_are_pressed_then_released(tmp_path):
    vr, sent, _ = make(tmp_path)
    vr.press("jump")
    assert [parse(m) for m in sent] == [("/input/Jump", [1]), ("/input/Jump", [0])]
    sent.clear()
    assert "microphone" in vr.press("mute") and [parse(m) for m in sent] == [("/input/Voice", [1]), ("/input/Voice", [0])]
    assert vr.press("fly").startswith("Error")


def test_tools_exist_only_when_vrchat_is_switched_on():
    assert build_vrchat_tools(Config()) == []
    names = [t.name for t in build_vrchat_tools(Config(vrchat_enabled=True))]
    assert names == ["vrchat_say", "vrchat_set", "vrchat_parameters", "vrchat_press"]
    assert words("HatToggle") == "hat toggle" and words("Wing_Size2") == "wing size2" and "Voice" in BUILT_IN
