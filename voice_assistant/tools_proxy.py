"""Stdio MCP server that lets an MCP client (for example Odysseus) use the Voice Assistant's tools.

Standalone on purpose: standard library only, so any Python can run it (`python tools_proxy.py`) and the packaged,
windowless app does not need to. It owns no tools itself. It forwards to the running Voice Assistant over a local,
token-protected port, so every consent prompt (opening apps, writing files, shell, controlling windows) still appears in
the Voice Assistant window. The app writes the port and token to tool-server.json in its settings folder while sharing
is switched on (Settings > AI > Odysseus > "Let Odysseus use this app's tools").
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DIR = Path(os.environ.get("APPDATA", Path.home())) / "VoiceAssistant"
CALL_TIMEOUT_S = 660  # the app waits up to 60 s per confirmation and runs one tool at a time
PROTOCOL = "2025-03-26"


def _server():
    try:
        return json.loads((DIR / "tool-server.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _request(method, path, body=None, timeout=15):
    info = _server()
    if not info:
        raise RuntimeError("The Voice Assistant app is not running, or tool sharing is switched off in its Settings.")
    req = urllib.request.Request(
        f"http://127.0.0.1:{int(info['port'])}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"X-VA-Token": str(info["token"]), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed 127.0.0.1 address
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Voice Assistant refused the request (HTTP {e.code}).") from e
    except (urllib.error.URLError, OSError) as e:
        raise RuntimeError("Could not reach the Voice Assistant app. Is it running?") from e


def list_tools():
    try:
        return _request("GET", "/tools")["tools"]
    except RuntimeError:  # app not running: fall back to the list it saved, so the client still learns the tool names
        try:
            return json.loads((DIR / "tools-shared.json").read_text(encoding="utf-8"))["tools"]
        except (OSError, ValueError, KeyError):
            return []


def call_tool(name, arguments):
    try:
        out = _request("POST", "/call", {"name": name, "arguments": arguments or {}}, timeout=CALL_TIMEOUT_S)
        return {"content": [{"type": "text", "text": str(out.get("result", ""))}], "isError": bool(out.get("error"))}
    except RuntimeError as e:
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # notification: nothing to answer
        return None
    if method == "initialize":
        version = (msg.get("params") or {}).get("protocolVersion") or PROTOCOL
        result = {"protocolVersion": version, "capabilities": {"tools": {}}, "serverInfo": {"name": "voice-assistant-tools", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": list_tools()}
    elif method == "tools/call":
        p = msg.get("params") or {}
        result = call_tool(p.get("name", ""), p.get("arguments"))
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            reply = handle(json.loads(line))
        except Exception as e:  # never die on one bad message
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32603, "message": str(e)}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
