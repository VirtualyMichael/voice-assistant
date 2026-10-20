"""Tool registry and the safe file tools (approved folders only; writes need confirmation)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from .permissions import FolderPolicy, PermissionDenied

Confirm = Callable[[str], bool]

MAX_READ_CHARS = 20_000
MAX_WRITE_CHARS = 200_000
MAX_LIST = 200


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Callable[..., str]


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self._tools = {t.name: t for t in tools}

    def add(self, tools: list[Tool]) -> None:
        self._tools.update({t.name: t for t in tools})

    def remove(self, names) -> None:
        for n in names:
            self._tools.pop(n, None)

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict]:
        return [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in self._tools.values()
        ]

    def call(self, name: str, arguments: str) -> str:
        tool = self._tools.get(name)
        if tool is None:
            return f"Error: unknown tool {name!r}."
        try:
            args = json.loads(arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
            return tool.handler(**args)
        except PermissionDenied as e:
            return f"Error: permission denied. {e}"
        except Exception as e:  # a tool failing must never crash the turn
            return f"Error: {e.__class__.__name__}: {e}"


def build_file_tools(policy: FolderPolicy, confirm: Confirm) -> ToolRegistry:
    def list_directory(path: str = ".") -> str:
        d = policy.resolve(path)
        if not d.is_dir():
            return f"Error: {path} is not a directory."
        entries = sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        lines = [f"{e.name}{'/' if e.is_dir() else ''}" for e in entries[:MAX_LIST]]
        more = f"\n... {len(entries) - MAX_LIST} more" if len(entries) > MAX_LIST else ""
        return "\n".join(lines) + more if lines else "(empty)"

    def read_text_file(path: str) -> str:
        f = policy.resolve(path)
        text = f.read_text(encoding="utf-8", errors="replace")
        if len(text) > MAX_READ_CHARS:
            return text[:MAX_READ_CHARS] + f"\n... truncated ({len(text)} chars total)"
        return text

    def write_text_file(path: str, content: str) -> str:
        f = policy.resolve(path)
        if len(content) > MAX_WRITE_CHARS:
            return "Error: content too large."
        verb = "Overwrite" if f.exists() else "Create"
        if not confirm(f"{verb} {f} ({len(content)} characters)?"):
            return "The user declined this action."
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} characters to {f.name}."

    obj = lambda props, req: {"type": "object", "properties": props, "required": req}  # noqa: E731
    s = {"type": "string"}
    return ToolRegistry(
        [
            Tool("list_directory", "List files in an approved folder.", obj({"path": s}, []), list_directory),
            Tool("read_text_file", "Read a text file in an approved folder.", obj({"path": s}, ["path"]), read_text_file),
            Tool(
                "write_text_file",
                "Create or overwrite a text file in an approved folder. The user is asked to confirm.",
                obj({"path": s, "content": s}, ["path", "content"]),
                write_text_file,
            ),
        ]
    )
