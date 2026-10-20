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


WINS = [(1, "VRChat", "VRChat"), (2, "Discord | #general", "Discord"), (3, "Untitled - Notepad", "notepad")]


def tool(cfg, confirm=lambda p: True, llm=None, capture=lambda m, region=None: b"\x89PNG"):
    return build_screen_tools(cfg, confirm, lambda: llm, capture, lambda: WINS, lambda h: (10, 20, 800, 600))[0].handler


def test_asks_model_with_image_and_confirms():
    llm, asked = FakeVision(), []
    out = tool(Config(), lambda p: asked.append(p) or True, llm)("what is this?")
    assert "A code editor with an error." in out and asked
    part = llm.seen[1]["content"][1]
    assert part["type"] == "image_url" and part["image_url"]["url"].startswith("data:image/png;base64,")


def test_declined_never_captures():
    called = []
    out = tool(Config(), lambda p: False, FakeVision(), lambda m, region=None: called.append(m) or b"")()
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


def test_match_window_by_name():
    from voice_assistant.screen import match_window
    assert match_window(WINS, "vr chat")[0] == 1 and match_window(WINS, "discord")[0] == 2 and match_window(WINS, "note pad")[0] == 3
    assert match_window(WINS, "steam") is None


def test_window_capture_translates_into_main_language():
    llm, got = FakeVision(), []
    out = tool(Config(main_language="Japanese"), llm=llm, capture=lambda m, region=None: got.append(region) or b"\x89PNG")("translate the text", "VRChat")
    assert got == [(10, 20, 800, 600)] and "A code editor" in out
    assert "main language is Japanese" in llm.seen[0]["content"]


def test_unknown_window_lists_open_ones():
    out = tool(Config())("hi", "Steam")
    assert "No open window matches" in out and "Discord" in out


def test_real_window_list_runs():
    from voice_assistant.screen import list_windows
    assert isinstance(list_windows(), list)
