import threading

from test_session import FakeRec, Rec, make_speaker, wait

from voice_assistant.agent import Agent
from voice_assistant.config import Config
from voice_assistant.llm import StreamEvent, ToolCall
from voice_assistant.session import Session
from voice_assistant.tools import Tool, ToolRegistry


class ToolThenText:
    def __init__(self):
        self.rounds = [[StreamEvent("tool_calls", calls=(ToolCall("1", "ping", "{}"),))], [StreamEvent("text", text="All done here.")]]

    def stream_chat(self, messages, tools=None, cancel=None):
        yield from self.rounds.pop(0)


def test_filler_spoken_once_when_task_starts():
    said = []
    spk, played = make_speaker(0.0)
    orig_say = spk.say
    spk.say = lambda t: (said.append(t), orig_say(t))
    reg = ToolRegistry([Tool("ping", "d", {"type": "object", "properties": {}}, lambda: "pong")])
    ev = Rec()
    s = Session(Config(listen_tone=False, follow_up=False, tool_filler="One moment."), Agent(ToolThenText(), "sys", reg), spk, FakeRec(), events=ev)
    s.submit_text("do it")
    assert ev.done.wait(3)
    assert said[0] == "One moment." and said.count("One moment.") == 1 and said[-1] == "All done here."
    s.close()
