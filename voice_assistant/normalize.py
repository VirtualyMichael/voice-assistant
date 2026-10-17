"""Turns written text into text a speech model reads correctly (numbers, symbols, abbreviations, jargon)."""
from __future__ import annotations

import re

from num2words import num2words

# Words the phonemizer tends to get wrong. Case-sensitive whole-word replacements; users can extend via config.
DEFAULT_PRONUNCIATIONS = {
    "VRChat": "V R Chat",
    "SteamVR": "Steam V R",
    "VR": "V R",
    "LLM": "L L M",
    "GPU": "G P U",
    "CPU": "C P U",
    "AI": "A I",
    "USB": "U S B",
    "API": "A P I",
    "TTS": "T T S",
    "OK": "okay",
    "Wi-Fi": "Wifi",
}

_ABBREV = [
    (r"\bMr\.", "Mister"), (r"\bMrs\.", "Missus"), (r"\bMs\.", "Miss"), (r"\bDr\.", "Doctor"),
    (r"\bvs\.?(?=\s)", "versus"), (r"\be\.g\.", "for example"), (r"\bi\.e\.", "that is"),
    (r"\betc\.", "et cetera"), (r"\bapprox\.", "approximately"),
]
_UNITS = {
    "GB": "gigabytes", "MB": "megabytes", "KB": "kilobytes", "TB": "terabytes", "GHz": "gigahertz",
    "MHz": "megahertz", "ms": "milliseconds", "km": "kilometers", "kg": "kilograms", "cm": "centimeters",
    "mm": "millimeters", "mph": "miles per hour", "km/h": "kilometers per hour", "fps": "frames per second",
}
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")
_URL = re.compile(r"https?://\S+|www\.\S+")


def _words(n: float | int) -> str:
    return num2words(n).replace(" and ", " ").replace("-", " ").replace(",", "")


def _num(token: str) -> str:
    token = token.replace(",", "")
    try:
        if "." in token:
            whole, frac = token.split(".", 1)
            return f"{_words(int(whole or 0))} point " + " ".join(_words(int(d)) for d in frac)
        if len(token) > 15:
            return " ".join(token)
        return _words(int(token))
    except ValueError:
        return token


def _time(m: re.Match) -> str:
    h, mi = int(m.group(1)), int(m.group(2))
    ap = (m.group(3) or "").lower() if m.re.groups >= 3 else ""
    if h > 24 or mi > 59:
        return m.group(0)
    parts = [_words(h)]
    if mi:
        parts.append(("oh " if mi < 10 else "") + _words(mi))
    elif not ap:
        parts.append("o'clock")
    if ap:
        parts.append(f"{ap} m")
    return " ".join(parts)


def _money(m: re.Match) -> str:
    amount = m.group(1).replace(",", "")
    dollars, _, cents = amount.partition(".")
    out = f"{_num(dollars)} dollar" + ("" if dollars == "1" else "s")
    if cents and int(cents[:2].ljust(2, "0")):
        c = int(cents[:2].ljust(2, "0"))
        out += f" and {_num(str(c))} cent" + ("" if c == 1 else "s")
    return out


_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def _iso_date(m: re.Match) -> str:
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return m.group(0)
    day = num2words(d, to="ordinal").replace("-", " ")
    return f"{_MONTHS[mo - 1]} {day}, {num2words(y, to='year').replace('-', ' ').replace(',', '')}"


def normalize_for_speech(text: str, pronunciations: dict[str, str] | None = None) -> str:
    text = _URL.sub("a link", text)
    text = _EMOJI.sub("", text)
    for word, said in {**DEFAULT_PRONUNCIATIONS, **(pronunciations or {})}.items():
        text = re.sub(rf"(?<![\w-]){re.escape(word)}(?![\w-])", said, text)
    for pat, rep in _ABBREV:
        text = re.sub(pat, rep, text)
    text = re.sub(r"\$(\d[\d,]*(?:\.\d+)?)", _money, text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s?%", lambda m: f"{_num(m.group(1))} percent", text)
    text = re.sub(r"(-?\d+(?:\.\d+)?)\s?°\s?([CF])\b", lambda m: f"{_num(m.group(1))} degrees {'Celsius' if m.group(2) == 'C' else 'Fahrenheit'}", text)
    text = re.sub(r"\b(\d{1,2}):(\d{2})\s?([AaPp])\.?[Mm]\.?", _time, text)
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", _time, text)
    text = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", _iso_date, text)
    text = re.sub(r"(?<=\d)\s?[-–]\s?(?=\d)", " to ", text)  # ranges: 3-5 -> 3 to 5
    text = re.sub(r"\b(\d+)(st|nd|rd|th)\b", lambda m: num2words(int(m.group(1)), to="ordinal").replace("-", " ").replace(",", ""), text)
    text = re.sub(r"\b(1[1-9]\d\d|20\d\d)\b(?![.,]\d)", lambda m: num2words(int(m.group(1)), to="year").replace("-", " ").replace(",", ""), text)
    text = re.sub(r"(?<=\d)x\b", " times", text)
    for unit in sorted(_UNITS, key=len, reverse=True):
        text = re.sub(rf"(\d)\s?{re.escape(unit)}\b", lambda m, u=unit: f"{m.group(1)} {_UNITS[u]}", text)
    text = re.sub(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?", lambda m: ("minus " if m.group(0).startswith("-") else "") + _num(m.group(0).lstrip("-")), text)
    text = text.replace("&", " and ").replace("+", " plus ").replace("=", " equals ")
    text = re.sub(r"(?<=\w)/(?=\w)", " slash ", text)
    return re.sub(r"\s+", " ", text).strip()
