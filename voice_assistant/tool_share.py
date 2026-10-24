"""Share this app's tools with Odysseus (or any MCP client) through tools_proxy.py.

A tiny HTTP server on 127.0.0.1 (random port) runs the registry's tools on request. Callers must send the token from
tool-server.json (rewritten at every start), the Host header must be local, and calls run one at a time, so every
consent prompt the tools raise appears in this app's window exactly as if the assistant had asked itself.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from .tools import ToolRegistry

MAX_BODY = 1_000_000
MAX_RESULT_CHARS = 50_000
CALL_TIMEOUT_S = 640.0
SERVER_FILE = "tool-server.json"
TOOLS_FILE = "tools-shared.json"


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


class ToolShare:
    def __init__(self, registry: ToolRegistry, directory: Path, exclude: tuple[str, ...] = (), init_com: Callable[[], None] | None = None):
        self.registry, self.dir, self.exclude = registry, directory, set(exclude)
        self.token = secrets.token_urlsafe(32)
        self.port = 0
        self._httpd: ThreadingHTTPServer | None = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="shared-tool", initializer=init_com)

    def tools(self) -> list[dict]:
        return [
            {"name": t.name, "description": t.description, "inputSchema": t.parameters}
            for t in self.registry.tool_list() if t.name not in self.exclude
        ]

    def call(self, name: str, arguments: dict) -> tuple[str, bool]:
        if name in self.exclude or name not in self.registry.names():
            return f"Error: unknown tool {name!r}.", True
        text = self._pool.submit(self.registry.call, name, json.dumps(arguments)).result(CALL_TIMEOUT_S)
        return str(text)[:MAX_RESULT_CHARS], str(text).startswith("Error:")

    def start(self) -> int:
        share = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # keep quiet
                pass

            def _send(self, code: int, body: dict) -> None:
                data = json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _allowed(self) -> bool:
                host = (self.headers.get("Host") or "").lower()
                if host not in (f"127.0.0.1:{share.port}", f"localhost:{share.port}"):
                    return False  # DNS-rebinding guard
                return hmac.compare_digest(self.headers.get("X-VA-Token") or "", share.token)

            def do_GET(self):
                if not self._allowed():
                    return self._send(403, {"error": "forbidden"})
                if self.path == "/tools":
                    return self._send(200, {"tools": share.tools()})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                if not self._allowed():
                    return self._send(403, {"error": "forbidden"})
                if self.path != "/call":
                    return self._send(404, {"error": "not found"})
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    if not 0 < n <= MAX_BODY:
                        return self._send(413, {"error": "bad size"})
                    req = json.loads(self.rfile.read(n))
                    args = req.get("arguments") or {}
                    if not isinstance(req.get("name"), str) or not isinstance(args, dict):
                        return self._send(400, {"error": "bad request"})
                    result, err = share.call(req["name"], args)
                except Exception as e:  # a failing tool or timeout must not kill the server
                    return self._send(200, {"result": f"Error: {e.__class__.__name__}: {e}", "error": True})
                self._send(200, {"result": result, "error": err})

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True, name="tool-share").start()
        self.dir.mkdir(parents=True, exist_ok=True)
        _write_json(self.dir / TOOLS_FILE, {"tools": self.tools()})
        _write_json(self.dir / SERVER_FILE, {"port": self.port, "token": self.token})
        return self.port

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        (self.dir / SERVER_FILE).unlink(missing_ok=True)
        self._pool.shutdown(wait=False, cancel_futures=True)


def proxy_script() -> Path:
    """Where tools_proxy.py lives: next to this module, or in the packaged app's data folder."""
    import sys

    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "voice_assistant" / "tools_proxy.py"
    return Path(__file__).with_name("tools_proxy.py")


def proxy_python() -> str:
    """A Python that can run the proxy: this interpreter from source; 'python' for the packaged app (needs any Python 3)."""
    import sys

    return "python" if getattr(sys, "frozen", False) else sys.executable
