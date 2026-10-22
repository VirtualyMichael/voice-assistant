import json
import os
import struct
from urllib.parse import unquote

import pytest

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


class FakeVRChat:
    """Plays VRChat's OSCQuery page and its OSC input: serves the worn avatar's parameters with live values and applies what is sent (unless `deaf`, like OSC switched off)."""

    def __init__(self, avatar="avtr_worn", params=None, osc_port=9000):
        # params: name -> (type tag, value, access)
        self.avatar, self.params, self.osc_port, self.deaf = avatar, dict(params or {}), osc_port, False
        self.gets = []

    def tree(self):
        root: dict = {}
        for name, (tag, value, access) in self.params.items():
            node = root
            parts = name.split("/")
            for i, part in enumerate(parts):
                node = node.setdefault("CONTENTS", {}).setdefault(part, {"FULL_PATH": "/avatar/parameters/" + "/".join(parts[:i + 1]), "ACCESS": 0})
            node.update({"TYPE": tag, "VALUE": [value], "ACCESS": access})
        return root

    def get(self, url: str):
        self.gets.append(url)
        path = url.split("127.0.0.1:", 1)[1].split("/", 1)[1] if "127.0.0.1:" in url else ""
        if path == "?HOST_INFO":
            return {"NAME": "VRChat-Client-TEST", "OSC_IP": "127.0.0.1", "OSC_PORT": self.osc_port, "OSC_TRANSPORT": "UDP"}
        if path == "avatar":
            return {"FULL_PATH": "/avatar", "CONTENTS": {"change": {"FULL_PATH": "/avatar/change", "TYPE": "s", "VALUE": [self.avatar], "ACCESS": 3}, "parameters": {"FULL_PATH": "/avatar/parameters", **self.tree()}}}
        if path.startswith("avatar/parameters/"):
            name = unquote(path[len("avatar/parameters/"):])
            tag, value, access = self.params[name]
            return {"FULL_PATH": "/" + path, "TYPE": tag, "VALUE": [value], "ACCESS": access}
        raise KeyError(path)

    def receive(self, data: bytes):
        addr, args = parse(data)
        name = addr.removeprefix("/avatar/parameters/")
        if addr.startswith("/avatar/parameters/") and name in self.params and not self.deaf and self.params[name][2] >= 2:
            tag, _, access = self.params[name]
            self.params[name] = (tag, args[0], access)


def nothing(url):
    raise OSError("VRChat is not running")


def make(tmp_path, avatars=None, vrc: FakeVRChat | None = None, **cfg):
    sent = []
    root = tmp_path / "OSC"
    for n, (aid, params) in enumerate(avatars or []):
        d = root / "usr_1" / "Avatars"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{aid}.json").write_text(json.dumps({"id": aid, "name": "Name of " + aid, "parameters": [
            {"name": name, "input": {"address": f"/avatar/parameters/{name}", "type": typ}, "output": {"address": f"/avatar/parameters/{name}", "type": typ}} for name, typ in params]}), encoding="utf-8")
        os.utime(d / f"{aid}.json", (1000 + n, 1000 + n))  # later avatars are newer
    now = [100.0]

    def send(data):
        sent.append(data)
        if vrc is not None:
            vrc.receive(data)

    vr = VRChat(Config(vrchat_enabled=True, **cfg), send=send, config_root=root, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s),
                get=vrc.get if vrc else nothing, ports=lambda: [53232] if vrc else [])
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


# ---------- with VRChat answering (OSCQuery) -----------------------------------------------------------------
WORN = {"shirtToggle": ("T", False, 3), "shirtColor": ("T", False, 3), "Go/Pose": ("i", 0, 3), "wingSize": ("f", 0.2, 3), "Clothing/Shirt Sleeve": ("T", True, 3),
        "MuteSelf": ("T", False, 1), "VelocityX": ("f", 0.0, 1), "Voice": ("f", 0.0, 1)}
OTHER_AVATARS = [("avtr_other", [("Stylization/Clothing/Shirt", "Bool"), ("ShirtToggle", "Bool"), ("Shirt", "Bool")])]


def test_only_the_worn_avatars_own_parameters_are_used_never_another_avatars(tmp_path):
    vrc = FakeVRChat("avtr_worn", WORN)
    vr, sent, _ = make(tmp_path, [("avtr_worn", [("shirtToggle", "Bool")])] + OTHER_AVATARS, vrc)
    live = vr.live()
    assert live["id"] == "avtr_worn" and live["name"] == "Name of avtr_worn"
    assert set(p["name"] for p in live["params"].values() if p["writable"]) == {"shirtToggle", "shirtColor", "Go/Pose", "wingSize", "Clothing/Shirt Sleeve"}
    out = vr.set_param("shirt toggle", "off")  # 'ShirtToggle' on another avatar must not make this ambiguous
    assert "Set shirtToggle to off" in out and "VRChat confirms" in out and parse(sent[-1]) == ("/avatar/parameters/shirtToggle", [False])
    out = vr.set_param("Stylization/Clothing/Shirt", "off")  # a name that exists only on another avatar: refused, nothing sent
    assert "has no switch that matches" in out and "shirtToggle" in out and len(sent) == 1


def test_changes_are_verified_against_what_vrchat_reports(tmp_path):
    vrc = FakeVRChat("avtr_worn", WORN)
    vr, sent, _ = make(tmp_path, vrc=vrc)
    assert "Set shirtToggle to on" in vr.set_param("shirt toggle", "toggle") and vrc.params["shirtToggle"][1] is True
    assert "Set shirtToggle to off" in vr.set_param("shirt toggle", "toggle")  # flips what VRChat reports, not a guess
    vrc.params["shirtToggle"] = ("T", True, 3)  # the user changed it by hand in the menu
    assert "Set shirtToggle to off" in vr.set_param("shirtToggle", "toggle") and vrc.params["shirtToggle"][1] is False
    assert "Set wingSize to 0.5" in vr.set_param("wing size", "0.5") and vrc.params["wingSize"][1] == 0.5
    assert "Set Go/Pose to 2" in vr.set_param("go pose", "2") and parse(sent[-1]) == ("/avatar/parameters/Go/Pose", [2])
    assert vr.set_param("wing size", "banana").startswith("Error")
    vrc.deaf = True  # VRChat ignores OSC (switched off): the result must not claim success
    out = vr.set_param("shirt color", "on")
    assert "did not take" in out and "OSC" in out and "Set shirtColor" not in out


def test_read_only_values_are_not_switches_and_the_list_shows_current_values(tmp_path):
    vrc = FakeVRChat("avtr_worn", WORN)
    vr, _, _ = make(tmp_path, vrc=vrc)
    listing = vr.listing()
    assert "shirtToggle (off)" in listing and "Clothing/Shirt Sleeve (on)" in listing and "wingSize (float, now 0.2)" in listing
    assert "VelocityX" not in listing and "MuteSelf" not in listing and "current avatar (Name of" not in listing  # no saved file for this avatar: no name, no crash
    assert "shirtColor" in vr.listing("shirt") and "wingSize" not in vr.listing("shirt")
    assert "no switch" in vr.set_param("MuteSelf", "on")  # read-only built-ins cannot be set


def test_messages_go_to_the_port_vrchat_says_it_listens_on(tmp_path):
    vrc = FakeVRChat("avtr_worn", WORN, osc_port=9123)
    vr, _, _ = make(tmp_path, vrc=vrc)
    vr.say("hi")
    assert vr._dest == ("127.0.0.1", 9123)  # VRChat may pick another port when 9000 is taken


def test_mute_reports_the_state_vrchat_shows(tmp_path):
    vrc = FakeVRChat("avtr_worn", WORN)
    vr, sent, _ = make(tmp_path, vrc=vrc)

    def receive(data):
        addr, args = parse(data)
        if addr == "/input/Voice" and args == [1]:
            vrc.params["MuteSelf"] = ("T", not vrc.params["MuteSelf"][1], 1)

    old = vr._send
    vr._send = lambda d: (old(d), receive(d))
    assert "now muted" in vr.press("mute") and "now unmuted" in vr.press("mute")
    vr._send = old  # the toggle does nothing (not in toggle mode): say so
    assert "still reports" in vr.press("mute")


# ---------- VRChat cannot be asked: the saved avatar files, clearly marked ------------------------------------
def test_without_vrchat_it_falls_back_to_saved_files_and_says_it_is_unverified(tmp_path):
    vr, sent, _ = make(tmp_path, [("avtr_old", [("Cape", "Bool"), ("Hat", "Bool")]),
                                  ("avtr_new", [("HatToggle", "Bool"), ("Wing_Size", "Float"), ("Outfit", "Int"), ("Voice", "Bool"), ("VelocityX", "Float")])])
    assert [p["name"] for p in vr.parameters()][:5] == ["HatToggle", "Wing_Size", "Outfit", "Cape", "Hat"] and "VelocityX" not in vr.listing() and "every avatar saved" in vr.listing()
    out = vr.set_param("hat toggle", "on")
    assert parse(sent[0]) == ("/avatar/parameters/HatToggle", [True]) and "Not verified" in out and "may belong to a different avatar" in out
    assert vr.find_param("wing size")[0]["name"] == "Wing_Size" and vr.find_param("hat")[0]["name"] == "Hat" and vr.find_param("capee")[0]["name"] == "Cape"
    out = vr.set_param("tail", "on")
    assert "could not be asked" in out and "closest" in out and len(sent) == 1  # an unknown name sends nothing


def test_without_saved_files_or_vrchat_an_exact_name_still_works_but_is_marked_unchecked(tmp_path):
    vr, sent, _ = make(tmp_path)
    out = vr.set_param("Dance", "on")
    assert parse(sent[0]) == ("/avatar/parameters/Dance", [True]) and "unchecked" in out and "Not verified" in out
    assert "could not be asked" in vr.listing() and "OSC" in vr.listing()
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


def test_it_finds_the_oscquery_port_from_the_log_when_processes_cannot_be_listed(tmp_path, monkeypatch):
    import psutil

    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))  # no VRChat process, whatever is running on this PC
    log_dir = tmp_path / "VRChat"
    (log_dir / "OSC").mkdir(parents=True)
    (log_dir / "output_log_2026-10-22.txt").write_text("x\n2026.10.22 Debug - Advertising Service VRChat-Client-1 of type OSCQuery on 53232\n", encoding="utf-8")
    vrc = FakeVRChat("avtr_worn", WORN)
    vr = VRChat(Config(vrchat_enabled=True), send=lambda d: None, config_root=log_dir / "OSC", get=vrc.get)  # default port finder: no VRChat process in the tests, so the log is used
    import voice_assistant.vrchat as mod

    vr._ports = lambda: mod.VRChat._vrchat_ports(vr)
    assert vr.live()["id"] == "avtr_worn" and vr._query_port == 53232
