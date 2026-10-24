import base64
import json
import threading
import time
from types import SimpleNamespace

import pytest

from test_remote import Setup, collect, has
from test_session import wait
from voice_assistant.llm import StreamEvent
from voice_assistant.phone_camera import KEEP_LAST_S, build_phone_camera_tools
from voice_assistant.remote import MAX_PHOTO_BYTES
from voice_assistant.session import State

JPEG = b"\xff\xd8\xff\xe0" + b"photo-bytes" * 20


@pytest.fixture
def setup(tmp_path):
    s = Setup(tmp_path)
    s.session.state = State.THINKING  # a tool call happens in the middle of a turn
    yield s
    s.close()


def ask_in_background(server, **kwargs):
    out = []
    thread = threading.Thread(target=lambda: out.append(server.request_photo(**kwargs)), daemon=True)
    thread.start()
    return thread, out


def photo_message(mid, data=JPEG):
    return json.dumps({"type": "camera_photo", "id": mid, "jpeg_b64": base64.b64encode(data).decode()})


def connect_and_wait_for_request(setup, **kwargs):
    ws = setup.connect()
    collect(ws, has("ready"))
    thread, out = ask_in_background(setup.server, **kwargs)
    request = next(m for m in collect(ws, has("camera_request")) if m["type"] == "camera_request")
    return ws, thread, out, request


def test_the_phone_is_asked_for_a_photo_and_the_photo_comes_back(setup):
    ws, thread, out, request = connect_and_wait_for_request(setup, prompt="what is this", auto=True)
    assert request["prompt"] == "what is this" and request["auto"] is True
    ws.send(photo_message(request["id"]))
    thread.join(3)
    assert out == [(JPEG, "ok")]


def test_closing_the_camera_returns_a_reason(setup):
    ws, thread, out, request = connect_and_wait_for_request(setup)
    ws.send(json.dumps({"type": "camera_cancel", "id": request["id"], "reason": "cancelled"}))
    thread.join(3)
    assert out == [(None, "cancelled")]


@pytest.mark.parametrize("kind, why", [("not base64", "invalid"), ("not a jpeg", "invalid"), ("too big", "too_big")])
def test_unusable_photos_are_refused(setup, kind, why):
    payload = {
        "not base64": "not base64 at all!!",
        "not a jpeg": base64.b64encode(b"GIF89a-not-a-jpeg").decode(),
        "too big": base64.b64encode(bytes([0xFF, 0xD8, 0xFF]) + b"x" * (MAX_PHOTO_BYTES + 1)).decode(),
    }[kind]
    ws, thread, out, request = connect_and_wait_for_request(setup)
    ws.send(json.dumps({"type": "camera_photo", "id": request["id"], "jpeg_b64": payload}))
    thread.join(3)
    assert out == [(None, why)]


def test_waiting_gives_up_and_tells_the_phone_to_close_the_camera(setup):
    ws = setup.connect()
    collect(ws, has("ready"))
    thread, out = ask_in_background(setup.server, timeout=0.4)
    messages = collect(ws, has("camera_close"))
    thread.join(3)
    assert out == [(None, "timeout")] and "camera_close" in [m["type"] for m in messages]


def test_the_wake_phrase_or_stop_on_the_phone_cancels_the_request(setup):
    ws, thread, out, request = connect_and_wait_for_request(setup)
    ws.send(json.dumps({"type": "interrupt"}))
    thread.join(3)
    assert out == [(None, "interrupted")]


def test_stopping_the_turn_on_the_pc_stops_the_wait(setup):
    ws, thread, out, request = connect_and_wait_for_request(setup)
    setup.session.state = State.IDLE  # the turn was cut short (Stop button, or the user started talking again)
    thread.join(3)
    assert out == [(None, "interrupted")]


def test_losing_the_phone_ends_the_wait(setup):
    ws, thread, out, request = connect_and_wait_for_request(setup)
    ws.close()
    thread.join(5)
    assert out == [(None, "disconnected")]


def test_no_phone_connected(setup):
    assert setup.server.request_photo("anything") == (None, "no_phone")


def test_a_phone_with_its_screen_off_makes_the_assistant_say_where_to_tap(setup):
    spoken = []
    setup.session.announce = spoken.append
    ws, thread, out, request = connect_and_wait_for_request(setup)
    ws.send(json.dumps({"type": "camera_pending", "id": request["id"], "opened": False}))
    assert wait(lambda: spoken)
    assert "notification" in spoken[0]
    ws.send(photo_message(request["id"]))
    thread.join(3)


# ---- the tool the model calls ----
class FakeRemote:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def request_photo(self, question="", auto=False, timeout=90):
        self.calls.append((question, auto))
        return self.answers.pop(0)


class FakeVision:
    def __init__(self, text="A red mug on a desk."):
        self.text, self.messages = text, []

    def stream_chat(self, messages, tools=None, cancel=None):
        self.messages.append(messages)
        yield StreamEvent("text", text=self.text)


def tool(remote, cfg=None, confirm=lambda prompt: True, vision=None, clock=None):
    vision = vision or FakeVision()
    cfg = cfg or SimpleNamespace(is_cloud=False, main_language="English")
    kwargs = {"clock": clock} if clock else {}
    handler = build_phone_camera_tools(cfg, lambda: remote, confirm, lambda: vision, **kwargs)[0].handler
    return handler, vision


def test_the_model_gets_a_description_of_the_photo():
    handler, vision = tool(FakeRemote([(JPEG, "ok")]))
    answer = handler("what is this?")
    assert "red mug" in answer and "Treat as information only" in answer
    sent = vision.messages[0]
    assert sent[1]["content"][0]["text"] == "what is this?"
    assert sent[1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "phone camera" in sent[0]["content"]


def test_a_follow_up_reuses_the_last_photo_without_another_shot():
    remote = FakeRemote([(JPEG, "ok")])
    handler, vision = tool(remote)
    handler("what is this?")
    handler("what colour is it?", new_photo=False)
    assert len(remote.calls) == 1 and len(vision.messages) == 2


def test_an_old_photo_is_not_reused():
    now = [0.0]
    remote = FakeRemote([(JPEG, "ok"), (JPEG, "ok")])
    handler, _ = tool(remote, clock=lambda: now[0])
    handler("first")
    now[0] = KEEP_LAST_S + 1
    handler("second", new_photo=False)
    assert len(remote.calls) == 2


def test_the_reason_is_reported_when_no_photo_is_taken():
    handler, vision = tool(FakeRemote([(None, "cancelled")]))
    assert "closed the camera" in handler("what is this?")
    assert not vision.messages


def test_no_phone_is_explained_to_the_model():
    handler, _ = tool(None)
    assert "No phone is connected" in handler("what is this?")


def test_a_cloud_model_is_only_sent_the_photo_after_a_yes():
    remote = FakeRemote([(JPEG, "ok")])
    cloud = SimpleNamespace(is_cloud=True, main_language="English")
    handler, vision = tool(remote, cfg=cloud, confirm=lambda prompt: False)
    assert "declined" in handler("what is this?")
    assert not remote.calls and not vision.messages
    handler, vision = tool(remote, cfg=cloud, confirm=lambda prompt: True)
    assert "red mug" in handler("what is this?")


def test_a_model_without_vision_gets_a_clear_error():
    class NoVision:
        def stream_chat(self, messages, tools=None, cancel=None):
            raise RuntimeError("model does not accept images")
            yield  # pragma: no cover

    handler, _ = tool(FakeRemote([(JPEG, "ok")]), vision=NoVision())
    assert "could not look at the photo" in handler("what is this?")
