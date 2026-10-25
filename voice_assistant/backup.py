"""Back up and restore the user's own data: saved chats, settings, remembered notes and the profile, as one zip file.

Left out on purpose: the list of paired phones (it holds their access tokens, so a restored PC asks each phone to pair again), downloaded models,
caches, logs and attachments. API keys are not in the settings file (they live in the system's credential store), so they are never in a backup.
"""
from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path

FILES = ("config.json", "notes.json", "profile.md", "ytm-library.json")
FOLDERS = ("chats",)
MARKER = "backup.json"


def make_backup(source: Path, dest: Path, version: str = "") -> dict:
    """Write the zip. Returns {"files": n, "chats": n}."""
    source, dest = Path(source), Path(dest)
    files = chats = 0
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(MARKER, json.dumps({"app": "voice-assistant", "version": version, "made": datetime.now().isoformat(timespec="seconds")}))
        for name in FILES:
            p = source / name
            if p.is_file():
                z.write(p, name)
                files += 1
        for folder in FOLDERS:
            for p in sorted((source / folder).glob("*.json")) if (source / folder).is_dir() else []:
                z.write(p, f"{folder}/{p.name}")
                chats += 1
    return {"files": files, "chats": chats}


def _safe(name: str) -> bool:
    parts = Path(name).parts
    if name == MARKER:
        return True
    if len(parts) == 1:
        return name in FILES
    return len(parts) == 2 and parts[0] in FOLDERS and parts[1].endswith(".json") and ".." not in parts and not Path(name).is_absolute()


def restore_backup(src: Path, target: Path) -> dict:
    """Put a backup back. Anything it would replace is first kept next to it as <name>.before-restore. Raises ValueError for something that is not a backup."""
    src, target = Path(src), Path(target)
    try:
        z = zipfile.ZipFile(src)
    except (zipfile.BadZipFile, OSError) as e:
        raise ValueError("That file is not a Voice Assistant backup.") from e
    with z:
        names = z.namelist()
        if MARKER not in names:
            raise ValueError("That file is not a Voice Assistant backup.")
        try:
            if json.loads(z.read(MARKER)).get("app") != "voice-assistant":
                raise ValueError("That file is not a Voice Assistant backup.")
        except json.JSONDecodeError as e:
            raise ValueError("That file is not a Voice Assistant backup.") from e
        restored = kept = 0
        for name in names:
            if name == MARKER or not _safe(name):  # anything unexpected in the zip is ignored, never written
                continue
            out = target / name
            out.parent.mkdir(parents=True, exist_ok=True)
            if out.exists():
                out.replace(out.with_name(out.name + ".before-restore"))
                kept += 1
            out.write_bytes(z.read(name))
            restored += 1
    return {"restored": restored, "kept": kept}
