"""VRChat over OSC: type in the chatbox, switch avatar parameters (toggles, outfits, emotes) and press a few game controls by voice.

OSC is VRChat's own, documented way for other programs to talk to the game: small UDP messages sent to this PC's VRChat (127.0.0.1, port 9000 by default). Nothing leaves the PC.
VRChat also runs an OSCQuery service (a small local web page) that says which avatar is worn, which parameters it has and what their values are right now. This module asks it,
so it only touches parameters the worn avatar really has and can check that a change took effect. Without OSCQuery it falls back to the avatar files VRChat saved, and says so.
See docs/VRCHAT.md."""
from __future__ import annotations

import json
import os
import re
import socket
import struct
import threading
import time
from pathlib import Path
from urllib.parse import quote

from .tools import Tool
from .ytm_browser import similarity

CHATBOX_MAX = 144  # VRChat's limit for one chatbox message
CHATBOX_GAP_S = 1.6  # VRChat throttles the chatbox when it is sent too often, so messages are spaced out
BUTTONS = {"mute": "Voice", "unmute": "Voice", "toggle mute": "Voice", "voice": "Voice", "jump": "Jump"}
# Parameters VRChat itself fills in; they are not switches the user can flip.
SEARCH_FILLER = {"all", "the", "my", "every", "of", "a", "an", "and", "to", "in", "on", "please"}  # words that say nothing about which switch is meant ('color' and 'toggle' do: avatars name switches shirtColor, hatToggle)
GROUP_FILLER = {"all", "the", "my", "every", "toggle", "toggles", "switch", "switches", "color", "colors", "colour", "colours", "folder", "group", "of"}
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
    def __init__(self, cfg, send=None, config_root: Path | None = None, clock=time.time, sleep=time.sleep, get=None, ports=None):
        """get(url) -> parsed JSON from VRChat's OSCQuery web page (raises when unreachable); ports() -> the TCP ports VRChat is listening on (tests replace both)."""
        self.cfg, self.clock, self.sleep = cfg, clock, sleep
        self._get = get or self._http_get
        self._ports = ports or self._vrchat_ports
        self._query_port: int | None = None
        self._dest: tuple[str, int] | None = None
        self.root = config_root if config_root is not None else default_config_root()
        self._send = send or self._udp_send
        self._sock: socket.socket | None = None
        self._last_chat = 0.0
        self._lock = threading.Lock()
        self.state: dict[str, object] = {}  # what this program last set (used only when VRChat cannot be asked)

    def _udp_send(self, data: bytes) -> None:
        if self._sock is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.sendto(data, self._dest or (self.cfg.vrchat_host, int(self.cfg.vrchat_port)))

    # ---- OSCQuery: what VRChat says about itself -----------------------------------------------
    @staticmethod
    def _http_get(url: str):
        import httpx

        r = httpx.get(url, timeout=2.0)
        r.raise_for_status()
        return r.json()

    def _vrchat_ports(self) -> list[int]:
        """TCP ports VRChat listens on (one of them is the OSCQuery page); the newest log's 'OSCQuery on <port>' line is the fallback."""
        found: list[int] = []
        try:
            import psutil

            for p in psutil.process_iter(["name"]):
                if (p.info.get("name") or "").lower().startswith("vrchat"):
                    conns = getattr(p, "net_connections", None) or p.connections
                    found += [c.laddr.port for c in conns(kind="tcp") if c.status == "LISTEN"]
        except Exception:
            pass
        try:
            logs = sorted(self.root.parent.glob("output_log*.txt"), key=lambda f: f.stat().st_mtime, reverse=True)
            if logs:
                m = re.findall(r"OSCQuery on (\d+)", logs[0].read_text(encoding="utf-8", errors="ignore"))
                found += [int(x) for x in m[-1:]]
        except Exception:
            pass
        return list(dict.fromkeys(found))

    def _base(self) -> str | None:
        """The OSCQuery address of the running VRChat, or None (VRChat is not running, or OSC is off). Also learns where VRChat wants OSC messages sent."""
        for port in ([self._query_port] if self._query_port else []) + [p for p in self._ports() if p != self._query_port]:
            try:
                info = self._get(f"http://127.0.0.1:{port}/?HOST_INFO")
            except Exception:
                continue
            if isinstance(info, dict) and "OSC_PORT" in info:
                self._query_port = port
                self._dest = (str(info.get("OSC_IP") or "127.0.0.1"), int(info["OSC_PORT"]))
                return f"http://127.0.0.1:{port}"
        self._query_port = None
        return None

    def live(self) -> dict | None:
        """The worn avatar as VRChat reports it right now: {'id', 'name', 'params': {name: {'name', 'type', 'value', 'writable'}}} or None when VRChat cannot be asked."""
        base = self._base()
        if base is None:
            return None
        try:
            tree = self._get(base + "/avatar")
        except Exception:
            return None
        contents = tree.get("CONTENTS") or {}
        ident = ((contents.get("change") or {}).get("VALUE") or [""])[0]
        params: dict[str, dict] = {}

        def walk(node: dict) -> None:
            if "TYPE" in node and str(node.get("FULL_PATH", "")).startswith("/avatar/parameters/"):
                name = node["FULL_PATH"][len("/avatar/parameters/"):]
                kind = {"T": "Bool", "F": "Bool", "i": "Int", "f": "Float"}.get(str(node["TYPE"]))
                if kind and name not in BUILT_IN:
                    v = (node.get("VALUE") or [None])[0]
                    params[name] = {"name": name, "type": kind, "value": v, "writable": int(node.get("ACCESS", 0)) >= 2}
                elif kind:
                    params.setdefault(name, {"name": name, "type": kind, "value": (node.get("VALUE") or [None])[0], "writable": False})
            for child in (node.get("CONTENTS") or {}).values():
                walk(child)

        walk(contents.get("parameters") or {})
        return {"id": ident, "name": self.avatar_name(ident), "params": params}

    def avatar_name(self, avatar_id: str) -> str:
        """The avatar's name from the file VRChat saved for it (the OSCQuery page gives only its id), or ''."""
        try:
            for f in self.root.glob(f"*/Avatars/{avatar_id}.json"):
                return str(json.loads(f.read_text(encoding="utf-8-sig")).get("name") or "")
        except Exception:
            pass
        return ""

    def read(self, name: str):
        """One parameter's value as VRChat reports it right now, or None."""
        base = self._base()
        if base is None:
            return None
        try:
            return (self._get(base + "/avatar/parameters/" + quote(name, safe="/")).get("VALUE") or [None])[0]
        except Exception:
            return None

    # ---- chatbox -------------------------------------------------------------------------
    def say(self, text: str) -> str:
        text = " ".join(text.split())
        if not text:
            return "Error: nothing to say."
        cut = len(text) > CHATBOX_MAX
        text = text[:CHATBOX_MAX]
        self._base()  # learn where VRChat listens (it may not be 9000)
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

    def find_param(self, query: str, params: list[dict] | None = None) -> tuple[dict | None, list[str]]:
        """(the parameter a spoken name clearly means, closest names)."""
        probe = words(query)
        def score(p: dict) -> float:
            theirs = words(p["name"])
            shared = any(len(w) >= 4 and w in theirs.split() for w in probe.split())  # 'Clothing/Shirt' shares the word 'shirt' with 'shirtToggle': a candidate to offer, never an automatic match
            return max(similarity(probe, theirs), 0.6 if shared else 0.0)

        scored = sorted(((score(p), p) for p in (self.parameters() if params is None else params)), key=lambda x: -x[0])
        closest = [p["name"] for _, p in scored[:6]]
        exact = [p for s, p in scored if words(p["name"]) == probe]
        if len(exact) == 1:
            return exact[0], closest
        if scored and scored[0][0] >= 0.85 and (len(scored) == 1 or scored[1][0] < scored[0][0] - 0.05):
            return scored[0][1], closest
        return None, closest

    def set_param(self, name: str, value: str = "toggle") -> str:
        val = str(value).strip().lower()
        live = self.live()
        if live is not None:
            who = f"your current avatar{' (' + live['name'] + ')' if live['name'] else ''}"
            switches = [p for p in live["params"].values() if p["writable"]]
            p, closest = self.find_param(name, switches)
            if p is None:
                if closest:
                    return f"{who[0].upper() + who[1:]} has no switch that matches '{name}'. Its closest are: {', '.join(closest)}. Ask the user which one, or call vrchat_parameters."
                return f"{who[0].upper() + who[1:]} has no switchable parameters that VRChat reports. Make sure OSC is enabled (Action Menu > Options > OSC > Enabled)."
            sent, err = self._target(p, val, p["value"])
            if err:
                return err
            self._send(osc_message("/avatar/parameters/" + p["name"], sent))
            self.state[p["name"]] = sent
            for _ in range(6):  # VRChat answers within a moment; look at what it reports
                self.sleep(0.25)
                now = self.read(p["name"])
                if now is not None and (now == sent or (isinstance(sent, float) and isinstance(now, (int, float)) and abs(now - sent) < 0.01)):
                    return f"Set {p['name']} to {self._shown(now)} on {who}. VRChat confirms it now reads {self._shown(now)}."
            return (f"I sent {p['name']} = {self._shown(sent)} to {who}, but VRChat still reports {self._shown(self.read(p['name']))}, so the change did not take. "
                    "Check Action Menu > Options > OSC > Enabled, or the avatar may be driving that parameter itself.")
        p, closest = self.find_param(name)
        if p is None:
            if closest:
                return (f"VRChat could not be asked which avatar is worn (is it running with OSC enabled?). From the saved avatar files, no parameter matches '{name}'. "
                        f"The closest are: {', '.join(closest)}. Ask the user which one, or call vrchat_parameters.")
            if not re.fullmatch(r"[A-Za-z0-9_ ]{1,60}", name.strip()):
                return "Error: that is not a parameter name."
            p, note = {"name": name.strip().replace(" ", ""), "type": "Bool"}, " (no saved avatar configuration was found, so the name and its type are unchecked)"
        else:
            note = " (VRChat could not be asked which avatar is worn, so this name comes from the saved avatar files and may belong to a different avatar)"
        sent, err = self._target(p, val, self.state.get(p["name"]))
        if err:
            return err
        self._send(osc_message("/avatar/parameters/" + p["name"], sent))
        self.state[p["name"]] = sent
        return f"Sent {p['name']} = {self._shown(sent)}{note}. Not verified: VRChat could not be reached to confirm it."

    @staticmethod
    def _shown(v) -> str:
        return ("on" if v else "off") if isinstance(v, bool) else ("unknown" if v is None else f"{v:g}" if isinstance(v, float) else str(v))

    @staticmethod
    def _target(p: dict, val: str, current) -> tuple[object, str]:
        """(the value to send in the parameter's own type, an error message). 'toggle' flips what VRChat reports now (or what was last set)."""
        kind = p["type"]
        on = val in ("on", "true", "yes", "enable", "enabled", "show", "1") or (val == "toggle" and not current)
        off = val in ("off", "false", "no", "disable", "disabled", "hide", "0") or (val == "toggle" and bool(current))
        if on:
            return (True if kind == "Bool" else 1 if kind == "Int" else 1.0), ""
        if off:
            return (False if kind == "Bool" else 0 if kind == "Int" else 0.0), ""
        try:
            num = float(val)
        except ValueError:
            return None, f"Error: '{val}' is not on, off, toggle or a number."
        return (bool(num) if kind == "Bool" else int(num) if kind == "Int" else num), ""

    def group_members(self, live: dict, group: str) -> tuple[list[dict], str]:
        """The worn avatar's on/off switches inside a folder of switches ('Stylization/Clothing' = every switch named Stylization/Clothing/...), and how the group matched.
        The folder is matched from the start of the name; only if nothing starts that way, a folder of that name anywhere in the path counts."""
        want = words(group).split()
        if not want:
            return [], ""
        members, how = self._members(live, want)
        if not members:  # 'stylization clothing color toggles' -> the folder 'Stylization/Clothing': filler words may be dropped when the exact words match nothing
            trimmed = [w for w in want if w not in GROUP_FILLER]
            if trimmed and trimmed != want:
                members, how = self._members(live, trimmed)
                how = how + "-trimmed" if members else how
        return members, how

    def _members(self, live: dict, want: list[str]) -> tuple[list[dict], str]:
        switches = [p for p in live["params"].values() if p["writable"] and p["type"] == "Bool" and "/" in p["name"]]
        starts = [p for p in switches if words(p["name"]).split()[:len(want)] == want and len(words(p["name"]).split()) > len(want)]
        if starts:
            return starts, "start"
        inside = []
        for p in switches:
            folders = [words(seg).split() for seg in p["name"].split("/")[:-1]]  # the name without its last part is the folder path
            flat = [w for f in folders for w in f]
            if any(flat[i:i + len(want)] == want for i in range(len(flat))):
                inside.append(p)
        return inside, "inside"

    def _apply(self, plan: list[tuple[dict, object]]) -> list[str]:
        """Send each (parameter, value), then ask VRChat what every one reads; returns the names that did not change."""
        for p, sent in plan:
            self._send(osc_message("/avatar/parameters/" + p["name"], sent))
            self.state[p["name"]] = sent
            self.sleep(0.03)  # a small gap so none of the burst is dropped
        later: dict = {}
        for _ in range(8):
            self.sleep(0.25)
            fresh = self.live()
            later = fresh["params"] if fresh else {}
            if later and all(later.get(p["name"], {}).get("value") == sent for p, sent in plan):
                break
        return [p["name"] for p, sent in plan if later.get(p["name"], {}).get("value") != sent]

    def set_many(self, names: str, value: str = "toggle") -> str:
        """Set the switches the assistant picked (after reading vrchat_parameters), given as exact names separated by commas, semicolons or new lines, to one value."""
        val = str(value).strip().lower()
        live = self.live()
        if live is None:
            return "Setting several switches needs VRChat to be running with OSC enabled, so the result can be checked. Start VRChat and enable OSC (Action Menu > Options > OSC > Enabled)."
        who = f"your current avatar{' (' + live['name'] + ')' if live['name'] else ''}"
        asked = [n.strip() for n in re.split(r"[;\n]|,(?!\s*\d)", names if isinstance(names, str) else ",".join(map(str, names))) if n.strip()]
        if not asked:
            return "Error: give the exact switch names, separated by commas."
        if len(asked) > 120:
            return "Error: that is too many switches at once."
        switches = [p for p in live["params"].values() if p["writable"]]
        plan, unknown = [], []
        for n in asked:
            p, _ = self.find_param(n, switches)
            if p is None:
                unknown.append(n)
                continue
            sent, err = self._target(p, val, p["value"])
            if err:
                return err
            if all(q["name"] != p["name"] for q, _ in plan):
                plan.append((p, sent))
        if not plan:
            return f"None of those names match a switch on {who}: {', '.join(unknown)}. Call vrchat_parameters to see the real names."
        failed = self._apply(plan)
        ons = sum(1 for p, sent in plan if sent)
        what = f"toggled each (now {ons} on, {len(plan) - ons} off)" if val == "toggle" else ("on" if plan[0][1] else "off")
        out = f"{'Set' if val != 'toggle' else 'Switched'} {len(plan) - len(failed)} of {len(plan)} switches on {who}: {what}. Changed: {', '.join(p['name'] for p, _ in plan)}."
        if unknown:
            out += f" Not found, skipped: {', '.join(unknown)}."
        return out + (f" VRChat did not change {len(failed)}: {', '.join(failed[:8])}." if failed else " VRChat confirms every one.")

    def set_group(self, group: str, value: str = "toggle") -> str:
        """Set every on/off switch in a folder at once ('Stylization/Clothing'), then check what VRChat reports for each."""
        val = str(value).strip().lower()
        live = self.live()
        if live is None:
            return "Setting a whole group needs VRChat to be running with OSC enabled, so it can list the avatar's switches and check the result. Start VRChat and enable OSC (Action Menu > Options > OSC > Enabled)."
        who = f"your current avatar{' (' + live['name'] + ')' if live['name'] else ''}"
        members, how = self.group_members(live, group)
        if not members:
            folders = sorted({"/".join(p["name"].split("/")[:-1]) for p in live["params"].values() if p["writable"] and p["type"] == "Bool" and "/" in p["name"]})
            return f"No folder of switches called '{group}' on {who}. Its folders are: {', '.join(folders[:30]) or 'none (its switches are not in folders)'}."
        if len(members) > 120:
            return f"'{group}' holds {len(members)} switches, which is too many to change at once. Name a smaller folder."
        plan = []
        for p in members:
            sent, err = self._target(p, val, p["value"])
            if err:
                return err
            plan.append((p, sent))
        failed = self._apply(plan)
        ons = sum(1 for p, sent in plan if sent)
        what = "toggled each (now " + f"{ons} on, {len(plan) - ons} off)" if val == "toggle" else ("on" if plan[0][1] else "off")
        base = f"{'Set' if val != 'toggle' else 'Switched'} {len(plan) - len(failed)} of {len(plan)} switches in '{group}' on {who}: {what}."
        if how == "inside":
            base += " (No switch name starts with that, so I used the folders called that.)"
        elif how.endswith("-trimmed"):
            base += f" (Nothing is called exactly '{group}', so I read it as the folder with the filler words removed.)"
        if failed:
            return base + f" VRChat did not change {len(failed)}: {', '.join(failed[:8])}{'...' if len(failed) > 8 else ''}. Check that OSC is enabled, or the avatar may drive those itself."
        return base + " VRChat confirms every one."

    @staticmethod
    def search_switches(live: dict, query: str) -> list[dict]:
        """The worn avatar's switches that fit a request like 'clothing color white', best first. Every word of the query may match anywhere in the switch's path
        ('Stylization/Clothing/Shirt' fits 'clothing shirt'); when no switch has all the words, those with the most words come first. Filler words are ignored."""
        rows = [p for p in live["params"].values() if p["writable"]]
        want = [w for w in words(query).split() if w not in SEARCH_FILLER]
        if not want:
            return rows
        scored = []
        for p in rows:
            have = words(p["name"]).split()
            hit = sum(any(w == h or (len(w) >= 4 and (w in h or h in w) and len(h) >= 4) for h in have) for w in want)
            if hit:
                scored.append((hit, p))
        full = [p for hit, p in scored if hit == len(want)]
        return full or [p for hit, p in sorted(scored, key=lambda x: -x[0])]

    def listing(self, query: str = "") -> str:
        live = self.live()
        if live is not None:
            rows = self.search_switches(live, query) if query else [p for p in live["params"].values() if p["writable"]]
            who = f"your current avatar{' (' + live['name'] + ')' if live['name'] else ''}"
            if not rows:
                return f"VRChat reports no switches on {who} that fit '{query}'. Try other words, or call vrchat_parameters with no words to see all of them."
            folders: dict[str, list[str]] = {}
            for p in rows:
                folder, _, leaf = p["name"].rpartition("/")
                label = leaf if p["type"] == "Bool" else f"{leaf} ({p['type'].lower()})"
                folders.setdefault(folder, []).append(f"{label} {self._shown(p['value'])}")
            shown = [f"[{f or 'no folder'}] " + ", ".join(v) for f, v in folders.items()]
            text = f"{len(rows)} switch(es) on {who}{' that fit ' + repr(query) if query else ''}, by folder with current values: " + "; ".join(shown)
            return text[:6000] + (" ... (more not shown; ask with fewer words)" if len(text) > 6000 else "")
        names = [p["name"] + ("" if p["type"] == "Bool" else f" ({p['type'].lower()})") for p in self.parameters() if not query or words(query) in words(p["name"])]
        if not names:
            return ("VRChat could not be asked, and no saved avatar parameters were found. Start VRChat and enable OSC (Action Menu > Options > OSC > Enabled), "
                    "then try again. You can still name a parameter exactly.")
        return (f"VRChat could not be asked which avatar is worn, so these are the parameters of every avatar saved on this PC ({len(names)}): "
                + ", ".join(names[:80]) + ("..." if len(names) > 80 else ""))

    # ---- game controls -------------------------------------------------------------------
    def press(self, action: str) -> str:
        button = BUTTONS.get(action.strip().lower())
        if button is None:
            return "Error: action must be one of: " + ", ".join(sorted(BUTTONS)) + "."
        before = self.read("MuteSelf") if button == "Voice" else None
        self._base()
        self._send(osc_message("/input/" + button, 1))
        self.sleep(0.15)
        self._send(osc_message("/input/" + button, 0))  # a button is pressed with 1 and must be released with 0
        if button == "Voice":
            if before is not None:
                for _ in range(6):
                    self.sleep(0.25)
                    after = self.read("MuteSelf")
                    if after is not None and after != before:
                        return f"Your VRChat microphone is now {'muted' if after else 'unmuted'}."
                return ("I pressed the mute toggle, but VRChat still reports your microphone as " + ("muted" if before else "unmuted") +
                        ". The toggle only works when VRChat's 'Toggle Voice' setting is on and you are not in a locked state.")
            return "Toggled your VRChat microphone (mute/unmute). VRChat could not be asked, so its new state cannot be reported."
        return f"Pressed {button} in VRChat."


VRCHAT_TOOL_NAMES = ("vrchat_say", "vrchat_set", "vrchat_set_group", "vrchat_set_many", "vrchat_parameters", "vrchat_press")


def build_vrchat_tools(cfg, vr: VRChat | None = None) -> list[Tool]:
    from .agent_tools import _obj, _str

    if not cfg.vrchat_enabled:
        return []
    vr = vr or VRChat(cfg)
    return [
        Tool("vrchat_say", "Type a message in the user's VRChat chatbox (other players see it; 144 characters). Use it only for words the user asked you to say there.", _obj({"text": _str}, ["text"]), vr.say),
        Tool("vrchat_set", "Switch a VRChat avatar parameter: a toggle, outfit piece, emote or slider, by the name the user says ('hat', 'wings', 'dance'). value is on, off, toggle or a number.",
             _obj({"name": _str, "value": _str}, ["name"]), vr.set_param),
        Tool("vrchat_set_group", "Switch EVERY on/off switch in a folder of the worn avatar's switches in one go, e.g. group 'Stylization/Clothing' (spoken 'stylization clothing') with value on, off, or toggle (flips each one from its own state). "
             "If the user names colours such as white or black, you do not know which one is 'on': use vrchat_parameters to look, or ask once.", _obj({"group": _str, "value": _str}, ["group", "value"]), vr.set_group),
        Tool("vrchat_set_many", "Set several switches the SAME way after you have picked them: names is the exact switch names from vrchat_parameters separated by commas, value is on, off or toggle. "
             "Use it when the user's request is looser than one folder, e.g. 'change all the clothing colors to white'.", _obj({"names": _str, "value": _str}, ["names", "value"]), vr.set_many),
        Tool("vrchat_parameters", "Search the worn avatar's switches with their current values, grouped by folder. query may be several words such as 'clothing color white' (any word can match anywhere in the switch name); no query lists everything. "
             "Read the names and decide which switches the user means; the folder and switch names usually say (Clothing, Color, White, Black, Invert, Hue...).", _obj({"query": _str}), vr.listing),
        Tool("vrchat_press", "Press a VRChat control: 'mute' (toggles the microphone) or 'jump'.", _obj({"action": _str}, ["action"]), vr.press),
    ]
