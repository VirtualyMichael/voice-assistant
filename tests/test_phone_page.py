import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QMessageBox

from voice_assistant.config import Config
from voice_assistant.phone_page import PairDialog, PhonePanel, QrWidget, ago, countdown, qr_matrix
from voice_assistant.remote import PairedDevices, format_code

app = QApplication.instance() or QApplication([])


class FakeServer:
    def __init__(self, devices, running=True, error="", connected=None):
        self.devices, self.running, self.error = devices, running, error
        self.bound_host, self.port = "100.1.2.3", 47830
        self.client = SimpleNamespace(alive=True, device_id=connected) if connected else None

    def begin_pairing(self):
        offer = self.devices.begin_pairing()
        return offer, json.dumps({"v": 2, "host": self.bound_host, "port": self.port, "code": offer.code, "name": "TESTPC"}, separators=(",", ":"))


@pytest.fixture
def devices(tmp_path):
    return PairedDevices(tmp_path / "devices.json")


def panel_for(devices, server, cfg=None, calls=None, ip="100.1.2.3"):
    calls = calls if calls is not None else []
    cfg = cfg or Config()
    state = {"server": server}

    def start():
        calls.append("start")
        state["server"] = FakeServer(devices)
        return True

    def stop():
        calls.append("stop")
        state["server"] = None

    panel = PhonePanel(cfg, lambda: state["server"], start, stop, lambda k, v: (calls.append((k, v)), setattr(cfg, k, v)), devices, find_ip=lambda: ip)
    return panel, calls, state


# ---- small helpers ----
def test_times_are_shown_the_way_a_person_says_them():
    assert ago(0) == "never"
    now = 1_000_000
    assert ago(now - 30, now) == "just now"
    assert ago(now - 600, now) == "10 min ago"
    assert ago(now - 7200, now) == "2 h ago"
    assert ago(now - 3 * 86400, now) == "3 days ago"
    assert countdown(300) == "5:00" and countdown(65) == "1:05" and countdown(0) == "0:00"


def test_the_qr_code_is_a_square_that_stays_easy_to_scan():
    payload = json.dumps({"v": 2, "host": "100.126.148.55", "port": 47830, "code": "ABCDEFGHJK", "name": "MICHAELSPC"}, separators=(",", ":"))
    matrix = qr_matrix(payload)
    assert len(matrix) == len(matrix[0]) >= 21
    assert len(matrix) <= 49  # up to QR version 8: big enough modules to scan from across a desk
    assert matrix == qr_matrix(payload) and matrix != qr_matrix(payload.replace("ABCDEFGHJK", "KJHGFEDCBA"))
    QrWidget(payload).grab()  # paints without error


# ---- the page ----
def test_with_the_link_off_the_page_says_what_to_do(devices):
    panel, calls, _ = panel_for(devices, None)
    text = panel.checks.text()
    assert "Tailscale" in text and "Phone link" in text and "Turn on the phone link" in text
    assert panel.power.text() == "Turn on the phone link" and not panel.pair.isEnabled()
    assert panel.list.count() == 1 and "No phones paired yet" in panel.list.item(0).text() and not panel.remove.isEnabled()


def test_missing_tailscale_is_explained(devices):
    panel, _, _ = panel_for(devices, None, ip=None)
    assert "Not running on this PC" in panel.checks.text() and "Install Tailscale" in panel.checks.text()


def test_the_power_button_turns_the_link_on_and_off_and_remembers_it(devices):
    panel, calls, state = panel_for(devices, None)
    panel.power.click()
    assert calls == [("remote_enabled", True), "start"]
    assert panel.power.text() == "Turn off the phone link" and panel.pair.isEnabled()
    panel.power.click()
    assert calls[2:] == [("remote_enabled", False), "stop"] and panel.power.text() == "Turn on the phone link"


def test_the_list_shows_paired_phones_and_removing_one_asks_first(devices, monkeypatch):
    device_id, token = devices.pair("Pixel 8 Pro")
    panel, _, _ = panel_for(devices, FakeServer(devices))
    assert panel.list.count() == 1 and panel.list.item(0).text().startswith("Pixel 8 Pro") and "never" in panel.list.item(0).text()
    panel.list.setCurrentRow(0)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    panel.remove.click()
    assert devices.verify(token) == device_id  # said no: still paired
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    panel.remove.click()
    assert devices.verify(token) is None and "No phones paired yet" in panel.list.item(0).text()


def test_the_options_change_the_assistant_settings(devices):
    cfg = Config(remote_output="phone")
    panel, calls, _ = panel_for(devices, FakeServer(devices), cfg=cfg)
    panel.output.setCurrentIndex(panel.output.findData("both"))
    panel.safety.setChecked(True)
    assert ("remote_output", "both") in calls and ("remote_allow_safety_changes", True) in calls
    assert cfg.remote_output == "both" and cfg.remote_allow_safety_changes is True


def test_a_connected_phone_shows_up(devices):
    device_id, _ = devices.pair("Pixel 8 Pro")
    panel, _, _ = panel_for(devices, FakeServer(devices, connected=device_id))
    assert "Pixel 8 Pro is connected" in panel.checks.text()


# ---- the pairing dialog ----
def test_the_dialog_shows_the_code_counts_down_and_closes_on_success(devices):
    server = FakeServer(devices)
    cancelled = []
    dialog = PairDialog(server.begin_pairing, lambda: (cancelled.append(True), devices.cancel_pairing()))
    assert dialog.qr.matrix and format_code(devices._offer.code) in dialog.code.text()
    assert "Expires in" in dialog.timer_label.text() and dialog.status.text() == "Waiting for your phone..."
    assert dialog.address.text() == "PC address: 100.1.2.3:47830"  # what to type if the camera cannot scan
    first = dialog.seconds
    dialog.tick()
    assert dialog.seconds == first - 1
    dialog.phone_paired("Pixel 8 Pro")
    assert "Paired with Pixel 8 Pro" in dialog.status.text() and dialog.paired
    dialog.reject()
    assert cancelled and devices.pairing_seconds_left() == 0  # closing never leaves a code open


def test_an_expired_code_offers_a_new_one(devices):
    server = FakeServer(devices)
    dialog = PairDialog(server.begin_pairing, devices.cancel_pairing)
    old = devices._offer.code
    dialog.seconds = 1
    dialog.tick()
    assert "expired" in dialog.status.text() and not dialog.again_button.isHidden() and not dialog.copy_button.isEnabled()
    dialog.again_button.click()
    assert devices._offer.code != old and dialog.again_button.isHidden() and dialog.status.text() == "Waiting for your phone..."
    dialog.reject()


def test_the_copy_button_puts_the_pairing_text_on_the_clipboard(devices):
    server = FakeServer(devices)
    dialog = PairDialog(server.begin_pairing, devices.cancel_pairing)
    dialog.copy_button.click()
    assert json.loads(QApplication.clipboard().text())["code"] == devices._offer.code
    dialog.reject()


def test_a_paired_event_reaches_the_open_dialog_through_the_panel(devices):
    panel, _, _ = panel_for(devices, FakeServer(devices))
    server = panel.get_server()
    panel.dialog = PairDialog(server.begin_pairing, devices.cancel_pairing)
    panel.on_phone_event({"event": "paired", "name": "Pixel"})
    assert "Paired with Pixel" in panel.dialog.status.text()
    panel.dialog.reject()
