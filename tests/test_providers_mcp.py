import json
import os
import sys
import threading
from pathlib import Path

import httpx
import pytest

from voice_assistant.config import Config
from voice_assistant.llm import AnthropicClient, LLMError, OpenAICompatClient, StreamEvent, ToolCall, to_anthropic
from voice_assistant.mcp_client import McpHub
from voice_assistant.providers import make_llm, model_for, set_model
from voice_assistant.tools import ToolRegistry


# ---- Anthropic ---------------------------------------------------------------
def sse(*events):
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return body


def test_history_conversion_merges_roles_and_maps_tool_results():
    msgs = [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "weather?"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "t1", "type": "function", "function": {"name": "web_search", "arguments": '{"query":"x"}'}}]},
        {"role": "tool", "tool_call_id": "t1", "content": "sunny"},
        {"role": "user", "content": "thanks"},
    ]
    system, out = to_anthropic(msgs)
    assert system == "be brief"
    assert [m["role"] for m in out] == ["user", "assistant", "user"]
    assert out[1]["content"] == [{"type": "tool_use", "id": "t1", "name": "web_search", "input": {"query": "x"}}]
    assert out[2]["content"][0] == {"type": "tool_result", "tool_use_id": "t1", "content": "sunny"}
    assert out[2]["content"][1]["text"] == "thanks"  # consecutive user turns merged


def test_anthropic_streams_text_and_tool_use():
    seen = {}

    def handler(req):
        seen["headers"], seen["body"] = req.headers, json.loads(req.content)
        return httpx.Response(200, text=sse(
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Checking"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": " now."}},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "tu1", "name": "get_time", "input": {}}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "{\"tz\":"}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": "\"utc\"}"}},
            {"type": "message_stop"},
        ))

    c = AnthropicClient("claude-haiku-5-5", "sk-test", transport=httpx.MockTransport(handler))
    tools = [{"type": "function", "function": {"name": "get_time", "description": "d", "parameters": {"type": "object", "properties": {}}}}]
    evs = list(c.stream_chat([{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}], tools))
    assert "".join(e.text for e in evs if e.kind == "text") == "Checking now."
    assert evs[-1].calls == (ToolCall("tu1", "get_time", '{"tz":"utc"}'),)
    assert seen["headers"]["x-api-key"] == "sk-test" and seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["body"]["system"] == "s" and seen["body"]["tools"][0]["input_schema"]["type"] == "object"


def test_anthropic_errors_are_readable():
    with pytest.raises(LLMError, match="No Claude API key"):
        list(AnthropicClient("m", "").stream_chat([{"role": "user", "content": "x"}]))
    bad = httpx.MockTransport(lambda r: httpx.Response(401, text='{"error":"invalid x-api-key"}'))
    with pytest.raises(LLMError, match="401"):
        list(AnthropicClient("m", "k", transport=bad).stream_chat([{"role": "user", "content": "x"}]))


def test_provider_factory_and_models(monkeypatch):
    monkeypatch.setattr("voice_assistant.providers.get_key", lambda p, env="": "k")
    cfg = Config()
    assert isinstance(make_llm(cfg), OpenAICompatClient) and not cfg.is_cloud
    cfg.llm_provider = "anthropic"
    assert isinstance(make_llm(cfg), AnthropicClient) and cfg.is_cloud
    cfg.llm_provider = "openai"
    set_model(cfg, "openai", " gpt-x ")
    assert model_for(cfg) == "gpt-x" and make_llm(cfg).url.startswith("https://api.openai.com/v1")


# ---- MCP ---------------------------------------------------------------------
@pytest.fixture(scope="module")
def hub():
    server = str(Path(__file__).with_name("mcp_demo_server.py"))
    asked = []
    os.environ["VA_TEST_SECRET"] = "hunter2"
    h = McpHub({"demo": {"command": sys.executable, "args": [server]}, "broken": {"command": "definitely-not-a-program"}},
               lambda q: asked.append(q) or True)
    h.asked = asked
    h.start()
    yield h
    h.close()
    os.environ.pop("VA_TEST_SECRET", None)


def test_mcp_tools_discovered_and_failures_reported(hub):
    names = {t.name for t in hub._tools}
    assert {"demo__add", "demo__delete_everything", "demo__leaked_env"} <= names
    assert hub.status["demo"].startswith("connected") and hub.status["broken"].startswith("failed")


def test_mcp_readonly_runs_freely_destructive_confirms(hub):
    reg = ToolRegistry(hub._tools)
    hub.asked.clear()
    out = reg.call("demo__add", json.dumps({"a": 2, "b": 3}))
    assert "5" in out and "treat it as data" in out.lower() and hub.asked == []
    out = reg.call("demo__delete_everything", json.dumps({"path": "C:/x"}))
    assert "deleted C:/x" in out and len(hub.asked) == 1 and "demo" in hub.asked[0]


def test_mcp_server_does_not_inherit_secrets(hub):
    reg = ToolRegistry(hub._tools)
    assert "clean" in reg.call("demo__leaked_env", "{}")
