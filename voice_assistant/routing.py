"""Detects audio routes outside this app that would make you heard twice on the virtual cable.

Windows' own "Listen to this device" sends a microphone to a playback device from the audio service (svchost.exe), no
matter which mic this app is using. If it targets the same cable the app forwards your mic into, the game hears both.
"""
from __future__ import annotations

from dataclasses import dataclass

LISTEN_KEY = "{24dbb0fc-9311-4b3d-9cf0-18ff155639d4}"  # PKEY_Listen_* property set of an audio endpoint
NAME_KEY = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"
ADAPTER_KEY = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"
MMDEVICES = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio"


@dataclass(frozen=True)
class ListenRoute:
    mic: str
    target: str  # playback device name, or "" for the default playback device


def parse_listen_enabled(raw) -> bool:
    """A stored PROPVARIANT of type VT_BOOL (0x0B): 0xFFFF = true, 0 = false."""
    return isinstance(raw, (bytes, bytearray)) and len(raw) >= 10 and int.from_bytes(raw[:2], "little") == 0x0B and raw[8:10] != b"\x00\x00"


def _friendly(vals: dict) -> str:
    desc, adapter = vals.get(NAME_KEY), vals.get(ADAPTER_KEY)
    return f"{desc} ({adapter})" if desc and adapter else (desc or adapter or "")


def _read_endpoints(flow: str) -> dict[str, dict]:
    """{endpoint guid: {'state': int, 'props': {...}}} for 'Capture' or 'Render' (read-only registry access)."""
    import winreg

    out: dict[str, dict] = {}
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"{MMDEVICES}\{flow}") as root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            guid = winreg.EnumKey(root, i)
            try:
                with winreg.OpenKey(root, guid) as k:
                    state = winreg.QueryValueEx(k, "DeviceState")[0]
                with winreg.OpenKey(root, guid + r"\Properties") as p:
                    props = {winreg.EnumValue(p, j)[0]: winreg.EnumValue(p, j)[1] for j in range(winreg.QueryInfoKey(p)[1])}
            except OSError:
                continue
            out[guid.lower()] = {"state": state, "props": props}
    return out


def listen_routes(reader=_read_endpoints) -> list[ListenRoute]:
    """Every active microphone that Windows is currently set to 'Listen to this device' on."""
    try:
        captures, renders = reader("Capture"), reader("Render")
    except OSError:
        return []
    routes = []
    for info in captures.values():
        props = info["props"]
        if info["state"] != 1 or not parse_listen_enabled(props.get(f"{LISTEN_KEY},1")):
            continue
        target_ref = str(props.get(f"{LISTEN_KEY},0") or "")  # "{0.0.0.00000000}.{render endpoint guid}" or empty
        guid = target_ref.rsplit(".", 1)[-1].lower() if "{" in target_ref else ""
        target = _friendly(renders.get(guid, {}).get("props", {})) if guid else ""
        routes.append(ListenRoute(_friendly(props), target))
    return routes


def _same_device(a: str, b: str) -> bool:
    """Device names get truncated by some audio APIs, so compare by prefix."""
    a, b = a.strip().lower(), b.strip().lower()
    return bool(a and b) and (a.startswith(b) or b.startswith(a))


def double_voice_warnings(cfg, routes: list[ListenRoute]) -> list[str]:
    """Plain-language problems: Windows is feeding a mic into the cable that this app already forwards your mic into."""
    if not (cfg.mic_forward and cfg.output_device_2):
        return []
    out = []
    for r in routes:
        if _same_device(r.target, cfg.output_device_2):
            out.append(
                f"Windows is also sending your '{r.mic}' to '{r.target}' (Listen to this device), so the game hears you twice. "
                f"Turn it off: Windows Sound settings > Recording > {r.mic} > Properties > Listen > untick 'Listen to this device'."
            )
    return out
