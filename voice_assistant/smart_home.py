"""Smart-home control: Govee lights over the local network (no cloud, no key) and Home Assistant (which brings in Google Home,
Alexa-connected, Matter, Hue, Govee cloud and hundreds of other devices through one URL and token)."""
from __future__ import annotations

import json
import re
import socket
import time

import httpx

from .tools import Tool

GOVEE_MCAST, GOVEE_SCAN_PORT, GOVEE_REPLY_PORT, GOVEE_CTRL_PORT = "239.255.255.250", 4001, 4002, 4003
CONFIRM_DOMAINS = {"lock", "alarm_control_panel", "cover", "garage_door", "siren", "script", "shell_command"}  # these always ask first

COLORS = {
    "red": (255, 0, 0), "orange": (255, 100, 0), "yellow": (255, 210, 0), "green": (0, 255, 0), "lime": (120, 255, 0),
    "cyan": (0, 255, 255), "teal": (0, 160, 140), "blue": (0, 0, 255), "purple": (140, 0, 255), "violet": (140, 0, 255),
    "magenta": (255, 0, 255), "pink": (255, 80, 160), "white": (255, 255, 255),
}
WHITES = {"warm white": 2700, "warm": 2700, "soft white": 3000, "neutral white": 4000, "cool white": 6000, "cool": 6000, "daylight": 6500}


def parse_color(text: str) -> tuple[tuple[int, int, int] | None, int | None]:
    """'red', '#ff8800', '255,0,0' -> (rgb, None); 'warm white', '3000k' -> (None, kelvin); unknown -> (None, None)."""
    t = text.strip().lower()
    if t in WHITES:
        return None, WHITES[t]
    if m := re.fullmatch(r"(\d{4})\s*k", t):
        return None, max(2000, min(9000, int(m[1])))
    if m := re.fullmatch(r"#?([0-9a-f]{6})", t):
        return tuple(int(m[1][i:i + 2], 16) for i in (0, 2, 4)), None
    if m := re.fullmatch(r"(\d{1,3})\s*[, ]\s*(\d{1,3})\s*[, ]\s*(\d{1,3})", t):
        return tuple(min(255, int(x)) for x in m.groups()), None
    return COLORS.get(t), None


class GoveeLan:
    """Govee's LAN API (UDP). Each light needs 'LAN Control' switched on in the Govee Home app (Device settings), and a model that supports it."""

    def __init__(self, names: dict[str, str] | None = None):
        self.names = names if names is not None else {}  # ip -> friendly name chosen by the user
        self.devices: dict[str, dict] = {}  # ip -> scan data (device, sku)

    def discover(self, wait: float = 2.0) -> dict[str, dict]:
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            rx.bind(("", GOVEE_REPLY_PORT))
        except OSError:
            return self.devices  # port busy (another Govee tool); keep what we know
        rx.settimeout(0.3)
        msg = json.dumps({"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}).encode()
        try:
            hosts = {i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
        except OSError:
            hosts = set()
        for ip in hosts or {""}:  # multicast leaves through the interface it is told to, so try each
            tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                if ip:
                    tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
                tx.sendto(msg, (GOVEE_MCAST, GOVEE_SCAN_PORT))
            except OSError:
                pass
            finally:
                tx.close()
        end = time.time() + wait
        while time.time() < end:
            try:
                data, addr = rx.recvfrom(4096)
                d = json.loads(data)["msg"]["data"]
                self.devices[d.get("ip") or addr[0]] = d
            except (socket.timeout, KeyError, ValueError):
                continue
            except OSError:
                break
        rx.close()
        return self.devices

    def label(self, ip: str) -> str:
        d = self.devices.get(ip, {})
        return self.names.get(ip) or f"{d.get('sku', 'Govee')} {str(d.get('device', ip))[-5:].replace(':', '')}"

    def send(self, ip: str, cmd: str, data: dict) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.sendto(json.dumps({"msg": {"cmd": cmd, "data": data}}).encode(), (ip, GOVEE_CTRL_PORT))
        finally:
            s.close()

    def pick(self, target: str) -> list[str]:
        t = target.strip().lower()
        if t in ("", "all", "everything", "lights", "all lights"):
            return list(self.devices)
        bare = re.sub(r"\s*\(.*?\)", "", t).strip()  # the model often copies "Name (H6008)" from the list
        hits = [ip for ip in self.devices if t == ip or bare in self.label(ip).lower() or self.label(ip).lower() in bare]
        if not hits and len(self.devices) == 1 and re.search(r"light|lamp|led|strip", t):
            hits = list(self.devices)  # one light and the user said "the light"
        return hits

    def apply(self, ips: list[str], on: bool | None, brightness: int | None, color: str | None) -> str:
        rgb, kelvin = parse_color(color) if color else (None, None)
        if color and rgb is None and kelvin is None:
            return f"Error: I don't know the colour '{color}'. Use a basic colour name, a hex code like #ff8800, 'warm white' or '3000K'."
        for ip in ips:
            if on is not None:
                self.send(ip, "turn", {"value": 1 if on else 0})
            if rgb or kelvin:
                self.send(ip, "turn", {"value": 1})
                self.send(ip, "colorwc", {"color": dict(zip("rgb", rgb)) if rgb else {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": kelvin or 0})
            if brightness is not None:
                self.send(ip, "brightness", {"value": max(1, min(100, int(brightness)))})
        return "Done: " + ", ".join(self.label(i) for i in ips) + "."


class GoveeCloud(GoveeLan):
    """Govee's official cloud API (needs the user's own free developer key; Govee limits it to non-commercial use and about 10 requests a minute per light).
    Same interface as GoveeLan, so the tools do not care which one is active. Works for any light linked to the Govee Home app, with no LAN Control needed."""

    BASE = "https://openapi.api.govee.com/router/api/v1"

    def __init__(self, key: str, names: dict[str, str] | None = None, client: httpx.Client | None = None):
        super().__init__(names)
        self.http = client or httpx.Client(timeout=10, headers={"Govee-API-Key": key})

    def discover(self, wait: float = 0) -> dict[str, dict]:
        r = self.http.get(f"{self.BASE}/user/devices")
        r.raise_for_status()
        self.devices = {d["device"]: d for d in r.json().get("data", [])}
        return self.devices

    def label(self, ip: str) -> str:
        d = self.devices.get(ip, {})
        return self.names.get(ip) or d.get("deviceName") or d.get("sku", ip)

    def send(self, device: str, cmd: str, data: dict) -> None:
        """cmd is a capability instance (powerSwitch, brightness, colorRgb, colorTemperatureK); data["value"] is its value."""
        kind = {"powerSwitch": "on_off", "brightness": "range", "colorRgb": "color_setting", "colorTemperatureK": "color_setting"}[cmd]
        d = self.devices[device]
        r = self.http.post(f"{self.BASE}/device/control", json={
            "requestId": "va", "payload": {"sku": d["sku"], "device": device,
                                           "capability": {"type": f"devices.capabilities.{kind}", "instance": cmd, "value": data["value"]}}})
        r.raise_for_status()

    def apply(self, ips: list[str], on: bool | None, brightness: int | None, color: str | None) -> str:
        rgb, kelvin = parse_color(color) if color else (None, None)
        if color and rgb is None and kelvin is None:
            return f"Error: I don't know the colour '{color}'. Use a basic colour name, a hex code like #ff8800, 'warm white' or '3000K'."
        ok, failed = [], []
        for dev in ips:
            try:
                if on is not None or rgb or kelvin:
                    self.send(dev, "powerSwitch", {"value": 0 if on is False else 1})
                if rgb:
                    self.send(dev, "colorRgb", {"value": (rgb[0] << 16) + (rgb[1] << 8) + rgb[2]})
                elif kelvin:
                    self.send(dev, "colorTemperatureK", {"value": kelvin})
                if brightness is not None:
                    self.send(dev, "brightness", {"value": max(1, min(100, int(brightness)))})
                ok.append(self.label(dev))
            except httpx.HTTPError as e:
                failed.append(f"{self.label(dev)} ({e})")
        return ("Done: " + ", ".join(ok) + "." if ok else "") + ((" " if ok else "") + "Failed: " + "; ".join(failed) if failed else "")


def make_govee(cfg):
    """Cloud API when the user saved a Govee key, otherwise the local network."""
    from .providers import get_key

    key = get_key("govee")
    return GoveeCloud(key, cfg.light_names) if key else GoveeLan(cfg.light_names)


class HomeAssistant:
    def __init__(self, url: str, token: str, client: httpx.Client | None = None):
        self.base = url.rstrip("/")
        self.http = client or httpx.Client(timeout=10, headers={"Authorization": f"Bearer {token}"})

    def states(self) -> list[dict]:
        r = self.http.get(f"{self.base}/api/states")
        r.raise_for_status()
        return r.json()

    def list(self, domain: str = "", query: str = "", limit: int = 40) -> str:
        q = query.strip().lower()
        out = []
        for s in self.states():
            eid = s["entity_id"]
            name = s.get("attributes", {}).get("friendly_name", "")
            if domain and not eid.startswith(domain + "."):
                continue
            if domain == "" and eid.split(".")[0] in ("sun", "zone", "person", "persistent_notification", "update", "event", "tts", "stt"):
                continue
            if q and q not in eid.lower() and q not in name.lower():
                continue
            out.append(f"{eid} ({name}) = {s['state']}")
        return "\n".join(out[:limit]) + (f"\n...and {len(out) - limit} more; narrow it with a domain or query." if len(out) > limit else "") or "No matching entities."

    def call(self, domain: str, service: str, entity_id: str, data: dict | None = None) -> str:
        body = {"entity_id": entity_id, **(data or {})}
        r = self.http.post(f"{self.base}/api/services/{domain}/{service}", json=body)
        if r.status_code >= 400:
            return f"Error {r.status_code}: {r.text[:200]}"
        return f"Called {domain}.{service} on {entity_id}."


HOME_TOOL_NAMES = ("list_lights", "set_lights", "ha_list", "ha_call")


def build_home_tools(cfg, confirm, govee: GoveeLan | None = None, ha: HomeAssistant | None = None) -> list[Tool]:
    from .agent_tools import _obj, _str  # same schema helpers as the other agent tools
    from .providers import get_key

    tools: list[Tool] = []
    declined = "The user declined this action."
    if cfg.govee_enabled:
        govee = govee or make_govee(cfg)

        def list_lights() -> str:
            if not govee.devices:
                try:
                    govee.discover()
                except httpx.HTTPError as e:
                    return f"Could not reach Govee: {e}"
            return "\n".join(f"{govee.label(ip)} ({d.get('sku', '?')})" for ip, d in govee.devices.items()) or (
                "No Govee lights answered. In the Govee Home app open each light > settings and turn on LAN Control; "
                "the PC must be on the same Wi-Fi/network.")

        def set_lights(target: str = "all", on: bool | None = None, brightness: int | None = None, color: str | None = None) -> str:
            if not govee.devices:
                try:
                    govee.discover()
                except httpx.HTTPError as e:
                    return f"Could not reach Govee: {e}"
            ips = govee.pick(target)
            if not ips:
                return f"No light matches '{target}'. Known lights: " + (", ".join(govee.label(i) for i in govee.devices) or "none found.")
            return govee.apply(ips, on, brightness, color)

        tools += [
            Tool("list_lights", "List the Govee lights found (names the user can say).", _obj({}), list_lights),
            Tool("set_lights", "Turn Govee lights on/off, set brightness (1-100) and/or colour (name, #hex, 'warm white', '3000K'). target = a light name, or 'all' when the user does not name one. Call it straight away.",
                 _obj({"target": _str, "on": {"type": "boolean"}, "brightness": {"type": "integer"}, "color": _str}), set_lights),
        ]
    if cfg.ha_url:
        ha = ha or HomeAssistant(cfg.ha_url, get_key("homeassistant"))

        def ha_list(domain: str = "", query: str = "") -> str:
            try:
                return ha.list(domain, query)
            except Exception as e:
                return f"Error talking to Home Assistant: {e}"

        def ha_call(domain: str, service: str, entity_id: str, data: str = "") -> str:
            if domain in CONFIRM_DOMAINS and not confirm(f"Home Assistant: {domain}.{service} on {entity_id}?"):
                return declined
            try:
                return ha.call(domain, service, entity_id, json.loads(data) if data.strip() else None)
            except Exception as e:
                return f"Error talking to Home Assistant: {e}"

        tools += [
            Tool("ha_list", "List Home Assistant devices/entities (lights, switches, thermostats, speakers, Google Home / Alexa devices). Filter by domain such as 'light' and/or a name.",
                 _obj({"domain": _str, "query": _str}), ha_list),
            Tool("ha_call", "Call a Home Assistant service, e.g. domain 'light', service 'turn_on', entity_id 'light.desk', data '{\"brightness_pct\":40,\"color_name\":\"blue\"}'. Look the entity up with ha_list first.",
                 _obj({"domain": _str, "service": _str, "entity_id": _str, "data": _str}, ["domain", "service", "entity_id"]), ha_call),
        ]
    return tools
