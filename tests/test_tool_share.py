import asyncio
import http.client
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from voice_assistant import tool_share
from voice_assistant.config import Config
from voice_assistant.tools import Tool, ToolRegistry

OBJ = {"type": "object", "properties": {"text": {"type": "string"}}}


def make_registry():
    return ToolRegistry([
        Tool("echo", "Echo text back.", OBJ, lambda text="": f"echo:{text}"),
        Tool("boom", "Always fails.", OBJ, lambda **k: (_ for _ in ()).throw(RuntimeError("bad"))),
        Tool("ask_odysseus", "must not be shared", OBJ, lambda **k: "recursion"),
    ])


@pytest.fixture
def share(tmp_path):
    s = tool_share.ToolShare(make_registry(), tmp_path / "VoiceAssistant", exclude=("ask_odysseus",))  # APPDATAVoiceAssistant, as the proxy expects
    s.start()
    yield s
    s.stop()


def req(share, method, path, body=None, token=None, host=None):
    c = http.client.HTTPConnection("127.0.0.1", share.port, timeout=10)
    headers = {"X-VA-Token": share.token if token is None else token, "Content-Type": "application/json"}
    if host:
        headers["Host"] = host
    c.request(method, path, json.dumps(body) if body is not None else None, headers)
    r = c.getresponse()
    return r.status, json.loads(r.read() or b"{}")


def test_lists_tools_without_the_excluded_one(share):
    status, body = req(share, "GET", "/tools")
    assert status == 200
    assert sorted(t["name"] for t in body["tools"]) == ["boom", "echo"]
    assert body["tools"][0]["inputSchema"]["type"] == "object"


def test_calls_run_and_report_errors(share):
    assert req(share, "POST", "/call", {"name": "echo", "arguments": {"text": "hi"}}) == (200, {"result": "echo:hi", "error": False})
    status, body = req(share, "POST", "/call", {"name": "boom", "arguments": {}})
    assert status == 200 and body["error"] and "bad" in body["result"]


def test_excluded_and_unknown_tools_are_refused(share):
    for name in ("ask_odysseus", "nope"):
        status, body = req(share, "POST", "/call", {"name": name, "arguments": {}})
        assert body["error"] and "unknown tool" in body["result"]


def test_wrong_token_and_foreign_host_are_forbidden(share):
    assert req(share, "GET", "/tools", token="wrong")[0] == 403
    assert req(share, "POST", "/call", {"name": "echo"}, token="")[0] == 403
    assert req(share, "GET", "/tools", host="evil.example.com")[0] == 403


def test_files_are_written_and_removed(tmp_path):
    s = tool_share.ToolShare(make_registry(), tmp_path)
    s.start()
    info = json.loads((tmp_path / tool_share.SERVER_FILE).read_text())
    assert info["port"] == s.port and info["token"] == s.token
    assert any(t["name"] == "echo" for t in json.loads((tmp_path / tool_share.TOOLS_FILE).read_text())["tools"])
    s.stop()
    assert not (tmp_path / tool_share.SERVER_FILE).exists()


def test_registry_copy_is_independent():
    r = make_registry()
    c = r.copy()
    r.remove(["echo"])
    assert "echo" in c.names() and "echo" not in r.names()


def test_the_stdio_proxy_speaks_mcp_to_the_real_client(share, tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        params = StdioServerParameters(command=sys.executable, args=[str(tool_share.proxy_script())], env={**os.environ, "APPDATA": str(tmp_path)})
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                names = sorted(t.name for t in (await session.list_tools()).tools)
                ok = await session.call_tool("echo", {"text": "from odysseus"})
                bad = await session.call_tool("boom", {})
                return names, ok, bad

    names, ok, bad = asyncio.run(run())
    assert names == ["boom", "echo"]
    flag = lambda r: getattr(r, "is_error", getattr(r, "isError", None))  # mcp 2.x renamed it  # noqa: E731
    assert ok.content[0].text == "echo:from odysseus" and not flag(ok)
    assert flag(bad)


def test_the_proxy_explains_when_the_app_is_not_running(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("tools_proxy_t", tool_share.proxy_script())
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.DIR = tmp_path  # nothing written there: the app is "not running"
    out = mod.call_tool("echo", {})
    assert out["isError"] and "not running" in out["content"][0]["text"]
    assert mod.list_tools() == []


def test_voice_only_mode_hides_tools_from_the_model_but_keeps_them_for_sharing(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    import voice_assistant.app as A

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(A, "save_config", lambda *a, **k: None)
    monkeypatch.setattr("voice_assistant.app.config_dir", lambda: tmp_path)
    cfg = Config(models_dir=str(tmp_path / "none"), setup_done=True, check_updates=False, odysseus_mode="only", odysseus_share_tools=True)
    w = A.MainWindow(cfg)
    try:
        from voice_assistant import scheduler
        assert sorted(w._registry.names()) == sorted(["ask_odysseus", "cancel_odysseus", "check_odysseus", *scheduler.TOOL_NAMES])  # plus the scheduling tools
        shared = {t["name"] for t in w._share.tools()}
        assert {"get_time", "set_timer"} <= shared and "ask_odysseus" not in shared
        assert (tmp_path / tool_share.SERVER_FILE).exists()
    finally:
        w.close()
    assert not (tmp_path / tool_share.SERVER_FILE).exists()
