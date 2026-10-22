"""What an avatar's menu calls things, which VRChat's OSC never says.

OSC and OSCQuery give only the raw parameter names ('wolfToggle'), never the menu labels you see in the Action Menu ('Animal Parts'). Two ways to bridge that are kept here, per avatar:
  - nicknames you teach (by voice, or by the guided 'learn this avatar' pass): 'Animal Parts' means wolfToggle + tailSway
  - the avatar's own menu, read from the Unity project it was built in (VRCExpressionsMenu assets are plain text there), for avatars you made
Both end up as the same thing: entries {'label': 'Accessories/Animal Parts', 'params': [...], 'values': {...}, 'kind': ..., 'source': 'taught'|'unity'}."""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

SKIP_FOLDERS = {"library", "temp", "obj", "logs", "builds", "build", ".git", "node_modules"}
MENU_TOGGLE, MENU_BUTTON, MENU_SUB, MENU_RADIAL = 102, 101, 103, 203
FILLER = {"all", "the", "my", "every", "of", "a", "an", "and", "to", "in", "on", "please", "toggle", "toggles", "switch", "switches", "folder", "menu"}
MAX_FILE = 1_500_000


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _words(text: str) -> list[str]:
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return [_stem(w) for w in re.sub(r"[\W_]+", " ", text).lower().split()]


class AvatarNotes:
    """One small JSON file per avatar in a folder, so each can be opened and edited by hand: {'id', 'name', 'entries': [...], 'switches': {...}, 'imported_from'}.
    The avatar id VRChat reports is the key (it matches the blueprint id shown in Unity's Pipeline Manager); the name in the file name is only for reading.
    The files are read fresh every time, so a hand edit applies at once; a file that no longer parses is never overwritten (it is copied to .broken first)."""

    def __init__(self, folder: Path, legacy_file: Path | None = None):
        self.folder = Path(folder)
        self._lock = threading.Lock()
        if legacy_file is not None:
            self._migrate(Path(legacy_file))

    @staticmethod
    def _safe(name: str) -> str:
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", name).strip(" .")[:60] or "avatar"

    def path_for(self, avatar_id: str) -> Path | None:
        """The file already kept for this avatar id, or None."""
        try:
            for f in sorted(self.folder.glob("*.json")):
                if f"[{avatar_id}]" in f.name:
                    return f
        except OSError:
            pass
        return None

    def files(self) -> list[tuple[str, Path]]:
        """(avatar name, file) for every avatar with notes."""
        out = []
        try:
            for f in sorted(self.folder.glob("*.json")):
                try:
                    out.append((str(json.loads(f.read_text(encoding="utf-8")).get("name") or f.stem), f))
                except (OSError, ValueError):
                    out.append((f.stem + " (not valid JSON)", f))
        except OSError:
            pass
        return out

    def _migrate(self, legacy: Path) -> None:
        """Older versions kept every avatar in one file: split it into one file per avatar, once."""
        if not legacy.exists():
            return
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict):
            for aid, rec in data.items():
                if isinstance(rec, dict) and self.path_for(aid) is None:
                    self._write(aid, rec)
        try:
            legacy.rename(legacy.with_suffix(".json.migrated"))
        except OSError:
            pass

    def _write(self, avatar_id: str, rec: dict) -> None:
        path = self.path_for(avatar_id) or self.folder / f"{self._safe(rec.get('name') or 'avatar')} [{avatar_id}].json"
        self.folder.mkdir(parents=True, exist_ok=True)
        rec = {"id": avatar_id, **{k: v for k, v in rec.items() if k != "id"}}
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)

    def get(self, avatar_id: str) -> dict:
        p = self.path_for(avatar_id)
        if p is not None:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (OSError, ValueError):
                pass
        return {"name": "", "entries": []}

    def entries(self, avatar_id: str) -> list[dict]:
        return [e for e in (self.get(avatar_id).get("entries") or []) if isinstance(e, dict) and e.get("label") and isinstance(e.get("params"), list)]

    def update(self, avatar_id: str, name: str, fn) -> dict:
        """fn(record) edits the avatar's record in place; written through a temporary file and a rename."""
        with self._lock:
            p = self.path_for(avatar_id)
            if p is not None:
                try:
                    json.loads(p.read_text(encoding="utf-8"))
                except ValueError:  # edited by hand into something that is not JSON: keep it, do not overwrite it
                    p.with_suffix(".broken").write_bytes(p.read_bytes())
            rec = self.get(avatar_id)
            rec["name"] = name or rec.get("name", "")
            rec.setdefault("entries", [])
            fn(rec)
            self._write(avatar_id, rec)
            return rec

    def remember(self, avatar_id: str, name: str, label: str, params: list[str], source: str = "taught") -> None:
        def edit(rec):
            rec["entries"] = [e for e in rec["entries"] if not (e["label"].lower() == label.lower() and e.get("source") == source)]
            rec["entries"].insert(0, {"label": label, "params": params, "values": {}, "kind": "taught" if source == "taught" else "menu", "source": source})
        self.update(avatar_id, name, edit)

    def forget(self, avatar_id: str, label: str) -> int:
        removed = [0]

        def edit(rec):
            keep = [e for e in rec["entries"] if e["label"].lower() != label.lower() or e.get("source") != "taught"]
            removed[0] = len(rec["entries"]) - len(keep)
            rec["entries"] = keep
        self.update(avatar_id, "", edit)
        return removed[0]

    def switch(self, avatar_id: str) -> dict:
        """How individual switches behave on this avatar: {param: {'inverted': bool, 'note': str, 'source': 'taught'|'unity'}}."""
        sw = self.get(avatar_id).get("switches")
        return {k: v for k, v in sw.items() if isinstance(v, dict)} if isinstance(sw, dict) else {}

    def note_switches(self, avatar_id: str, name: str, params: list[str], inverted: bool, note: str, source: str = "taught") -> None:
        def edit(rec):
            sw = rec.setdefault("switches", {})
            for p in params:
                sw[p] = {"inverted": bool(inverted), "note": note, "source": source}
        self.update(avatar_id, name, edit)

    def replace_imported(self, avatar_id: str, name: str, entries: list[dict], source_folder: str, polarity: dict[str, dict] | None = None) -> list[str]:
        """Store a Unity import: its menu entries, and which switches its animations say work backwards. Things the user taught are never overwritten;
        returns the switches where the Unity files disagree with what was taught."""
        conflicts: list[str] = []

        def edit(rec):
            rec["entries"] = [e for e in rec["entries"] if e.get("source") != "unity"] + entries
            rec["imported_from"] = source_folder
            sw = rec.setdefault("switches", {})
            for p, info in (polarity or {}).items():
                old = sw.get(p)
                if old and old.get("source") != "unity" and bool(old.get("inverted")) != bool(info["inverted"]):
                    conflicts.append(f"{p}: you taught {'backwards' if old.get('inverted') else 'normal'}, Unity says {'backwards' if info['inverted'] else 'normal'} ({info['why']})")
                    continue
                if old and old.get("source") != "unity":
                    continue
                sw[p] = {"inverted": bool(info["inverted"]), "note": info["why"], "source": "unity"}
        self.update(avatar_id, name, edit)
        return conflicts


EXPORT_KINDS = {"toggle", "button", "folder", "radial"}


def load_export(path: Path) -> dict | None:
    """An avatar export written by the Unity exporter (unity/VoiceAssistantExport), checked and cleaned: {'name', 'id', 'entries', 'polarity', 'path'}, or None when it is not one."""
    try:
        if Path(path).stat().st_size > 20_000_000:
            return None
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("format") != "voice-assistant-avatar" or not isinstance(data.get("menu", []), list):
        return None
    avatar = data.get("avatar") if isinstance(data.get("avatar"), dict) else {}
    entries = []
    for e in data.get("menu") or []:
        if not isinstance(e, dict) or not isinstance(e.get("label"), str) or not isinstance(e.get("params"), list):
            continue
        values = {k: float(v) for k, v in (e.get("values") or {}).items() if isinstance(v, (int, float)) and not isinstance(v, bool)} if isinstance(e.get("values"), dict) else {}
        kind = e.get("kind") if e.get("kind") in EXPORT_KINDS else "toggle"
        entries.append({"label": e["label"], "params": [p for p in e["params"] if isinstance(p, str)], "values": values, "kind": kind, "source": "unity"})
    polarity = {k: {"inverted": bool(v.get("inverted")), "why": str(v.get("why") or "from the Unity export")[:160]}
                for k, v in (data.get("switches") or {}).items() if isinstance(v, dict) and "inverted" in v} if isinstance(data.get("switches"), dict) else {}
    return {"name": str(avatar.get("name") or ""), "id": str(avatar.get("id") or ""), "entries": entries, "polarity": polarity, "path": str(path),
            "parameters": [p.get("name") for p in data.get("parameters") or [] if isinstance(p, dict)]}


def match_entries(entries: list[dict], query: str) -> tuple[dict | None, list[str]]:
    """(the entry a spoken name clearly means, the labels it could be when it is unclear). Spoken 'animal parts' fits the label 'Accessories/Animal Parts'; a folder wins over the items inside it."""
    want = [w for w in _words(query) if w not in FILLER]
    if not want or not entries:
        return None, []

    def stems(e):
        return _words(e["label"])

    def leaf(e):
        return _words(e["label"].rsplit("/", 1)[-1])

    def pick(cands: list[dict]):
        labels = [c["label"] for c in cands]
        top = [c for c in cands if not any(c["label"].startswith(o + "/") for o in labels if o != c["label"])]  # the folder, not its contents
        distinct = {}
        for c in top:  # a taught nickname beats an imported entry with the same label, and the same switches under two labels count once
            key = c["label"].lower() if any(o is not c and o["label"].lower() == c["label"].lower() for o in top) else tuple(sorted(c["params"]))
            if key not in distinct or (c.get("source") == "taught" and distinct[key].get("source") != "taught"):
                distinct[key] = c
        if len(distinct) == 1:
            return next(iter(distinct.values())), []
        return None, [c["label"] for c in distinct.values()][:8]

    for tier in (lambda e: stems(e) == want or leaf(e) == want, lambda e: set(want) <= set(stems(e))):
        cands = [e for e in entries if e.get("params") and tier(e)]
        if cands:
            return pick(cands)
    return None, []


# ---- reading VRCExpressionsMenu assets from a Unity project ------------------------------------

def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        try:
            return json.loads(s)
        except ValueError:
            return s[1:-1]
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1].replace("''", "'")
    return s


def parse_menu(text: str) -> list[dict] | None:
    """The controls of one VRCExpressionsMenu asset (Unity YAML) as [{'name', 'type', 'param', 'value', 'sub'}], or None when the text is not a menu."""
    lines = text.splitlines()
    start = next((i for i, l in enumerate(lines) if re.match(r"^\s*controls:\s*$", l)), None)
    if start is None:
        return None
    base = len(lines[start]) - len(lines[start].lstrip())
    items: list[list[str]] = []
    for l in lines[start + 1:]:
        if not l.strip():
            continue
        ind = len(l) - len(l.lstrip())
        s = l.strip()
        if ind < base or (ind == base and not s.startswith("-")):
            break
        if ind == base and s.startswith("- name:"):
            items.append([l])
        elif items:
            items[-1].append(l)
    controls = []
    for item in items:
        c = {"name": _unquote(item[0].split("name:", 1)[1]), "type": 0, "param": "", "value": 1.0, "sub": ""}
        for k, l in enumerate(item[1:], 1):
            ind, s = len(l) - len(l.lstrip()), l.strip()
            if ind != base + 2:
                continue
            if s.startswith("type:"):
                c["type"] = int(re.sub(r"\D", "", s.split(":", 1)[1]) or 0)
            elif s.startswith("value:"):
                try:
                    c["value"] = float(s.split(":", 1)[1])
                except ValueError:
                    pass
            elif s.startswith("subMenu:"):
                m = re.search(r"guid:\s*([0-9a-f]{32})", s)
                c["sub"] = m.group(1) if m else ""
            elif s == "parameter:" and k + 1 < len(item):
                nxt = item[k + 1]
                if nxt.strip().startswith("name:") and len(nxt) - len(nxt.lstrip()) == base + 4:
                    c["param"] = _unquote(nxt.strip()[5:])
        controls.append(c)
    return controls


def _guid_of(asset: Path) -> str:
    try:
        m = re.search(r"^guid:\s*([0-9a-f]{32})", asset.with_name(asset.name + ".meta").read_text(encoding="utf-8", errors="ignore"), re.M)
        return m.group(1) if m else ""
    except OSError:
        return ""


def find_menus(folder: Path) -> dict[str, tuple[Path, list[dict]]]:
    """Every VRCExpressionsMenu asset under a Unity project folder, by asset guid."""
    menus: dict[str, tuple[Path, list[dict]]] = {}
    seen = 0
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_FOLDERS]
        for fn in filenames:
            if not fn.endswith(".asset"):
                continue
            seen += 1
            if seen > 20000:  # a whole disk was dropped, not a project
                return menus
            p = Path(dirpath) / fn
            try:
                if p.stat().st_size > MAX_FILE:
                    continue
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "controls:" not in text or "MonoBehaviour" not in text:
                continue
            controls = parse_menu(text)
            guid = _guid_of(p)
            if controls and guid:
                menus[guid] = (p, controls)
    return menus


def flatten(menus: dict, root: str, depth: int = 0, seen: tuple = (), prefix: str = "") -> list[dict]:
    """The menu tree from one root as entries: a toggle or button is {'label': 'Folder/Name', 'params': [parameter]}; a sub-menu also becomes an entry holding every toggle beneath it."""
    out: list[dict] = []
    if root not in menus or root in seen or depth > 8:
        return out
    for c in menus[root][1]:
        path = f"{prefix}{c['name']}" if c["name"] else prefix.rstrip("/")
        if c["type"] == MENU_SUB and c["sub"]:
            inner = flatten(menus, c["sub"], depth + 1, seen + (root,), path + "/")
            params, values, seen_values = [], {}, {}
            for e in inner:
                if e["kind"] in ("toggle", "button"):
                    for q in e["params"]:
                        if q not in params:
                            params.append(q)
                        values.update(e["values"])
                        seen_values.setdefault(q, set()).add(e["values"].get(q))
            choosers = {q for q, v in seen_values.items() if len(v) > 1}  # a parameter several items pick a number for (hair colour 0-4) is a choice, not something to switch with the folder
            params = [q for q in params if q not in choosers]
            values = {q: v for q, v in values.items() if q not in choosers}
            out.append({"label": path, "params": params, "values": values, "kind": "folder", "source": "unity"})
            out += inner
        elif c["type"] in (MENU_TOGGLE, MENU_BUTTON) and c["param"]:
            out.append({"label": path, "params": [c["param"]], "values": {c["param"]: c["value"]}, "kind": "toggle" if c["type"] == MENU_TOGGLE else "button", "source": "unity"})
        elif c["type"] == MENU_RADIAL and c["param"]:
            out.append({"label": path, "params": [c["param"]], "values": {}, "kind": "radial", "source": "unity"})
    return out


def menus_in(roots) -> dict[str, tuple[Path, list[dict]]]:
    """Menus from folders (searched whole) and single .asset files, together, so a sub-menu in one place is found from a menu in another."""
    menus: dict[str, tuple[Path, list[dict]]] = {}
    for r in roots:
        r = Path(r)
        if r.is_dir():
            menus.update(find_menus(r))
        elif r.suffix.lower() == ".asset":
            try:
                controls = parse_menu(r.read_text(encoding="utf-8", errors="ignore")) if r.stat().st_size <= MAX_FILE else None
            except OSError:
                continue
            guid = _guid_of(r)
            if controls and guid:
                menus[guid] = (r, controls)
    return menus


def import_project(folder, live_params: set[str]) -> tuple[list[dict], str]:
    """(entries, a note) from the menu among a Unity project folder, or several folders and files, that fits the worn avatar best (most of its parameters are ones the avatar really has). Raises ValueError with a reason."""
    roots = [Path(folder)] if isinstance(folder, (str, Path)) else [Path(r) for r in folder]
    if not roots or not any(r.exists() for r in roots):
        raise ValueError(f"'{roots[0] if roots else ''}' does not exist.")
    menus = menus_in(roots)
    if not menus:
        raise ValueError("No avatar menu (VRCExpressionsMenu .asset files, with their .meta files) found. Give the avatar's folder or the Unity project folder (the one that holds 'Assets').")
    referenced = {c["sub"] for _, (_, controls) in menus.items() for c in controls if c["sub"]}
    roots = [g for g in menus if g not in referenced] or list(menus)
    scored = []
    for g in roots:
        entries = flatten(menus, g)
        used = {q for e in entries for q in e["params"]}
        scored.append((len(used & live_params), len(entries), g, entries))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    best = scored[0]
    if best[0] == 0:
        raise ValueError(f"Found {len(menus)} menu file(s) there, but none uses a parameter the worn avatar has. Is that the project this avatar was built from?")
    return best[3], f"menu '{menus[best[2]][0].stem}' from {menus[best[2]][0].parent}: {len(best[3])} entries, {best[0]} of the avatar's switches"
