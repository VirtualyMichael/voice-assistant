import json
import time

import pytest
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from test_remote import Setup, collect, has
from voice_assistant.config import Config
from voice_assistant.remote import (
    PAIR_ALPHABET, PAIR_CODE_LENGTH, PAIR_MAX_TRIES, PAIR_TTL_S, PairedDevices, RemoteServer, format_code, normalize_code, phone_link_checks,
)
from voice_assistant.session import MultiEvents


class Clock:
    def __init__(self, now=10_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def devices(tmp_path, clock):
    return PairedDevices(tmp_path / "devices.json", clock=clock)


# ---- the code itself ----
def test_a_code_is_short_readable_and_random(devices):
    offer = devices.begin_pairing()
    assert len(offer.code) == PAIR_CODE_LENGTH and set(offer.code) <= set(PAIR_ALPHABET)
    assert not set(offer.code) & set("01OIL")  # nothing that looks like something else
    assert devices.begin_pairing().code != offer.code
    assert format_code("ABCDEFGHJK") == "ABCD-EFGH-JK"


def test_typed_codes_are_forgiving(devices):
    offer = devices.begin_pairing()
    typed = format_code(offer.code).lower().replace("-", " ")
    assert normalize_code(typed) == offer.code
    assert devices.redeem(typed, "Pixel") is not None


# ---- redeeming ----
def test_the_code_gives_the_phone_its_own_token_once(devices):
    offer = devices.begin_pairing()
    device_id, token = devices.redeem(offer.code, "Pixel 8 Pro")
    assert devices.verify(token) == device_id
    assert devices.redeem(offer.code, "Pixel again") is None  # single use
    assert [d["name"] for d in devices.list()] == ["Pixel 8 Pro"]


def test_a_code_expires(devices, clock):
    offer = devices.begin_pairing()
    assert devices.pairing_seconds_left() == int(PAIR_TTL_S)
    clock.now += 100
    assert devices.pairing_seconds_left() == int(PAIR_TTL_S) - 100
    clock.now += PAIR_TTL_S
    assert devices.pairing_seconds_left() == 0
    assert devices.redeem(offer.code, "Late phone") is None and not devices.list()


def test_a_few_wrong_guesses_cancel_the_offer(devices):
    offer = devices.begin_pairing()
    for _ in range(PAIR_MAX_TRIES):
        assert devices.redeem("WRONGWRONG", "Guesser") is None
    assert devices.redeem(offer.code, "The real phone") is None  # even the right code is now dead
    assert devices.pairing_seconds_left() == 0 and not devices.list()


def test_a_new_offer_replaces_the_old_one(devices):
    first = devices.begin_pairing()
    second = devices.begin_pairing()
    assert devices.redeem(first.code, "Old") is None
    assert devices.redeem(second.code, "New") is not None


def test_cancelling_closes_the_offer(devices):
    offer = devices.begin_pairing()
    devices.cancel_pairing()
    assert devices.redeem(offer.code, "Phone") is None


def test_the_code_is_never_written_to_disk_and_the_token_only_as_a_hash(devices, tmp_path):
    offer = devices.begin_pairing()
    assert not (tmp_path / "devices.json").exists()
    device_id, token = devices.redeem(offer.code, "Pixel")
    text = (tmp_path / "devices.json").read_text()
    assert offer.code not in text and token not in text


def test_last_seen_is_recorded_at_most_once_a_minute(devices, clock):
    offer = devices.begin_pairing()
    device_id, _ = devices.redeem(offer.code, "Pixel")
    devices.touch(device_id)
    assert devices.list()[0]["last_seen"] == int(clock.now)
    clock.now += 10
    devices.touch(device_id)
    assert devices.list()[0]["last_seen"] == int(clock.now) - 10
    clock.now += 60
    devices.touch(device_id)
    assert devices.list()[0]["last_seen"] == int(clock.now)


# ---- over the phone link ----
@pytest.fixture
def linked(tmp_path, clock):
    s = Setup(tmp_path)
    s.devices._clock = clock  # the same fake clock that the unit tests use
    events = []
    s.server.listeners.append(events.append)
    yield s, clock, events
    s.close()


def pair_with(s, code, name="Pixel 8 Pro"):
    ws = connect(f"ws://127.0.0.1:{s.server.port}")
    ws.send(json.dumps({"type": "hello", "pair_code": code, "device": name}))
    return ws


def test_a_phone_pairs_with_the_code_and_gets_its_token(linked):
    s, clock, events = linked
    offer, payload = s.server.begin_pairing()
    ws = pair_with(s, format_code(offer.code).lower())
    messages = collect(ws, has("ready"))
    paired = next(m for m in messages if m["type"] == "paired")
    assert paired["token"] and paired["device_id"] and paired["name"]
    assert [m["type"] for m in messages].index("paired") < [m["type"] for m in messages].index("ready")
    assert {"event": "paired", "name": "Pixel 8 Pro"} in events
    ws.close()
    second = connect(f"ws://127.0.0.1:{s.server.port}")  # the new token is a permanent pairing
    second.send(json.dumps({"type": "hello", "token": paired["token"], "device": "x"}))
    assert collect(second, has("ready"))[0]["type"] == "ready"


def test_the_qr_text_carries_the_address_and_the_code_but_no_token(linked):
    s, _, _ = linked
    offer, payload = s.server.begin_pairing()
    data = json.loads(payload)
    assert data["v"] == 2 and data["host"] == "127.0.0.1" and data["port"] == s.server.port
    assert data["code"] == offer.code and data["name"]
    assert "token" not in data and len(payload) < 140  # short enough for a clean, easy-to-scan QR code
    assert s.server.status()["pairing_seconds"] > 0


def test_a_used_or_wrong_code_is_refused_with_a_plain_reason(linked):
    s, _, _ = linked
    offer, _ = s.server.begin_pairing()
    pair_with(s, offer.code).close()
    ws = pair_with(s, offer.code)
    refused = next(m for m in collect(ws, has("error")) if m["type"] == "error")
    assert refused["code"] == "bad_pair_code" and "Show a new one" in refused["message"]
    with pytest.raises(ConnectionClosed):
        ws.recv(timeout=2)


def test_an_expired_code_is_refused(linked):
    s, clock, _ = linked
    offer, _ = s.server.begin_pairing()
    clock.now += PAIR_TTL_S + 1
    ws = pair_with(s, offer.code)
    assert next(m for m in collect(ws, has("error")) if m["type"] == "error")["code"] == "bad_pair_code"
    assert len(s.devices.list()) == 1  # only the phone that the test fixture paired itself


def test_a_wrong_token_still_gets_the_old_message(linked):
    s, _, _ = linked
    ws = s.connect(token="nope")
    assert next(m for m in collect(ws, has("error")) if m["type"] == "error")["code"] == "not_paired"


def test_forgetting_the_pc_on_the_phone_removes_the_phone_from_the_pc(linked):
    s, _, events = linked
    ws = s.connect()
    collect(ws, has("ready"))
    ws.send(json.dumps({"type": "forget_me"}))
    with pytest.raises(ConnectionClosed):
        for _ in range(20):
            ws.recv(timeout=1)
    assert s.devices.verify(s.token) is None
    assert any(e["event"] == "disconnected" for e in events)


def test_connecting_and_disconnecting_are_announced(linked):
    s, _, events = linked
    ws = s.connect()
    collect(ws, has("ready"))
    ws.close()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not any(e["event"] == "disconnected" for e in events):
        time.sleep(0.05)
    assert [e["event"] for e in events] == ["connected", "disconnected"]


# ---- the Phone page's status lines ----
def test_the_checks_say_what_to_do_when_tailscale_is_missing(tmp_path):
    cfg = Config(remote_port=0)
    server = RemoteServer(None, MultiEvents(), cfg, PairedDevices(tmp_path / "d.json"), find_ip=lambda: None)
    assert not server.start()
    tailscale, link = phone_link_checks(cfg, server, find_ip=lambda: None)[:2]
    assert tailscale["ok"] is False and "Install Tailscale" in tailscale["fix"]
    assert link["ok"] is False and "Fix Tailscale first" in link["fix"]


def test_the_checks_when_the_link_is_off_but_tailscale_works(tmp_path):
    cfg = Config(remote_port=0)
    checks = phone_link_checks(cfg, None, find_ip=lambda: "100.1.2.3")
    assert [c["id"] for c in checks] == ["tailscale", "link"]
    assert checks[0]["ok"] is True and "100.1.2.3" in checks[0]["detail"]
    assert checks[1]["ok"] is False and "Turn on the phone link" in checks[1]["fix"]


def test_the_checks_when_everything_works(linked):
    s, _, _ = linked
    checks = {c["id"]: c for c in phone_link_checks(s.server.cfg, s.server, find_ip=lambda: "100.1.2.3")}
    assert checks["link"]["ok"] is True and str(s.server.port) in checks["link"]["detail"]
    assert checks["phone"]["ok"] is None and "No phone connected" in checks["phone"]["detail"]
    ws = s.connect()
    collect(ws, has("ready"))
    checks = {c["id"]: c for c in phone_link_checks(s.server.cfg, s.server, find_ip=lambda: "100.1.2.3")}
    assert checks["phone"]["ok"] is True and "Test phone" in checks["phone"]["detail"]


def test_with_no_phone_paired_the_page_says_to_pair_one(tmp_path):
    s = Setup(tmp_path)
    try:
        s.devices.revoke(s.device_id)
        phone = phone_link_checks(s.server.cfg, s.server, find_ip=lambda: "100.1.2.3")[2]
        assert phone["ok"] is None and "No phone paired yet" in phone["detail"] and "Pair a new phone" in phone["fix"]
    finally:
        s.close()
