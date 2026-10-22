"""Which of an avatar's switches work backwards, read from its FX animator controller in the Unity project (no AI, no tokens).

A toggle is an animator layer: a transition goes to one state when the parameter is true and to another when it is false, and each state plays an animation clip.
If the clip played when the parameter is TRUE turns GameObjects off (a curve on `m_IsActive` ending at 0), turning the switch on hides the item: it works backwards.
Only that GameObject on/off evidence is used; colour, blend-shape and material switches say nothing here and are left alone."""
from __future__ import annotations

import os
import re
from pathlib import Path

MODE_IF, MODE_IFNOT = 1, 2
MAX_CONTROLLER = 40_000_000
MAX_CLIP = 8_000_000


def _docs(text: str) -> dict[str, tuple[int, str]]:
    """The YAML documents of a Unity file: {file id: (class id, body)}."""
    heads = list(re.finditer(r"^--- !u!(\d+) &(-?\d+)", text, re.M))
    out = {}
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        out[m.group(2)] = (int(m.group(1)), text[m.end():end])
    return out


def clip_index(folder: Path) -> dict[str, Path]:
    """Animation clips under a folder by asset guid (read from the .anim.meta files)."""
    out, seen = {}, 0
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d.lower() not in ("library", "temp", "obj", "logs", ".git")]
        for fn in filenames:
            if fn.endswith(".anim.meta"):
                seen += 1
                if seen > 40000:
                    return out
                try:
                    m = re.search(r"^guid:\s*([0-9a-f]{32})", (Path(dirpath) / fn).read_text(encoding="utf-8", errors="ignore"), re.M)
                except OSError:
                    continue
                if m:
                    out[m.group(1)] = Path(dirpath) / fn[:-5]
    return out


HIDE_SHAPE = re.compile(r"^(hide|hidden|remove|disable)", re.I)
SHOW_SHAPE = re.compile(r"^(show|enable|visible)", re.I)


def clip_active(path: Path) -> dict[str, bool]:
    """{what the clip shows or hides: is it shown}: GameObject on/off curves (`m_IsActive`) and blend shapes named like 'Hide Socks' (100 = hidden) or 'Show Wings' (100 = shown), judged by their last key.
    Placeholder objects (names starting with an underscore, like _Empty) and every other animation are ignored."""
    try:
        if path.stat().st_size > MAX_CLIP:
            return {}
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}
    out: dict[str, bool] = {}
    for m in re.finditer(r"attribute: (m_IsActive|blendShape\.[^\n]+)\n\s+path: (.*)\n", text):
        start = text.rfind("- curve:", 0, m.start())  # the curve's own keys come before its attribute line
        keys = re.findall(r"value: (-?[\d.]+)", text[start:m.start()] if start >= 0 else "")
        where = m.group(2).strip()
        if not keys or where.rsplit("/", 1)[-1].startswith("_"):
            continue
        last = float(keys[-1])
        if m.group(1) == "m_IsActive":
            out[where] = last >= 0.5
        else:
            shape = m.group(1)[len("blendShape."):].strip()
            if HIDE_SHAPE.match(shape):
                out[f"{where}: {shape}"] = last < 50
            elif SHOW_SHAPE.match(shape):
                out[f"{where}: {shape}"] = last >= 50
    return out


def analyze_controller(path: Path, clips: dict[str, Path], params: set[str] | None = None, stats: dict | None = None) -> dict[str, dict]:
    """{parameter: {'inverted': True|False|None, 'on': {...}, 'off': {...}}} for the bool parameters this controller switches things with.
    'on' / 'off' are the GameObjects (path -> shown) the clips reached when the parameter is true / false set."""
    try:
        if path.stat().st_size > MAX_CONTROLLER:
            return {}
        docs = _docs(path.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return {}
    states = {}
    for fid, (cls, body) in docs.items():
        if cls == 1102:  # AnimatorState
            g = re.search(r"m_Motion: \{fileID: \d+, guid: ([0-9a-f]{32})", body)
            n = re.search(r"m_Name: (.*)", body)
            states[fid] = {"name": (n.group(1).strip() if n else ""), "clip": clips.get(g.group(1)) if g else None, "guid": g.group(1) if g else ""}
    by_param: dict[str, dict[str, set]] = {}
    for fid, (cls, body) in docs.items():
        if cls != 1101:  # AnimatorStateTransition
            continue
        dst = re.search(r"m_DstState: \{fileID: (-?\d+)\}", body)
        conds = re.findall(r"m_ConditionMode: (\d+)\n\s+m_ConditionEvent: (.*)\n", body)
        if not dst or dst.group(1) not in states:
            continue
        for mode, name in conds:
            name = name.strip()
            if params is not None and name not in params:
                continue
            if int(mode) in (MODE_IF, MODE_IFNOT):
                by_param.setdefault(name, {"on": set(), "off": set()})["on" if int(mode) == MODE_IF else "off"].add(dst.group(1))
    result = {}
    for name, sides in by_param.items():
        shown: dict[str, dict[str, bool]] = {"on": {}, "off": {}}
        for side, ids in sides.items():
            for sid in ids:
                clip = states[sid]["clip"]
                if stats is not None and states[sid]["guid"]:
                    stats.setdefault("used", set()).add(states[sid]["guid"])
                    if not clip:
                        stats.setdefault("missing", set()).add(states[sid]["guid"])
                if clip:
                    for obj, active in clip_active(clip).items():
                        shown[side][obj] = (shown[side][obj] and active) if obj in shown[side] else active
        result[name] = {"inverted": _verdict(shown["on"], shown["off"]), "on": shown["on"], "off": shown["off"]}
    return result


def _verdict(on: dict[str, bool], off: dict[str, bool]) -> bool | None:
    """True = turning the switch ON hides things (backwards), False = ON shows them, None = this controller cannot say."""
    if on:
        values = set(on.values())
        if values == {False}:
            return True
        if values == {True}:
            return False
        return None
    if off:
        values = set(off.values())
        if values == {True}:
            return True  # the OFF state is the one that shows the item
        if values == {False}:
            return False
    return None


def project_root(folder: Path) -> Path:
    """The Unity project a folder belongs to (the one holding Assets and ProjectSettings), else the folder itself."""
    for p in [folder, *list(folder.parents)[:8]]:
        if (p / "Assets").is_dir() and (p / "ProjectSettings").is_dir():
            return p
    return folder


def _descendant(p: Path, of: Path) -> bool:
    try:
        p.relative_to(of)
        return p != of
    except ValueError:
        return False


def analyze_roots(roots: list[Path], params: set[str]) -> tuple[dict[str, dict], str]:
    """Backwards-switch evidence from the avatar's FX controller found in what was given (folders and/or files, e.g. what the user attached): ({parameter: {'inverted': bool, 'why': str}}, a note about what was missing).
    Only FX controllers count (the only layer that can show and hide objects); when several are given, the one that uses the most of this avatar's parameters is read.
    Animation clips are looked up in the whole Unity project each item sits in, so attaching only the Controllers folder is enough as long as it is still where Unity keeps it."""
    roots = [Path(r) for r in roots if Path(r).exists()]
    bases: list[Path] = []
    for r in roots:
        d = r if r.is_dir() else r.parent
        pr = project_root(d)
        bases += [(pr / "Assets") if (pr / "Assets").is_dir() else d, d]
    bases = [b for b in dict.fromkeys(bases) if not any(_descendant(b, o) for o in bases)]
    clips: dict[str, Path] = {}
    for b in bases:
        clips.update(clip_index(b))
    controllers: list[Path] = []
    for r in roots:
        if r.is_file():
            if r.suffix.lower() == ".controller" and "fx" in r.name.lower():
                controllers.append(r)
            continue
        for dirpath, dirnames, filenames in os.walk(r):
            dirnames[:] = [d for d in dirnames if d.lower() not in ("library", "temp", "obj", "logs", ".git")]
            controllers += [Path(dirpath) / fn for fn in filenames if fn.endswith(".controller") and "fx" in fn.lower()]
    if not controllers:
        return {}, "No FX controller (a file named like FX.controller) was among them."
    best = None
    for c in dict.fromkeys(controllers):
        stats: dict = {}
        found = {n: i for n, i in analyze_controller(c, clips, params, stats).items() if i["inverted"] is not None}
        if best is None or len(found) > best[0]:
            best = (len(found), c.name, found, stats)
    count, name, found, stats = best
    note = ""
    used, missing = len(stats.get("used", ())), len(stats.get("missing", ()))
    if missing:
        note = (f"{missing} of the {used} animation clips {name} uses were not found, so those switches are unknown. Attach the folder that holds the .anim files "
                "(for example Assets/<avatar>/Animations) or the whole avatar folder, with the .anim.meta files, and import again.")
    elif not count:
        note = f"{name} was read, but none of its animations hide or show things for this avatar's switches."
    out = {}
    for pname, info in found.items():
        objs = info["on"] or info["off"]
        names = ", ".join(sorted({(o.split(": ", 1)[1] if ": " in o else o.rsplit("/", 1)[-1]) for o in objs})[:3])
        out[pname] = {"inverted": info["inverted"], "why": (f"ON {'hides' if info['inverted'] else 'shows'} {names}" if info["on"] else f"OFF {'shows' if info['inverted'] else 'hides'} {names}") + f" (read from {name})"}
    return out, note


def analyze_project(folder: Path, params: set[str]) -> dict[str, dict]:
    """analyze_roots for one folder, without the note."""
    return analyze_roots([folder], params)[0]
