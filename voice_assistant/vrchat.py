"""VRChat over OSC: type in the chatbox, switch avatar parameters (toggles, outfits, emotes) and press a few game controls by voice.

OSC is VRChat's own, documented way for other programs to talk to the game: small UDP messages sent to 127.0.0.1:9000 on this PC. Nothing leaves the PC.
VRChat never answers (it does not confirm a message arrived), so the results say what was sent and what could not be checked. See docs/VRCHAT.md."""
from __future__ import annotations

import json
import os
import re
import socket
import struct
import threading
import time
from pathlib import Path

from .tools import Tool
from .ytm_browser import similarity

CHATBOX_MAX = 144  # VRChat's limit for one chatbox message
CHATBOX_GAP_S = 1.6  # VRChat throttles the chatbox when it is sent too often, so messages are spaced out
BUTTONS = {"mute": "Voice", "unmute": "Voice", "toggle mute": "Voice", "voice": "Voice", "jump": "Jump"}
# Parameters VRChat itself fills in; they are not switches the user can flip.
BUILT_IN = {"IsLocal", "Viseme", "Voice", "GestureLeft", "GestureRight", "GestureLeftWeight", "GestureRightWeight", "AngularY", "VelocityX", "VelocityY", "VelocityZ",
            "VelocityMagnitude", "Upright", "Grounded", "Seated", "AFK", "TrackingType", "VRMode", "MuteSelf", "InStation", "Earmuffs", "IsOnFriendsList", "AvatarVersion",
            "ScaleModified", "ScaleFactor", "ScaleFactorInverse", "EyeHeightAsMeters", "EyeHeightAsPercent", "IsAnimatorEnabled", "PreviewMode", "VisemeLevel"}


def _pad(b: bytes) -> bytes:
    return b + b"\0" * (-len(b) % 4)


def osc_message(address: str, *args) -> bytes:
    """One OSC 1.0 message: the address, then the argument types, then the arguments (bool, int, float and str are enough for VRChat)."""
    tags, data = ",", b""
    for a in args:
        if a is True:
            tags += "T"
        elif a is False:
            tags += "F"
        elif isinstance(a, int):
            tags += "i"
            data += struct.pack(">i", a)
        elif isinstance(a, float):
            tags += "f"
            data += struct.pack(">f", a)
        else:
            tags += "s"
            data += _pad(str(a).encode("utf-8") + b"\0")
    return _pad(address.encode() + b"\0") + _pad(tags.encode() + b"\0") + data


def default_config_root() -> Path:
    return Path(os.environ.get("USERPROFILE") or Path.home()) / "AppData" / "LocalLow" / "VRChat" / "VRChat" / "OSC"


def words(name: str) -> str:
    """'HatToggle', 'hat_toggle' and 'Hat toggle' all become 'hat toggle'."""
    return re.sub(r"[\W_]+", " ", re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name)).strip().lower()


class VRChat:
    def __init__(self, cfg, send=None, config_root: Path | None = None, clock=time.time, sleep=time.sleep):
        self.cfg, self.clock, self.sleep = cfg, clock, sleep
        self.root = config_root if config_root is not None else default_config_root()
        self._send = send or self._udp_send
        self._sock: socket.socket | None = None
        self._last_chat = 0.0
        self._lock = threading.Lock()
        self.state: dict[str, object] = {}  # what this program last set (VRChat is never asked, so a toggle flips from this)

    def _udp_send(self, data: bytes) -> None:
        if self._sock is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.sendto(data, (self.cfg.vrchat_host, int(self.cfg.vrchat_port)))

    # ---- chatbox -------------------------------------------------------------------------
    def say(self, text: str) -> str:
        text = " ".join(text.split())
        if not text:
            return "Error: nothing to say."
        cut = len(text) > CHATBOX_MAX
        text = text[:CHATBOX_MAX]
        with self._lock:
            wait = CHATBOX_GAP_S - (self.clock() - self._last_chat)
            if wait > 0:
                self.sleep(wait)
            self._send(osc_message("/chatbox/input", text, True, False))  # send now, no keyboard, no notification sound
            self._last_chat = self.clock()
        return f"Sent to the VRChat chatbox: \"{text}\"" + (" (cut to VRChat's 144-character limit)" if cut else "") + ". VRChat does not confirm receipt; it shows only if OSC is enabled in the game."

    # ---- avatar parameters ---------------------------------------------------------------
    def avatars(self) -> list[tuple[str, list[dict]]]:
        """(avatar id, parameters) for every avatar config VRChat has saved on this PC, newest first. A parameter is {'name', 'type'} (input side)."""
        out = []
        files = sorted(self.root.glob("*/Avatars/*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if self.root.exists() else []
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError):
                continue
            params = []
            for p in data.get("parameters", []):
                inp = p.get("input") or {}
                if p.get("name") and inp.get("address") and p["name"] not in BUILT_IN:
                    params.append({"name": p["name"], "type": str(inp.get("type", "Bool")).capitalize()})
            out.append((data.get("id") or f.stem, params))
        return out

    def parameters(self) -> list[dict]:
        """All switchable parameter names across the saved avatars (the newest avatar's type wins when two avatars share a name)."""
        seen: dict[str, dict] = {}
        for _, params in self.avatars():
            for p in params:
                seen.setdefault(p["name"], p)
        return list(seen.values())

    def find_param(self, query: str) -> tuple[dict | None, list[str]]:
        """(the parameter a spoken name clearly means, closest names)."""
        probe = words(query)
        scored = sorted(((similarity(probe, words(p["name"])), p) for p in self.parameters()), key=lambda x: -x[0])
        closest = [p["name"] for _, p in scored[:6]]
        exact = [p for s, p in scored if words(p["name"]) == probe]
        if len(exact) == 1:
            return exact[0], closest
        if scored and scored[0][0] >= 0.85 and (len(scored) == 1 or scored[1][0] < scored[0][0] - 0.05):
            return scored[0][1], closest
        return None, closest

    def set_param(self, name: str, value: str = "toggle") -> str:
        val = str(value).strip().lower()
        p, closest = self.find_param(name)
        if p is None:
            if closest:
                return f"No avatar parameter matches '{name}'. The closest are: {', '.join(closest)}. Ask the user which one, or call vrchat_parameters."
            if not re.fullmatch(r"[A-Za-z0-9_ ]{1,60}", name.strip()):
                return "Error: that is not a parameter name."
            p, note = {"name": name.strip().replace(" ", ""), "type": "Bool"}, " (no saved avatar configuration was found, so the name and its type are unchecked)"
        else:
            note = ""
        kind = p["type"]
        if val in ("on", "true", "yes", "enable", "enabled", "show", "1") or (val == "toggle" and not self.state.get(p["name"])):
            sent = True if kind == "Bool" else (1 if kind == "Int" else 1.0)
        elif val in ("off", "false", "no", "disable", "disabled", "hide", "0", "toggle"):
            sent = False if kind == "Bool" else (0 if kind == "Int" else 0.0)
        else:
            try:
                num = float(val)
            except ValueError:
                return f"Error: '{value}' is not on, off, toggle or a number."
            sent = bool(num) if kind == "Bool" else (int(num) if kind == "Int" else num)
        self._send(osc_message("/avatar/parameters/" + p["name"], sent))
        self.state[p["name"]] = sent
        shown = ("on" if sent else "off") if isinstance(sent, bool) else sent
        return f"Set {p['name']} to {shown}{note}. VRChat does not confirm it; if nothing changed, check Action Menu > Options > OSC > Enabled." + (
            " (A toggle flips from what this assistant last set, so if you changed it by hand say 'turn it on' or 'off'.)" if val == "toggle" else "")

    def listing(self, query: str = "") -> str:
        names = [p["name"] + ("" if p["type"] == "Bool" else f" ({p['type'].lower()})") for p in self.parameters() if not query or words(query) in words(p["name"])]
        if not names:
            return ("No avatar parameters found. VRChat writes an avatar's OSC configuration after you publish it and switch to it with OSC enabled "
                    "(Action Menu > Options > OSC > Enabled). You can still name a parameter exactly.")
        return f"{len(names)} avatar parameter(s): " + ", ".join(names[:80]) + ("..." if len(names) > 80 else "")

    # ---- game controls -------------------------------------------------------------------
    def press(self, action: str) -> str:
        button = BUTTONS.get(action.strip().lower())
        if button is None:
            return "Error: action must be one of: " + ", ".join(sorted(BUTTONS)) + "."
        self._send(osc_message("/input/" + button, 1))
        self.sleep(0.15)
        self._send(osc_message("/input/" + button, 0))  # a button is pressed with 1 and must be released with 0
        if button == "Voice":
            return "Toggled your VRChat microphone (mute/unmute). VRChat does not say which state it is now in, so it cannot be reported."
        return f"Pressed {button} in VRChat."


VRCHAT_TOOL_NAMES = ("vrchat_say", "vrchat_set", "vrchat_parameters", "vrchat_press")


def build_vrchat_tools(cfg, vr: VRChat | None = None) -> list[Tool]:
    from .agent_tools import _obj, _str

    if not cfg.vrchat_enabled:
        return []
    vr = vr or VRChat(cfg)
    return [
        Tool("vrchat_say", "Type a message in the user's VRChat chatbox (other players see it; 144 characters). Use it only for words the user asked you to say there.", _obj({"text": _str}, ["text"]), vr.say),
        Tool("vrchat_set", "Switch a VRChat avatar parameter: a toggle, outfit piece, emote or slider, by the name the user says ('hat', 'wings', 'dance'). value is on, off, toggle or a number.",
             _obj({"name": _str, "value": _str}, ["name"]), vr.set_param),
        Tool("vrchat_parameters", "List the user's avatar parameter names (optionally only those containing a word).", _obj({"query": _str}), vr.listing),
        Tool("vrchat_press", "Press a VRChat control: 'mute' (toggles the microphone) or 'jump'.", _obj({"action": _str}, ["action"]), vr.press),
    ]
