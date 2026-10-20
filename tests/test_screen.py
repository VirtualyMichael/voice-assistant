from voice_assistant.config import Config
from voice_assistant.llm import StreamEvent, to_anthropic
from voice_assistant.screen import build_screen_tools, capture_png, image_message


class FakeVision:
    def __init__(self):
        self.seen = None

    def stream_chat(self, messages, tools=None, cancel=None):
        self.seen = messages
        yield StreamEvent("text", text="A code editor ")
        yield StreamEvent("text", text="with an error.")


def tool(cfg, confirm=lambda p: True, llm=None, capture=lambda m: b"\x89PNG"):
    return build_screen_tools(cfg, confirm, lambda: llm, capture)[0].handler


def test_asks_model_with_image_and_confirms():
    llm, asked = FakeVision(), []
    out = tool(Config(), lambda p: asked.append(p) or True, llm)("what is this?")
    assert "A code editor with an error." in out and asked
    part = llm.seen[1]["content"][1]
    assert part["type"] == "image_url" and part["image_url"]["url"].startswith("data:image/png;base64,")


def test_declined_never_captures():
    called = []
    out = tool(Config(), lambda p: False, FakeVision(), lambda m: called.append(m) or b"")()
    assert "declined" in out and not called


def test_model_without_vision_gives_clear_error():
    class Broken:
        def stream_chat(self, *a, **k):
            raise RuntimeError("HTTP 400: image input not supported")
            yield

    assert "may not support images" in tool(Config(), llm=Broken())("x")


def test_anthropic_gets_image_block():
    _, msgs = to_anthropic(image_message("hi", b"abc"))
    blocks = msgs[0]["content"]
    assert blocks[0] == {"type": "text", "text": "hi"}
    assert blocks[1]["source"]["media_type"] == "image/png" and blocks[1]["type"] == "image"


def test_real_capture_is_png(qapp=None):
    png = capture_png(1, 400)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
