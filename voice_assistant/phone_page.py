"""The "Phone" page of the settings window: switch the phone link on, pair a phone by scanning a QR code, see who is paired, and see at a glance
what is wrong when it does not work. It only talks to the server through the functions it is given, so it can be tested without a real one."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

import segno
from PySide6.QtCore import QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from .remote import format_code, phone_link_checks, tailscale_ip

QUIET_MODULES = 4  # the blank border a QR scanner needs
REPLY_CHOICES = (("phone", "On my phone"), ("both", "Phone and PC"), ("pc", "On the PC only"))
GUIDE = Path(__file__).resolve().parents[1] / "docs" / "PHONE.md"


def qr_matrix(text: str) -> list[list[bool]]:
    """The dark/light squares of a QR code for `text` (error correction M: robust, still easy to scan from a monitor)."""
    return [[bool(cell) for cell in row] for row in segno.make(text, error="m", boost_error=False).matrix]


def ago(timestamp: float, now: float | None = None) -> str:
    if not timestamp:
        return "never"
    seconds = max(0, int((now if now is not None else time.time()) - timestamp))
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} days ago"


def countdown(seconds: int) -> str:
    return f"{seconds // 60}:{seconds % 60:02d}"


class QrWidget(QWidget):
    def __init__(self, text: str = "", size: int = 280):
        super().__init__()
        self.matrix: list[list[bool]] = []
        self.setFixedSize(size, size)
        self.set_text(text)

    def set_text(self, text: str) -> None:
        self.matrix = qr_matrix(text) if text else []
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("white"))
        if not self.matrix:
            return
        modules = len(self.matrix) + 2 * QUIET_MODULES
        cell = max(1, min(self.width(), self.height()) // modules)  # whole pixels: crisp edges scan better
        left = (self.width() - cell * modules) // 2 + QUIET_MODULES * cell
        top = (self.height() - cell * modules) // 2 + QUIET_MODULES * cell
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("black"))
        for y, row in enumerate(self.matrix):
            for x, dark in enumerate(row):
                if dark:
                    painter.drawRect(left + x * cell, top + y * cell, cell, cell)


class PairDialog(QDialog):
    """Shows the QR code, counts down, and closes itself when the phone has paired."""

    def __init__(self, begin: Callable[[], tuple], cancel: Callable[[], None], events: QObject | None = None, parent=None):
        super().__init__(parent)
        self.begin, self.cancel_pairing = begin, cancel
        self.setWindowTitle("Pair a phone")
        layout = QVBoxLayout(self)
        steps = QLabel("1. Open the Voice Assistant app on your phone.\n2. Tap \"Scan the code\".\n3. Point the camera at this picture.", wordWrap=True)
        self.qr = QrWidget()
        self.code = QLabel(alignment=Qt.AlignCenter)
        self.code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.address = QLabel(alignment=Qt.AlignCenter)
        self.address.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.timer_label = QLabel(alignment=Qt.AlignCenter)
        self.status = QLabel("Waiting for your phone...", alignment=Qt.AlignCenter, wordWrap=True)
        self.status.setStyleSheet("font-weight: 600;")
        self.copy_button = QPushButton("Copy the pairing text (to paste into the app)")
        self.again_button = QPushButton("Show a new code")
        close = QPushButton("Close")
        self.copy_button.clicked.connect(lambda: QApplication.clipboard().setText(self.payload))
        self.again_button.clicked.connect(self.show_code)
        close.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addWidget(self.again_button)
        row.addWidget(close)
        layout.addWidget(steps)
        layout.addWidget(self.qr, 0, Qt.AlignHCenter)
        for w in (self.code, self.address, self.timer_label, self.status, self.copy_button):
            layout.addWidget(w)
        layout.addLayout(row)
        self.payload, self.seconds, self.paired = "", 0, False
        self.ticker = QTimer(self)
        self.ticker.timeout.connect(self.tick)
        self.ticker.start(1000)
        self.show_code()

    def show_code(self) -> None:
        offer, self.payload = self.begin()
        self.seconds, self.paired = int(max(0, offer.expires_at - time.time())) or 300, False
        self.qr.set_text(self.payload)
        self.code.setText(f"<span style='font-size:18pt; letter-spacing:2px;'>{format_code(offer.code)}</span><br><small>or type the address and this code in the app</small>")
        try:
            data = json.loads(self.payload)
            self.address.setText(f"PC address: {data['host']}:{data['port']}")
        except (ValueError, KeyError):
            self.address.setText("")
        self.status.setText("Waiting for your phone...")
        self.again_button.setVisible(False)
        self.copy_button.setEnabled(True)
        self.tick(count=False)

    def tick(self, count: bool = True) -> None:
        if self.paired:
            return
        if count:
            self.seconds = max(0, self.seconds - 1)
        if self.seconds <= 0:
            self.status.setText("This code has expired.")
            self.timer_label.setText("")
            self.again_button.setVisible(True)
            self.copy_button.setEnabled(False)
        else:
            self.timer_label.setText(f"Expires in {countdown(self.seconds)}")

    def phone_paired(self, name: str) -> None:
        self.paired = True
        self.status.setText(f"Paired with {name}. You can use the assistant from your phone now.")
        self.timer_label.setText("")
        self.copy_button.setEnabled(False)
        QTimer.singleShot(2500, self.accept)

    def done(self, result: int) -> None:
        self.ticker.stop()
        self.cancel_pairing()
        super().done(result)


class PhonePanel(QWidget):
    def __init__(self, cfg, get_server, start_remote, stop_remote, set_field, devices, find_ip=tailscale_ip, guide: Path = GUIDE, parent=None):
        super().__init__(parent)
        self.cfg, self.get_server, self.start_remote, self.stop_remote = cfg, get_server, start_remote, stop_remote
        self.set_field, self.devices, self.guide = set_field, devices, guide
        self._find_ip, self._ip, self._ip_checked = find_ip, None, 0.0
        self.dialog: PairDialog | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("<b>Use the assistant from your phone</b><br>Talk to it, show it things with the camera, and change its settings from "
                                "anywhere. Your PC does the work; your phone is the microphone, speaker and camera.", wordWrap=True))
        self.checks = QLabel(wordWrap=True, textFormat=Qt.RichText)
        self.checks.setFrameShape(QFrame.StyledPanel)
        self.checks.setMargin(8)
        layout.addWidget(self.checks)
        buttons = QHBoxLayout()
        self.power = QPushButton()
        self.pair = QPushButton("Pair a new phone")
        self.power.clicked.connect(self.toggle_link)
        self.pair.clicked.connect(self.open_pairing)
        buttons.addWidget(self.power)
        buttons.addWidget(self.pair)
        layout.addLayout(buttons)
        layout.addWidget(QLabel("<b>Your phones</b>"))
        self.list = QListWidget()
        self.list.setMaximumHeight(110)
        layout.addWidget(self.list)
        self.remove = QPushButton("Remove the selected phone")
        self.remove.clicked.connect(self.remove_selected)
        layout.addWidget(self.remove)
        layout.addWidget(QLabel("<b>Options</b>"))
        self.output = QComboBox()
        for value, label in REPLY_CHOICES:
            self.output.addItem(label, value)
        self.output.setCurrentIndex(max(0, self.output.findData(cfg.remote_output)))
        self.output.currentIndexChanged.connect(lambda _i: self.set_field("remote_output", self.output.currentData()))
        layout.addWidget(QLabel("Where replies play:"))
        layout.addWidget(self.output)
        self.safety = QCheckBox("Let my phone change safety settings")
        self.safety.setToolTip(
            "Off: settings that widen what the assistant may do (shell commands, web access, the 'ask me first' questions, cloud models, folders...) are locked in the "
            "phone app. On: they can be changed from your paired phone, which still asks for your fingerprint or PIN first.")
        self.safety.setChecked(bool(cfg.remote_allow_safety_changes))
        self.safety.toggled.connect(lambda v: self.set_field("remote_allow_safety_changes", v))
        layout.addWidget(self.safety)
        guide_button = QPushButton("Open the setup guide")
        guide_button.clicked.connect(self.open_guide)
        layout.addWidget(guide_button)
        self.refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2500)

    def find_ip(self) -> str | None:
        """Looking for Tailscale runs a command, so remember the answer for a few seconds instead of asking on every refresh."""
        if time.monotonic() - self._ip_checked > 8:
            self._ip, self._ip_checked = self._find_ip(), time.monotonic()
        return self._ip

    # ---- what is on screen ----
    def refresh(self) -> None:
        server = self.get_server()
        running = server is not None and server.running
        marks = {True: ("&#10003;", "#3a9d5d"), False: ("&#10007;", "#d9534f"), None: ("&ndash;", "#8a8a8a")}
        lines = []
        for c in phone_link_checks(self.cfg, server, self.find_ip):
            mark, colour = marks[c["ok"]]
            fix = f"<br><small style='color:#a0a0a0'>{c['fix']}</small>" if c["fix"] else ""
            lines.append(f"<span style='color:{colour}; font-weight:700'>{mark}</span> <b>{c['title']}</b>: {c['detail']}{fix}")
        self.checks.setText("<br>".join(lines))
        self.power.setText("Turn off the phone link" if running else "Turn on the phone link")
        self.pair.setEnabled(running)
        self.fill_list()

    def fill_list(self) -> None:
        selected = self.list.currentItem().data(Qt.UserRole) if self.list.currentItem() else None
        self.list.clear()
        phones = self.devices.list()
        for d in sorted(phones, key=lambda d: d["created"]):
            self.list.addItem(f"{d['name']}   (last connected {ago(d['last_seen'])})")
            row = self.list.item(self.list.count() - 1)
            row.setData(Qt.UserRole, d["id"])
            if d["id"] == selected:
                self.list.setCurrentItem(row)
        if not phones:
            self.list.addItem("No phones paired yet.")
            self.list.item(0).setFlags(Qt.NoItemFlags)
        self.remove.setEnabled(bool(phones))

    # ---- actions ----
    def toggle_link(self) -> None:
        server = self.get_server()
        if server is not None and server.running:
            self.set_field("remote_enabled", False)
            self.stop_remote()
        else:
            self.set_field("remote_enabled", True)
            self.start_remote()
        self.refresh()

    def open_pairing(self) -> None:
        server = self.get_server()
        if server is None or not server.running:
            return
        self.dialog = PairDialog(server.begin_pairing, self.devices.cancel_pairing, parent=self)
        self.dialog.exec()
        self.dialog = None
        self.refresh()

    def on_phone_event(self, event: dict) -> None:
        if event.get("event") == "paired" and self.dialog is not None:
            self.dialog.phone_paired(str(event.get("name") or "your phone"))
        self.refresh()

    def remove_selected(self) -> None:
        item = self.list.currentItem()
        device_id = item.data(Qt.UserRole) if item else None
        if not device_id:
            return
        name = item.text().split("   (")[0]
        ask = QMessageBox.question(self, "Remove phone", f"Remove {name}? It will need to be paired again before it can connect.")
        if ask == QMessageBox.Yes:
            self.devices.revoke(device_id)
            self.refresh()

    def open_guide(self) -> None:
        if self.guide.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.guide)))
        else:
            QMessageBox.information(self, "Setup guide", "1. Install Tailscale on this PC and your phone and sign in to the same account.\n"
                                    "2. Turn on the phone link here, then press Pair a new phone.\n3. In the phone app, tap Scan the code.")
