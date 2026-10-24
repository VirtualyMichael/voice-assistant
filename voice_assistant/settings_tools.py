"""Agent tools for the assistant's own settings: explain a setting, say where it is, open it in the PC window, and change the safe ones.

Safety settings (anything marked sensitive in remote_settings) are never changed by the assistant, whoever asks: a web page or file it reads
could otherwise talk it into widening its own permissions. It explains them and shows where the user can change them. Every other change is
confirmed by the user first, exactly like the phone's own settings screen saves it.
"""
from __future__ import annotations

from typing import Callable

from .remote_settings import BY_KEY, PC_ONLY, STOP_WORDS, SettingError, SettingsService, phone_path, search_catalog, validate
from .tools import Tool

_obj = lambda props, req=(): {"type": "object", "properties": props, "required": list(req)}  # noqa: E731
_str = {"type": "string"}


def _value_text(s, value) -> str:
    if s.kind == "toggle":
        return "on" if value else "off"
    if s.kind == "choice":
        return next((label for v, label in s.choices if v == value), str(value) or "system default")
    if s.kind == "number":
        return f"{value:g}{(' ' + s.unit) if s.unit else ''}"
    if s.kind == "readonly":
        n = len(value) if isinstance(value, (list, tuple, dict)) else 0
        return f"{n} item{'s' if n != 1 else ''}"
    return f'"{value}"' if value else "empty"


def _limits(s) -> str:
    if s.kind == "toggle":
        return "true or false"
    if s.kind == "number":
        return f"a {'whole ' if s.integer else ''}number from {s.lo:g} to {s.hi:g}"
    if s.kind == "choice":
        return "one of: " + ", ".join(f"{v!r} ({label})" for v, label in s.choices) + (" or another short value" if s.allow_other else "") if s.choices else "a device name"
    if s.kind == "text":
        return f"text, at most {s.maxlen} characters"
    return ""


def build_settings_tools(service: SettingsService, confirm: Callable[[str], bool], pc_where: Callable[[str], list[str]] | None = None,
                         show_on_pc: Callable[[str], str] | None = None) -> list[Tool]:
    """pc_where(query) lists matching places in the PC window ("Page › Setting"); show_on_pc(query) opens the best one there."""
    cfg = service.cfg

    def find_setting(query: str, open_on_pc: bool = False) -> str:
        found = search_catalog(query)
        lines = []
        for s in found:
            value = getattr(cfg, s.key)
            if s.kind == "readonly":
                can = "It is a list edited in the PC window; you cannot change it."
            elif s.sensitive:
                can = "It is a safety setting: you must not change it; tell the user where to change it themself."
            else:
                can = f"You can change it with change_setting (key {s.key}, value {_limits(s)})."
            restart = " Takes effect after the assistant restarts." if s.apply == "restart" and s.kind != "readonly" else ""
            lines.append(f"- {s.label}: now {_value_text(s, value)}. {s.help} Phone app: {phone_path(s)}.{restart} {can}")
        short = " ".join(w for w in query.lower().split() if w not in STOP_WORDS) or query
        pc = pc_where(short) if pc_where else []
        if pc:
            lines.append("In the PC window (gear button): " + "; ".join(f"Settings › {p}" for p in pc[:4]) + ".")
        if open_on_pc and show_on_pc:
            lines.append(show_on_pc(short))
        if not lines:
            pc_only = [f"{k} ({why})" for k, why in PC_ONLY.items() if any(w in k for w in query.lower().split())]
            hint = (" PC-only settings with a similar name: " + "; ".join(pc_only[:4]) + ".") if pc_only else ""
            return f"No setting matches '{query}'. Try other words, or ask the user to search in Settings (the search box at the top).{hint}"
        return "\n".join(lines)

    def change_setting(key: str, value) -> str:
        s = BY_KEY.get(key)
        if s is None:
            return f"Error: there is no setting '{key}' you can change. Call find_setting first to get the key."
        if s.sensitive:
            return (f"Refused: '{s.label}' is a safety setting, so the assistant never changes it itself. "
                    f"Tell the user they can change it in the PC window's Settings, or on the phone at {phone_path(s)}.")
        if s.kind == "readonly":
            return f"'{s.label}' is a list edited in the PC window's Settings; tell the user to change it there."
        old = getattr(cfg, key)
        if isinstance(value, str) and s.kind == "number":
            try:
                value = float(value)
            except ValueError:
                pass
        if isinstance(value, str) and s.kind == "toggle" and value.lower() in ("true", "false", "on", "off"):
            value = value.lower() in ("true", "on")
        try:
            value = validate(s, value, service.device_names)
        except SettingError as e:
            return f"Not changed: {e} Allowed: {_limits(s)}."
        if value == old:
            return f"“{s.label}” is already {_value_text(s, old)}."
        if not confirm(f"Change the setting “{s.label}” from {_value_text(s, old)} to {_value_text(s, value)}?"):
            return "The user declined this change."
        result = service.change(key, value)
        if not result["ok"]:
            return f"Not changed: {result['message']}"
        when = "It takes effect when the assistant restarts." if result["restart"] else "It works now."
        return f"Changed “{s.label}” from {_value_text(s, old)} to {_value_text(s, result['value'])}. {when}"

    return [
        Tool("find_setting",
             "Look up the assistant's own settings by what the user said ('how do I make you talk slower', 'where is the wake word', 'what does follow-up do'). "
             "Returns each matching setting's current value, what it does, where it is in the phone app and the PC window, and whether you may change it. "
             "Set open_on_pc true when the user wants to see it on the PC: the settings page opens there with the setting highlighted.",
             _obj({"query": _str, "open_on_pc": {"type": "boolean"}}, ["query"]), find_setting),
        Tool("change_setting",
             "Change one of the assistant's own settings (not safety settings) by its key from find_setting. The user confirms first.",
             _obj({"key": _str, "value": {"description": "true or false, a number, or text, as find_setting says"}}, ["key", "value"]), change_setting),
    ]


SETTINGS_PROMPT = (
    " When the user asks about one of your own settings (what it does, where it is, how to change how you behave or sound), call find_setting first "
    "and answer from it in plain words, naming where it is. When they ask you to change one ('turn on ...', 'make it ...') and it is not a safety setting, "
    "call find_setting for the key and then change_setting straight away, without asking in words first: the user is asked to confirm by itself. "
    "Never try to change a safety setting, just say where they can change it."
)
