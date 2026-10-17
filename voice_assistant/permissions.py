"""Approved-folder policy for task tools. Anything outside is rejected, not prompted."""
from __future__ import annotations

from pathlib import Path


class PermissionDenied(Exception):
    pass


class FolderPolicy:
    def __init__(self, folders: list[str | Path] | None = None):
        self._roots: list[Path] = []
        for f in folders or []:
            self.add(f)

    @property
    def roots(self) -> list[Path]:
        return list(self._roots)

    def add(self, folder: str | Path) -> None:
        root = Path(folder).expanduser().resolve()
        if root not in self._roots:
            self._roots.append(root)

    def remove(self, folder: str | Path) -> None:
        root = Path(folder).expanduser().resolve()
        self._roots = [r for r in self._roots if r != root]

    def resolve(self, path: str) -> Path:
        """Resolve `path` (relative paths are taken against the first approved folder)."""
        if not self._roots:
            raise PermissionDenied("No approved folders. Add one in the app first.")
        p = Path(path).expanduser()
        # Reject NTFS alternate data streams ("file.txt:stream") and similar colon tricks.
        if any(":" in part for part in p.parts[1:] if part != p.anchor) or (not p.is_absolute() and ":" in str(p)):
            raise PermissionDenied(f"Path not allowed: {path}")
        if not p.is_absolute():
            p = self._roots[0] / p
        resolved = p.resolve()  # follows symlinks/junctions and removes ".."
        if not any(resolved == r or resolved.is_relative_to(r) for r in self._roots):
            raise PermissionDenied(f"{path} is outside the approved folders.")
        return resolved
