"""Streaming client for OpenAI-compatible chat endpoints (LM Studio, cloud providers)."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import Iterator, Protocol

import httpx


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string


@dataclass(frozen=True)
class StreamEvent:
    kind: str  # "text" | "thinking" | "tool_calls"
    text: str = ""
    calls: tuple[ToolCall, ...] = ()


class ThinkSplitter:
    """Separate <think>...</think> reasoning from the answer in streamed text, even when a tag is cut across two chunks.

    Models that put their reasoning inline (instead of in a separate field) are shown the same way as the ones that do not.
    """

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self):
        self._inside = False
        self._hold = ""

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        """Returns [("text" | "thinking", piece), ...] in order."""
        out: list[tuple[str, str]] = []
        buf = self._hold + chunk
        self._hold = ""
        while buf:
            tag = self.CLOSE if self._inside else self.OPEN
            i = buf.find(tag)
            if i >= 0:
                if i:
                    out.append(("thinking" if self._inside else "text", buf[:i]))
                buf = buf[i + len(tag):]
                self._inside = not self._inside
                continue
            keep = 0  # hold back a possible start of the tag at the end of the chunk
            for n in range(min(len(tag) - 1, len(buf)), 0, -1):
                if tag.startswith(buf[-n:]):
                    keep = n
                    break
            if len(buf) > keep:
                out.append(("thinking" if self._inside else "text", buf[: len(buf) - keep]))
            self._hold = buf[len(buf) - keep:]
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        rest, self._hold = self._hold, ""
        return [("thinking" if self._inside else "text", rest)] if rest else []


class LLM(Protocol):
    def stream_chat(
        self, messages: list[dict], tools: list[dict] | None = None, cancel: threading.Event | None = None
    ) -> Iterator[StreamEvent]: ...


class OpenAICompatClient:
    def __init__(self, base_url: str, model: str, api_key: str = "", transport: httpx.BaseTransport | None = None, extra_body: dict | None = None):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self._transport = transport
        self.extra_body = extra_body or {}

    def stream_chat(self, messages, tools=None, cancel=None):
        body: dict = {**self.extra_body, "model": self.model, "messages": messages, "stream": True}
        if tools:
            body["tools"] = tools
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        calls: dict[int, dict] = {}
        splitter = ThinkSplitter()
        try:
            with httpx.Client(transport=self._transport, timeout=httpx.Timeout(120, connect=5)) as client:
                with client.stream("POST", self.url, json=body, headers=headers) as r:
                    if r.status_code >= 400:
                        r.read()
                        raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
                    for line in r.iter_lines():
                        if cancel is not None and cancel.is_set():
                            return
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        choices = json.loads(data).get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                        if reasoning:
                            yield StreamEvent("thinking", text=reasoning)
                        if delta.get("content"):
                            for kind, piece in splitter.feed(delta["content"]):
                                yield StreamEvent(kind, text=piece)
                        for tc in delta.get("tool_calls") or []:
                            acc = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                            acc["id"] = tc.get("id") or acc["id"]
                            fn = tc.get("function") or {}
                            acc["name"] = fn.get("name") or acc["name"]
                            acc["arguments"] += fn.get("arguments") or ""
        except httpx.HTTPError as e:
            raise LLMError(f"Cannot reach {self.url}: {e}") from e
        for kind, piece in splitter.flush():
            yield StreamEvent(kind, text=piece)
        if calls:
            yield StreamEvent(
                "tool_calls",
                calls=tuple(ToolCall(c["id"] or f"call_{i}", c["name"], c["arguments"] or "{}") for i, c in sorted(calls.items())),
            )


def _anthropic_parts(content) -> list[dict]:
    """User content is a string, or OpenAI-style parts (text / image_url with a data: URL)."""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    out = []
    for p in content:
        if p.get("type") == "image_url":
            header, _, data = p["image_url"]["url"].partition(",")
            out.append({"type": "image", "source": {"type": "base64", "media_type": header[5:].split(";")[0], "data": data}})
        else:
            out.append({"type": "text", "text": p.get("text", "")})
    return out


def to_anthropic(messages: list[dict]) -> tuple[str, list[dict]]:
    """OpenAI-style history (system / user / assistant+tool_calls / tool) -> Anthropic system string and messages."""
    system = "\n".join(m["content"] for m in messages if m["role"] == "system")
    out: list[dict] = []

    def push(role: str, blocks: list[dict]) -> None:
        if out and out[-1]["role"] == role:  # the API wants strictly alternating roles
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})

    for m in messages:
        r = m["role"]
        if r == "user":
            push("user", _anthropic_parts(m["content"]))
        elif r == "assistant":
            blocks = [{"type": "text", "text": m["content"]}] if m.get("content") else []
            for tc in m.get("tool_calls") or []:
                fn = tc["function"]
                blocks.append({"type": "tool_use", "id": tc["id"], "name": fn["name"], "input": json.loads(fn.get("arguments") or "{}")})
            if blocks:
                push("assistant", blocks)
        elif r == "tool":
            push("user", [{"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}])
    return system, out


class AnthropicClient:
    """Streaming client for the Claude Messages API (https://api.anthropic.com/v1/messages)."""

    def __init__(self, model: str, api_key: str, base_url: str = "https://api.anthropic.com/v1", max_tokens: int = 1024,
                 transport: httpx.BaseTransport | None = None):
        self.url, self.model, self.api_key = base_url.rstrip("/") + "/messages", model, api_key
        self.max_tokens, self._transport = max_tokens, transport

    def stream_chat(self, messages, tools=None, cancel=None):
        if not self.api_key:
            raise LLMError("No Claude API key saved. Use Settings > Set API key.")
        system, msgs = to_anthropic(messages)
        body: dict = {"model": self.model, "max_tokens": self.max_tokens, "stream": True, "messages": msgs}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {"name": t["function"]["name"], "description": t["function"].get("description", ""), "input_schema": t["function"]["parameters"]}
                for t in tools
            ]
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        calls: dict[int, dict] = {}
        try:
            with httpx.Client(transport=self._transport, timeout=httpx.Timeout(120, connect=5)) as client:
                with client.stream("POST", self.url, json=body, headers=headers) as r:
                    if r.status_code >= 400:
                        r.read()
                        raise LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
                    for line in r.iter_lines():
                        if cancel is not None and cancel.is_set():
                            return
                        if not line.startswith("data:"):
                            continue
                        ev = json.loads(line[5:].strip())
                        kind = ev.get("type")
                        if kind == "content_block_start" and ev["content_block"]["type"] == "tool_use":
                            cb = ev["content_block"]
                            calls[ev["index"]] = {"id": cb["id"], "name": cb["name"], "arguments": ""}
                        elif kind == "content_block_delta":
                            d = ev["delta"]
                            if d["type"] == "text_delta" and d.get("text"):
                                yield StreamEvent("text", text=d["text"])
                            elif d["type"] == "thinking_delta" and d.get("thinking"):
                                yield StreamEvent("thinking", text=d["thinking"])
                            elif d["type"] == "input_json_delta":
                                calls[ev["index"]]["arguments"] += d.get("partial_json", "")
                        elif kind == "error":
                            raise LLMError(ev.get("error", {}).get("message", "API error"))
                        elif kind == "message_stop":
                            break
        except httpx.HTTPError as e:
            raise LLMError(f"Cannot reach {self.url}: {e}") from e
        if calls:
            yield StreamEvent("tool_calls", calls=tuple(ToolCall(c["id"], c["name"], c["arguments"] or "{}") for _, c in sorted(calls.items())))
