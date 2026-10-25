import json
import threading

import httpx

from voice_assistant.agent import Agent
from voice_assistant.llm import OpenAICompatClient, StreamEvent, ThinkSplitter


def sse(*chunks):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.MockTransport(lambda req: httpx.Response(200, text=body))


def run(pieces):
    s = ThinkSplitter()
    out = []
    for p in pieces:
        out += s.feed(p)
    out += s.flush()
    text = "".join(t for k, t in out if k == "text")
    think = "".join(t for k, t in out if k == "thinking")
    return text, think


def test_splitter_separates_inline_reasoning():
    assert run(["<think>plan it</think>Answer."]) == ("Answer.", "plan it")


def test_splitter_handles_tags_cut_across_chunks():
    assert run(["<th", "ink>step ", "one</thi", "nk>Done", "."]) == ("Done.", "step one")


def test_splitter_leaves_ordinary_text_alone():
    assert run(["a < b and c > d, ", "<b>bold</b>"]) == ("a < b and c > d, <b>bold</b>", "")


def test_splitter_keeps_a_lone_angle_bracket_at_the_end():
    assert run(["x <"]) == ("x <", "")


def test_client_reads_reasoning_content_field():
    chunks = [
        {"choices": [{"delta": {"reasoning_content": "Hmm, "}}]},
        {"choices": [{"delta": {"reasoning_content": "two plus two."}}]},
        {"choices": [{"delta": {"content": "Four."}}]},
    ]
    evs = list(OpenAICompatClient("http://x/v1", "m", transport=sse(*chunks)).stream_chat([]))
    assert "".join(e.text for e in evs if e.kind == "thinking") == "Hmm, two plus two."
    assert "".join(e.text for e in evs if e.kind == "text") == "Four."


class Thinker:
    def stream_chat(self, messages, tools=None, cancel=None):
        yield StreamEvent("thinking", text="let me see")
        yield StreamEvent("text", text="Hi.")


def test_agent_reports_thinking_but_does_not_keep_or_speak_it():
    agent = Agent(Thinker(), "sys")
    seen = []
    agent.on_thinking = seen.append
    spoken = list(agent.respond("hello", threading.Event()))
    assert seen == ["let me see"]
    assert spoken == ["Hi."]
    assert agent.messages[-1] == {"role": "assistant", "content": "Hi."}
