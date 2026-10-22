"""PySide6 window and wiring of real components."""
from __future__ import annotations

import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QInputDialog, QListWidget, QListWidgetItem, QTabWidget, QComboBox, QDoubleSpinBox, QFileDialog, QPlainTextEdit, QSpinBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from .agent import Agent
from .attachments import Inbox, build_attachment_tools
from .audio_io import AudioIO, list_devices
from .hardware import detect_gpus, recommend
from .config import INTERRUPT_MODES, LISTEN_MODES, Config, config_dir, load_config, save_config
from .mcp_client import McpHub, read_mcp_json
from .memory import ConversationStore
from .pdf_tools import PdfStore
from .skill_tools import build_skill_tools
from .skills import SkillLibrary
from .metrics import TurnMetrics
from .models import AccurateRecognizer, ModelsMissing, SherpaRecognizer, SherpaWakeWord, load_synth
from .permissions import FolderPolicy
from .routing import double_voice_warnings, listen_routes
from .delegate import UsageLog, build_delegate_tools
from .computer import build_computer_tools
from .extras import Audio, build_extra_tools
from .music import build_music_tools, open_users_youtube_music
from .ytm_bridge import YtmBridge, extension_folder, make_music_actions, make_player, make_playlist_player
from .ytm_library import Library
from .ytm_browser import YtmBrowser, make_judge
from .screen import list_windows
from .profile import Profile, build_profile_tools
from .screen import build_screen_tools
from .smart_home import HOME_TOOL_NAMES, build_home_tools, make_govee
from .diagnostics import CallLog, build_diagnostic_tools
from .ducking import Ducker
from .interpreter import Interpreter, build_interpreter_tools
from .vrchat import VRCHAT_TOOL_NAMES, VRChat, build_vrchat_tools
from .vrchat_menu import AvatarNotes
from .providers import PROVIDERS, URL_FIELD, get_key, key_env, make_llm, model_for, needs_key, save_key, set_model
from .session import Session, SessionEvents
from .speaker import Speaker
from .setup_wizard import run_wizard
from . import glass
from .theme import ACCENTS, ICON_FONT, STATE_COLORS, STYLE, build_style, window_tint
from .agent_tools import Timers, build_agent_tools, capabilities_prompt
from .apps import AppCatalog
from .tools import build_file_tools
from .vr import VrWatcher


JARVIS_PERSONA = (
    " Personality: a calm, composed, dry-witted British butler AI in the spirit of a classic fictional assistant. Polite and precise, "
    "with understated humour, never gushing. Keep replies short and spoken."
)


class Bridge(QObject):
    state = Signal(str)
    partial = Signal(str)
    user = Signal(str)
    delta = Signal(str)
    done = Signal(str)
    error = Signal(str)
    confirm = Signal(str, object)
    confirm_close = Signal(object)
    vr = Signal(bool)
    tool = Signal(str)
    vr_note = Signal(str)
    note = Signal(str)
    apps_count = Signal(int)
    route = Signal(str, bool)


def icon_button(glyph: str, tip: str, checkable: bool = False) -> QPushButton:
    """Round icon-only button (Segoe Fluent Icons glyph) with the name in its tooltip and accessible name."""
    b = QPushButton(glyph, objectName="icon", checkable=checkable)
    b.setToolTip(tip)
    b.setAccessibleName(tip.split(":")[0])
    return b


class PasteEntry(QLineEdit):
    """The message box. Pasting a picture or copied files attaches them to the chat instead of typing something."""
    attached = Signal(list)
    picture = Signal(bytes)

    def keyPressEvent(self, e) -> None:
        if e.matches(QKeySequence.Paste):
            clip = QApplication.clipboard()
            md = clip.mimeData()
            if md is not None and md.hasUrls() and any(u.isLocalFile() for u in md.urls()):
                self.attached.emit([u.toLocalFile() for u in md.urls() if u.isLocalFile()])
                return
            if md is not None and md.hasImage():
                img = clip.image()
                if not img.isNull():
                    data = QByteArray()  # must outlive the buffer
                    buf = QBuffer(data)
                    buf.open(QIODevice.WriteOnly)
                    img.save(buf, "PNG")
                    buf.close()
                    self.picture.emit(bytes(data))
                    return
        super().keyPressEvent(e)


class _OpaqueDialogs(QObject):
    """Dialogs keep the solid theme even when the main window is frosted glass (otherwise they would be see-through)."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Show and isinstance(obj, QDialog) and not obj.property("opaque_done"):
            obj.setProperty("opaque_done", True)
            obj.setStyleSheet(STYLE)
        return False


class _Holder:
    def __init__(self):
        self.event, self.result = threading.Event(), False
        self.box, self.voice = None, None


class QtEvents(SessionEvents):
    def __init__(self, b: Bridge):
        self.b = b
        self.on_state = self.on_delta = self.on_done = None  # optional taps: music ducking and interpreter mode

    def state(self, s):
        self.b.state.emit(s.value)
        if self.on_state:
            self.on_state(s.value)
    def partial(self, t): self.b.partial.emit(t)
    def user_final(self, t): self.b.user.emit(t)
    def assistant_delta(self, t):
        self.b.delta.emit(t)
        if self.on_delta:
            self.on_delta(t)

    def assistant_done(self, m: TurnMetrics):
        self.b.done.emit(m.describe())
        if self.on_done:
            self.on_done()
    def error(self, msg): self.b.error.emit(msg)


def _init_com() -> None:
    try:
        import comtypes

        comtypes.CoInitialize()
    except Exception:
        pass


class TabbedForm:
    """Presents the old single-form API (addRow / insertRow) but spreads rows over scrollable tabs."""

    def __init__(self, tabs: QTabWidget, names: list[str]):
        self.forms: dict[str, QFormLayout] = {}
        for n in names:
            page = QWidget()
            form = QFormLayout(page)
            form.setContentsMargins(10, 12, 10, 10)
            form.setVerticalSpacing(8)
            form.setRowWrapPolicy(QFormLayout.WrapAllRows)  # label above its field: nothing is cut off in a narrow window
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            tabs.addTab(scroll, n)
            self.forms[n] = form
        self.current = names[0]

    def use(self, name: str) -> "TabbedForm":
        self.current = name
        return self

    def addRow(self, *args) -> None:
        self.forms[self.current].addRow(*args)

    def insertRow(self, index: int, *args) -> None:
        self.forms[self.current].insertRow(index, *args)


class Chat(QScrollArea):
    """Scrolling list of message bubbles; the assistant bubble grows as text streams in."""

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self.col = QVBoxLayout(body)
        self.col.setContentsMargins(4, 4, 4, 4)
        self.col.setSpacing(8)
        self.col.addStretch(1)
        self.setWidget(body)
        self._ai: QLabel | None = None
        self._labels: list[QLabel] = []
        bar = self.verticalScrollBar()
        bar.rangeChanged.connect(lambda _a, hi: bar.setValue(hi))

    def add(self, kind: str, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName(kind)
        label.setWordWrap(True)
        label.setTextFormat(Qt.PlainText)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setMaximumWidth(self._max_width())
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if kind == "bubbleUser":
            row.addStretch(1)
        row.addWidget(label)
        if kind in ("bubbleNote", "bubbleErr"):
            label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)  # notes use the full width instead of a narrow column
        elif kind != "bubbleUser":
            row.addStretch(1)
        self.col.insertLayout(self.col.count() - 1, row)
        self._labels.append(label)
        self._ai = None if kind != "bubbleAI" else label
        return label

    def clear(self) -> None:
        while self.col.count() > 1:  # keep the trailing stretch
            item = self.col.takeAt(0)
            if item.layout():
                while item.layout().count():
                    w = item.layout().takeAt(0).widget()
                    if w:
                        w.hide()  # vanish at once; deleteLater alone waits for the event loop
                        w.setParent(None)
                        w.deleteLater()
        self._labels.clear()
        self._ai = None

    def stream(self, delta: str) -> None:
        if self._ai is None:
            self.add("bubbleAI", "")
        self._ai.setText(self._ai.text() + delta)

    def end_stream(self) -> None:
        self._ai = None

    def _max_width(self) -> int:
        return max(int(self.viewport().width() * 0.82), 200)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        for lbl in self._labels:
            lbl.setMaximumWidth(self._max_width())


VR_SETTLE_S = 6.0  # the headset's audio devices can appear a few seconds after SteamVR starts


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config):
        super().__init__()
        self.setAttribute(Qt.WA_TranslucentBackground)  # lets the frosted backdrop show through
        self.setWindowTitle("Voice Assistant")
        self.resize(*(cfg.window_size or [440, 700]))
        self.cfg, self.bridge, self.policy = cfg, Bridge(), FolderPolicy(cfg.approved_folders)
        self.warnings: list[str] = []
        self.skills = SkillLibrary(cfg.skill_folders, cfg.disabled_skills)  # needed by the settings tabs built below
        self.profile, self.usage = Profile(), UsageLog()
        self._vr_gen = 0
        self._vr_apply_lock = threading.Lock()
        self._warned_routes: set[str] = set()

        self.status = QLabel(objectName="status")
        self.meter = QProgressBar(textVisible=False, maximumHeight=4)
        self.pin = icon_button("\uE718", "Pin on top: keep this window above other apps", checkable=True)
        self.mute = icon_button("\uE720", "Mute game mic: stop your voice going to the game", checkable=True)
        self.mute.toggled.connect(lambda m: self.mute.setText("\uE74F" if m else "\uE720"))
        new_chat = icon_button("\uE8F2", "New chat: forget the conversation so far and start fresh")
        self.mute.setVisible(cfg.mic_forward)
        gear = self._gear = icon_button("\uE713", "Settings", checkable=True)
        head = QHBoxLayout()
        head.addWidget(self.status)
        head.addStretch(1)
        head.addWidget(self.mute)
        head.addWidget(new_chat)
        head.addWidget(self.pin)
        head.addWidget(gear)

        self.inbox = Inbox(config_dir() / "attachments", on_change=self._attachments_changed)  # files, folders and pictures handed to the assistant
        self.setAcceptDrops(True)
        self.chat = Chat()
        self.partial = QLabel("", objectName="partial")
        self.partial.setWordWrap(True)
        self.latency = QLabel("", objectName="small")
        self.entry = PasteEntry(placeholderText="Message the assistant… (paste or drop files and pictures)")
        talk, stop = QPushButton("Talk", objectName="talk"), icon_button("\uE71A", "Stop: stop listening or speaking")
        self.attach_btn = icon_button("\uE723", "Attach: give the assistant files, a folder or pictures to learn from (you can also drop them here or paste a picture)")
        self.attach_label = QLabel("", objectName="small", wordWrap=True)
        self.attach_clear = icon_button("\uE711", "Remove attachments: forget everything attached to this chat")
        inp = QHBoxLayout()
        inp.addWidget(self.entry, 1)
        inp.addWidget(self.attach_btn)
        inp.addWidget(talk)
        inp.addWidget(stop)
        attach_row = QHBoxLayout()
        attach_row.addWidget(self.attach_label, 1)
        attach_row.addWidget(self.attach_clear)
        self.attach_label.setVisible(False)
        self.attach_clear.setVisible(False)

        self.settings = self._build_settings()
        self.settings.setVisible(False)

        col = QVBoxLayout()
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(8)
        col.addLayout(head)
        col.addWidget(self.meter)
        self.chat_page = QWidget()  # hidden while the settings page is open
        page = QVBoxLayout(self.chat_page)
        page.setContentsMargins(0, 0, 0, 0)
        page.setSpacing(8)
        page.addWidget(self.chat, 1)
        page.addWidget(self.partial)
        page.addLayout(inp)
        page.addLayout(attach_row)
        page.addWidget(self.latency)
        col.addWidget(self.settings, 1)
        col.addWidget(self.chat_page, 1)
        root = QWidget()
        root.setLayout(col)
        self.setCentralWidget(root)

        self._build_session()
        b = self.bridge
        b.state.connect(self._on_state)
        b.partial.connect(self.partial.setText)
        b.user.connect(self._on_user)
        b.delta.connect(self.chat.stream)
        b.done.connect(self._on_done)
        b.error.connect(self._on_error)
        b.confirm.connect(self._on_confirm)
        b.confirm_close.connect(self._close_confirm)
        talk.clicked.connect(self.session.activate)
        stop.clicked.connect(self.session.stop)
        gear.toggled.connect(self._show_settings)
        self.pin.toggled.connect(self._set_pin)
        new_chat.clicked.connect(self._new_chat)
        self.mute.toggled.connect(lambda m: setattr(self.audio, "forward_muted", m))
        self.entry.returnPressed.connect(self._send_text)
        self.entry.attached.connect(self._attach_paths)
        self.entry.picture.connect(self._attach_picture)
        self.attach_btn.clicked.connect(self._pick_files)
        self.attach_clear.clicked.connect(lambda: self.inbox.clear())
        self.timer = QTimer(self, interval=100)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        b.vr.connect(self._on_vr)
        b.vr_note.connect(self._on_vr_note)
        b.note.connect(lambda m: self.chat.add("bubbleNote", m))
        b.apps_count.connect(self._on_apps_count)
        b.route.connect(self._on_route)
        b.tool.connect(lambda m: self.chat.add("bubbleNote", "\u2699 " + m[:140]))
        self.vr_watcher = VrWatcher(b.vr.emit, cfg.vr_processes, cfg.vr_poll_s)
        if cfg.vr_autoswitch:
            self.vr_watcher.start()
        self._on_state("idle")
        self.setWindowOpacity(cfg.opacity)
        if cfg.always_on_top:
            self.pin.setChecked(True)
        self._apply_look()
        self._restore_chat()
        threading.Thread(target=self._check_routes, daemon=True).start()
        for w in self.warnings:
            self.chat.add("bubbleNote", w)
        if not cfg.approved_folders:
            self.chat.add("bubbleNote", "No approved folders yet: file tasks are refused until you add one (Settings).")

    # ---- settings panel --------------------------------------------------------
    def _build_settings(self) -> QFrame:
        cfg = self.cfg
        panel = QFrame(objectName="panel")
        tabs = QTabWidget()
        tabs.tabBar().hide()  # the sidebar list below drives the pages
        names = ["Voice", "Audio", "VR", "AI", "Skills", "Apps", "Home", "Memory", "Delegate", "Look", "General"]
        nav = QListWidget(objectName="nav")
        nav.setFixedWidth(96)
        nav.addItems(names)
        nav.setFocusPolicy(Qt.NoFocus)
        nav.currentRowChanged.connect(tabs.setCurrentIndex)
        nav.setCurrentRow(0)
        side = QHBoxLayout(panel)
        side.setContentsMargins(8, 8, 8, 8)
        side.setSpacing(6)
        side.addWidget(nav)
        side.addWidget(tabs, 1)
        form = TabbedForm(tabs, names)
        self._form = form
        form.use("Audio")
        duck = QCheckBox("Lower my music while the assistant listens and speaks")
        duck.setToolTip("Only music apps' own volume (browsers, Spotify, VLC...) is lowered, not the master volume, and it is restored afterwards")
        duck.setChecked(cfg.duck_music)
        duck.toggled.connect(lambda v: self._set_field("duck_music", v))
        form.addRow(duck)
        self.dev = {}
        for key, label, kind, optional in (
            ("input_device", "Microphone", "input", False),
            ("input_device_2", "2nd input (mixed in)", "input", True),
            ("output_device", "Speaker", "output", False),
            ("output_device_2", "2nd output (same audio)", "output", True),
        ):
            box = QComboBox()
            try:
                names = list_devices(kind)
            except Exception:
                names = []
            box.addItem("None" if optional else "System default", "")
            for n in names:
                box.addItem(n, n)
            cur = getattr(cfg, key)
            if cur and box.findData(cur) < 0:
                box.addItem(f"{cur} (not found)", cur)
            box.setCurrentIndex(max(box.findData(cur), 0))
            box.currentIndexChanged.connect(lambda _i, k=key, b=box: self._set_device(k, b.currentData()))
            self.dev[key] = box
            form.addRow(label, box)
        follow = QDoubleSpinBox(minimum=0, maximum=60, singleStep=1, value=cfg.follow_up_s, suffix=" s")
        follow.setSpecialValueText("Off")
        follow.setToolTip("After a spoken reply, keep the mic open this long for your next sentence. Off = push-to-talk each time.")
        follow.valueChanged.connect(lambda v: self._set_field("follow_up_s", float(v)))
        wait = QDoubleSpinBox(minimum=2, maximum=60, singleStep=1, value=cfg.listen_timeout_s, suffix=" s")
        wait.setToolTip("After the hotkey or wake word, how long to wait for you to start speaking.")
        wait.valueChanged.connect(lambda v: self._set_field("listen_timeout_s", float(v)))
        endsil = QDoubleSpinBox(minimum=0.4, maximum=3, singleStep=0.1, value=cfg.end_of_speech_s, suffix=" s")
        endsil.setToolTip("Silence that ends your sentence. Raise it if you get cut off mid-sentence (restart to apply).")
        endsil.valueChanged.connect(lambda v: self._set_field("end_of_speech_s", float(v)))
        acc = QCheckBox("Re-check sentences with the accurate recognizer")
        acc.setChecked(cfg.accurate_asr)
        acc.setToolTip("Needs the Parakeet model (setup guide > download). Restart to apply.")
        acc.toggled.connect(lambda v: self._set_field("accurate_asr", v))
        form.use("Voice")
        form.addRow("Keep listening after a reply", follow)
        form.addRow("Wait for me to start speaking", wait)
        form.addRow("End of sentence after silence", endsil)
        form.addRow(acc)
        listen, mode = QComboBox(), QComboBox()
        listen.addItems(LISTEN_MODES)
        listen.setCurrentText(cfg.listen_mode)
        listen.setToolTip("push: hotkey/Talk starts a turn. always: the mic is always open.")
        mode.addItems(INTERRUPT_MODES)
        mode.setCurrentText(cfg.interrupt_mode)
        mode.setToolTip("voice: speaking interrupts | hotkey: only the hotkey | ignore: reply always finishes")
        listen.currentTextChanged.connect(self._set_listen)
        mode.currentTextChanged.connect(self._set_mode)
        form.addRow("Listening", listen)
        form.addRow("Interrupt", mode)
        opacity = QSlider(Qt.Horizontal, minimum=40, maximum=100, value=int(cfg.opacity * 100))
        opacity.valueChanged.connect(self._set_opacity)
        opacity_row = QWidget()
        orow = QHBoxLayout(opacity_row)
        orow.setContentsMargins(0, 0, 0, 0)
        orow.addWidget(QLabel("Whole window opacity"))
        orow.addWidget(opacity, 1)
        form.use("General")
        lang = QLineEdit(cfg.main_language)
        lang.setToolTip("Screen translations are given in this language")
        lang.editingFinished.connect(lambda: self._set_field("main_language", lang.text().strip() or "English"))
        form.addRow("My language", lang)
        self.prov = QComboBox()
        for pid, label in PROVIDERS.items():
            self.prov.addItem(label, pid)
        self.prov.setCurrentIndex(max(self.prov.findData(cfg.llm_provider), 0))
        self.model_edit = QLineEdit(model_for(cfg))
        self.model_edit.setPlaceholderText("model name")
        key_btn = QPushButton("Set API key\u2026")
        key_btn.setToolTip("Stored in Windows Credential Manager, never in the settings file")
        self.prov_status = QLabel("", objectName="small")
        self.prov.currentIndexChanged.connect(lambda _i: self._set_provider(self.prov.currentData()))
        self.model_edit.editingFinished.connect(self._set_model_name)
        key_btn.clicked.connect(self._ask_key)
        form.use("AI")
        form.addRow("AI model", self.prov)
        form.addRow("Model name", self.model_edit)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("server address, e.g. http://localhost:11434/v1")
        self.url_edit.editingFinished.connect(self._set_url)
        form.addRow("Server address", self.url_edit)
        self._show_url()
        form.addRow(key_btn)
        form.addRow(self.prov_status)
        remember = QCheckBox("Remember our conversation between sessions")
        remember.setChecked(cfg.remember_conversation)
        remember.toggled.connect(self._set_remember)
        form.addRow(remember)
        web = QCheckBox("Let the agent search/read the web")
        web.setChecked(cfg.allow_web)
        web.toggled.connect(lambda v: self._set_field("allow_web", v))
        shell = QCheckBox("Let the agent run PowerShell (asks each time)")
        shell.setChecked(cfg.allow_shell)
        shell.toggled.connect(lambda v: self._set_field("allow_shell", v))
        form.addRow(QLabel("Agent abilities (restart to apply):", objectName="small"))
        form.addRow(web)
        form.addRow(shell)
        wake, phrase = QCheckBox("Enabled (restart to apply)"), QLineEdit(cfg.wake_phrase)
        wake.setChecked(cfg.wake_word_enabled)
        wake.toggled.connect(lambda v: self._set_field("wake_word_enabled", v))
        phrase.editingFinished.connect(lambda: self._set_field("wake_phrase", phrase.text().strip() or "hey assistant"))
        vapprove = QCheckBox("Read confirmations aloud; answer by saying yes or no")
        vapprove.setToolTip("The on-screen Yes/No still works; whichever answers first wins. Unclear or no answer counts as no.")
        vapprove.setChecked(cfg.voice_approval)
        vapprove.toggled.connect(lambda v: self._set_field("voice_approval", v))
        own_voice, clear_voice = QPushButton("Use my own voice model\u2026"), QPushButton("Back to built-in voices")
        own_voice.setToolTip("A folder with a sherpa-onnx VITS/Piper voice: model .onnx, tokens.txt and espeak-ng-data. Restart to apply.")
        own_voice.clicked.connect(self._pick_voice_folder)
        clear_voice.clicked.connect(lambda: self._set_field("custom_voice_dir", ""))
        persona = QCheckBox("Jarvis-style butler personality")
        persona.setToolTip("Calm, dry-witted British butler manner. Pair it with the George or Lewis (UK) voice.")
        persona.setChecked(cfg.persona_jarvis)
        persona.toggled.connect(self._set_persona)
        self._persona_cb = persona
        jarvis = QPushButton("Apply Jarvis-style preset")
        jarvis.setToolTip("British male voice (George), butler personality, wake phrase \"Jarvis\". A stand-in, not the film voice.")
        jarvis.clicked.connect(self._apply_jarvis_preset)
        form.use("Voice")
        form.addRow(jarvis)
        form.addRow(persona)
        form.addRow(vapprove)
        form.addRow(own_voice)
        form.addRow(clear_voice)
        sens = QSlider(Qt.Horizontal, minimum=0, maximum=100, value=self._sens_from_threshold(cfg.wake_threshold))
        sens.setToolTip("Right: reacts more easily (but may wake on other sounds)")
        sens.valueChanged.connect(self._set_wake_sensitivity)
        test_wake = QPushButton("Test wake word (10 seconds)")
        test_wake.setToolTip("Say your wake phrase a few times; it reports your mic level, how often it caught you and what it heard")
        test_wake.clicked.connect(self._test_wake)
        form.addRow("Wake word", wake)
        form.addRow("Wake phrase", phrase)
        form.addRow("Wake sensitivity", sens)
        form.addRow(test_wake)
        reserve = QDoubleSpinBox(minimum=0, maximum=64, singleStep=0.5, value=cfg.vram_reserve_gb, suffix=" GB")
        info = QLabel(wordWrap=True)
        gpus = detect_gpus()

        def show_profile() -> None:
            if gpus:
                info.setText(f"{gpus[0].name}: " + recommend(gpus[0].vram_gb, reserve.value()))
            else:
                info.setText("No GPU detected; use a small model or a cloud endpoint.")

        reserve.valueChanged.connect(lambda v: (self._set_field("vram_reserve_gb", v), show_profile()))
        show_profile()
        form.use("AI")
        form.addRow("VRAM reserve", reserve)
        form.addRow(info)
        fwd = QCheckBox("Send my mic to the 2nd output (game voice)")
        fwd.setToolTip("Replaces Windows 'Listen to this device'. Follows the VR mic when VR auto-switch is active.")
        fwd.setChecked(cfg.mic_forward)
        fwd.toggled.connect(self._set_forward)
        form.use("VR")
        form.addRow(fwd)
        self.route_status = QLabel("", objectName="small")
        self.route_status.setWordWrap(True)
        sound_btn, recheck = QPushButton("Open Windows sound settings"), QPushButton("Check for double voice")
        sound_btn.clicked.connect(self._open_sound_settings)
        recheck.clicked.connect(lambda: threading.Thread(target=self._check_routes, daemon=True).start())
        route_row = QHBoxLayout()
        route_row.addWidget(sound_btn)
        route_row.addWidget(recheck)
        form.addRow(self.route_status)
        form.addRow(route_row)
        vr_on = QCheckBox("Switch devices while SteamVR is running")
        vr_on.setChecked(cfg.vr_autoswitch)
        form.addRow(vr_on)
        self.vr_box = QWidget()  # hidden until auto-switch is on
        vr_form = QFormLayout(self.vr_box)
        vr_form.setContentsMargins(0, 0, 0, 0)
        for key, label, kind in (("vr_input_device", "VR microphone", "input"), ("vr_output_device", "VR speaker", "output")):
            box = QComboBox()
            try:
                names = list_devices(kind)
            except Exception:
                names = []
            box.addItem("Same as normal", "")
            for n in names:
                box.addItem(n, n)
            cur = getattr(cfg, key)
            if cur and box.findData(cur) < 0:
                box.addItem(f"{cur} (not found)", cur)
            box.setCurrentIndex(max(box.findData(cur), 0))
            box.currentIndexChanged.connect(lambda _i, k=key, b=box: self._set_vr_device(k, b.currentData()))
            vr_form.addRow(label, box)
        ann = QCheckBox("Say a short confirmation when switching")
        ann.setChecked(cfg.vr_announce)
        ann.toggled.connect(lambda v: self._set_field("vr_announce", v))
        vr_form.addRow(ann)
        self.vr_status = QLabel("", objectName="small")
        vr_form.addRow(self.vr_status)
        self.vr_box.setVisible(cfg.vr_autoswitch)
        form.addRow(self.vr_box)
        vr_on.toggled.connect(self._set_vr_autoswitch)
        vrc = QCheckBox("Control VRChat by voice (chatbox and avatar switches over OSC)")
        vrc.setChecked(cfg.vrchat_enabled)
        vrc.toggled.connect(lambda v: self._set_home("vrchat_enabled", v))
        form.addRow(vrc)
        form.addRow(QLabel("Needs OSC switched on in VRChat: Action Menu > Options > OSC > Enabled. Setup and what you can say: docs/VRCHAT.md in the repository.", objectName="small", wordWrap=True))
        form.addRow(QLabel("Avatar memory: what each avatar's menu names mean and which switches work backwards, one file per avatar (matched by the avatar id VRChat reports). Open one to edit it by hand; it applies at once.", objectName="small", wordWrap=True))
        self.avatar_box = QComboBox()
        self.avatar_box.setToolTip("Avatars the assistant has notes for")
        avatar_edit = QPushButton("Edit file")
        avatar_edit.setToolTip("Open this avatar's notes (a JSON file) in your editor")
        avatar_folder, avatar_refresh = QPushButton("Open folder"), QPushButton("Refresh")
        row = QHBoxLayout()
        for w in (self.avatar_box, avatar_edit, avatar_folder, avatar_refresh):
            row.addWidget(w)
        form.addRow(row)
        avatar_edit.clicked.connect(self._edit_avatar_file)
        avatar_folder.clicked.connect(self._open_avatar_folder)
        avatar_refresh.clicked.connect(self._refresh_avatar_files)
        self._refresh_avatar_files()
        form.use("Skills")
        form.addRow(QLabel("Skills are folders with a SKILL.md (the Bionic/Claude format). Add a skill folder, or a folder of skills.", objectName="small", wordWrap=True))
        self.skills_list = QListWidget()
        self.skills_list.setMaximumHeight(110)
        self.skills_list.itemChanged.connect(self._toggle_skill)
        self.skill_folders_list = QListWidget()
        self.skill_folders_list.setMaximumHeight(60)
        self.skills_status = QLabel("", objectName="small", wordWrap=True)
        add_sk, rem_sk, rescan_sk = QPushButton("Add folder\u2026"), QPushButton("Remove folder"), QPushButton("Rescan")
        add_sk.clicked.connect(self._add_skill_folder)
        rem_sk.clicked.connect(self._remove_skill_folder)
        rescan_sk.clicked.connect(self._rescan_skills)
        sk_row = QHBoxLayout()
        for w in (add_sk, rem_sk, rescan_sk):
            sk_row.addWidget(w)
        mcp_btn = QPushButton("Import tool servers (MCP) from LM Studio\u2026")
        mcp_btn.setToolTip("Skills such as a science tutor may need an MCP server; import it from LM Studio/Bionic's mcp.json")
        mcp_btn.clicked.connect(self._import_mcp)
        form.addRow(self.skills_list)
        form.addRow(self.skill_folders_list)
        form.addRow(sk_row)
        form.addRow(self.skills_status)
        form.addRow(mcp_btn)
        self._fill_skills()
        form.use("Apps")
        self.apps_count = QLabel("Scanning installed apps\u2026", objectName="small")
        ask = self.ask_apps = QCheckBox("Ask before opening an app")
        ask.setChecked(cfg.confirm_launch)
        ask.toggled.connect(lambda v: self._set_field("confirm_launch", v))
        self.apps_list = QListWidget()
        self.apps_list.setMaximumHeight(80)
        self.apps_list.setToolTip("Your own shortcuts. Everything in the Start menu is found automatically.")
        self._fill_apps()
        add_app, rem_app, rescan = QPushButton("Add app\u2026"), QPushButton("Remove"), QPushButton("Rescan")
        add_app.clicked.connect(self._add_app)
        rem_app.clicked.connect(self._remove_app)
        rescan.clicked.connect(lambda: threading.Thread(target=self._scan_apps, daemon=True).start())
        app_row = QHBoxLayout()
        for w in (add_app, rem_app, rescan):
            app_row.addWidget(w)
        ctl = QCheckBox("Let the assistant click, type and press keys in app windows (experimental)")
        ctl.setToolTip("Asks before it first touches each window; never terminals, password managers or security prompts. Works best with a strong vision model.")
        ctl.setChecked(cfg.computer_control)
        ctl.toggled.connect(lambda v: self._set_home("computer_control", v))
        ytmb = QCheckBox("Play YouTube Music in the assistant's own YouTube Music window")
        ytmb.setToolTip("Reliable and free: the assistant searches and presses play on the web page itself. It opens a separate YouTube Music window (sign in there once).")
        ytmb.setChecked(cfg.ytm_browser)
        ytmb.toggled.connect(lambda v: self._set_home("ytm_browser", v))
        ytmx = QCheckBox("Control my signed-in YouTube Music through the browser extension")
        ytmx.setToolTip("Install the small extension from the folder below once. The app then searches and plays in your own YouTube Music tab or app window.")
        ytmx.setChecked(cfg.ytm_extension)
        ytmx.toggled.connect(lambda v: self._set_field("ytm_extension", v))
        self.ytm_ext_status = QLabel("", objectName="small", wordWrap=True)
        ext_open, ext_check = QPushButton("Open the extension folder"), QPushButton("Check connection")
        ext_open.clicked.connect(self._open_extension_folder)
        ext_check.clicked.connect(self._check_extension)
        self.library_label = QLabel("", objectName="small", wordWrap=True)
        lib_btn = QPushButton("Refresh my YouTube Music library now")
        lib_btn.setToolTip("Saves the names of your playlists and every song in them on this PC (needs the extension), so spoken names are matched exactly")
        lib_btn.clicked.connect(lambda: threading.Thread(target=self._refresh_library, daemon=True).start())
        ext_how = QLabel("Install once: open chrome://extensions, turn on Developer mode, click Load unpacked and pick that folder. Its code is plain JavaScript in the repository.",
                         objectName="small", wordWrap=True)
        form.addRow(ask)
        form.addRow(ytmx)
        form.addRow(self.ytm_ext_status)
        form.addRow(ext_how)
        form.addRow(ext_open)
        form.addRow(ext_check)
        form.addRow(self.library_label)
        form.addRow(lib_btn)
        form.addRow(ytmb)
        form.addRow(ctl)
        form.addRow(self.apps_count)
        form.addRow(self.apps_list)
        form.addRow(app_row)
        test = QPushButton("Test speaker")
        test.clicked.connect(self._test_speaker)
        form.use("Voice")
        form.insertRow(0, test)
        form.use("Apps")
        form.addRow(QLabel("Folders the assistant may read and write:", objectName="small"))
        self.folders_list = QListWidget()
        self.folders_list.setMaximumHeight(80)
        self._fill_folders()
        add_f, rem_f = QPushButton("Add folder\u2026"), QPushButton("Remove")
        add_f.clicked.connect(self._add_folder)
        rem_f.clicked.connect(self._remove_folder)
        folder_row = QHBoxLayout()
        folder_row.addWidget(add_f)
        folder_row.addWidget(rem_f)
        form.addRow(self.folders_list)
        form.addRow(folder_row)
        form.use("Home")
        govee = QCheckBox("Control my Govee lights by voice")
        govee.setChecked(cfg.govee_enabled)
        govee.toggled.connect(lambda v: self._set_home("govee_enabled", v))
        find, blink = QPushButton("Find lights"), QPushButton("Test: blink lights")
        find.clicked.connect(lambda: threading.Thread(target=self._find_lights, daemon=True).start())
        blink.clicked.connect(lambda: threading.Thread(target=self._blink_lights, daemon=True).start())
        self.home_status = QLabel("In the Govee Home app, open each light > settings and turn on LAN Control.", objectName="small", wordWrap=True)
        ha_url = QLineEdit(cfg.ha_url)
        ha_url.setPlaceholderText("http://homeassistant.local:8123")
        ha_url.editingFinished.connect(lambda: self._set_home("ha_url", ha_url.text().strip()))
        ha_key, ha_test = QPushButton("Set Home Assistant token…"), QPushButton("Test connection")
        ha_key.clicked.connect(self._ask_ha_token)
        ha_test.clicked.connect(lambda: threading.Thread(target=self._test_ha, daemon=True).start())
        form.addRow(govee)
        gkey = QPushButton("Set Govee API key\u2026 (optional: uses Govee's cloud instead of LAN)")
        gkey.clicked.connect(lambda: self._ask_secret("govee", "Govee API key", "Paste your Govee developer API key (Govee Home app > Profile > Settings > Apply for API Key):"))
        form.addRow(gkey)
        form.addRow(find)
        form.addRow(blink)
        form.addRow(self.home_status)
        form.addRow(QLabel("Home Assistant (brings in Google Home, Alexa devices, Matter, Hue and more):", objectName="small", wordWrap=True))
        form.addRow("Address", ha_url)
        form.addRow(ha_key)
        form.addRow(ha_test)
        form.use("Memory")
        form.addRow(QLabel("Tell the assistant about yourself: name, job, interests, how you like answers, your personality. It uses this to personalise replies and choices. Stays on this PC; sent to your AI model (the cloud one, if you chose one).", objectName="small", wordWrap=True))
        self.profile_edit = QPlainTextEdit(self.profile.load())
        self.profile_edit.setPlaceholderText("Example: I'm Alex. I like short answers, dry humour, and I drive a Mazda CX-5.")
        self.profile_edit.setMinimumHeight(180)
        save_profile = QPushButton("Save profile")
        save_profile.clicked.connect(self._save_profile)
        learn = QCheckBox("Let the assistant suggest facts to add (asks before each)")
        learn.setChecked(cfg.profile_learning)
        learn.toggled.connect(lambda v: self._set_home("profile_learning", v))
        form.addRow(self.profile_edit)
        form.addRow(save_profile)
        form.addRow(learn)
        form.use("Delegate")
        form.addRow(QLabel("Let the assistant hand coding tasks to Claude Code or Codex (their own command-line programs, with your logins). You approve the exact text before anything is sent.", objectName="small", wordWrap=True))
        dele = QCheckBox("Enable delegating to Claude Code / Codex")
        dele.setChecked(cfg.delegate_enabled)
        dele.toggled.connect(lambda v: self._set_home("delegate_enabled", v))
        edits = QCheckBox("Allow them to edit files (off = read-only)")
        edits.setChecked(cfg.delegate_allow_edits)
        edits.toggled.connect(lambda v: self._set_field("delegate_allow_edits", v))
        reqs = QSpinBox(minimum=1, maximum=200, value=cfg.delegate_daily_requests)
        reqs.valueChanged.connect(lambda v: self._set_field("delegate_daily_requests", v))
        usd = QDoubleSpinBox(minimum=0, maximum=500, singleStep=0.5, value=cfg.delegate_daily_usd, prefix="$")
        usd.setToolTip("Claude reports its cost; Codex does not, so only the request limit applies to it. 0 = no dollar limit.")
        usd.valueChanged.connect(lambda v: self._set_field("delegate_daily_usd", v))
        self.usage_label = QLabel(self.usage.summary(), objectName="small", wordWrap=True)
        refresh = QPushButton("Refresh usage")
        refresh.clicked.connect(lambda: self.usage_label.setText(self.usage.summary()))
        form.addRow(dele)
        form.addRow(edits)
        form.addRow("Max requests per day", reqs)
        form.addRow("Max spend per day", usd)
        form.addRow(self.usage_label)
        form.addRow(refresh)
        form.addRow(QLabel("Runs inside the folders you approved (Apps tab). Commands are set in config.json (delegate_cmd_claude / delegate_cmd_codex).", objectName="small", wordWrap=True))
        form.use("Look")
        form.addRow(QLabel("Make the window your own.", objectName="small"))
        gmode = QComboBox()
        for gid, label in glass.MODES.items():
            gmode.addItem(label, gid)
        gmode.setCurrentIndex(max(gmode.findData(cfg.glass), 0))
        gmode.setEnabled(glass.supported())
        gmode.setToolTip("Frosted glass needs Windows 11 22H2 or newer" if not glass.supported() else "Blurs what is behind the window")
        gmode.currentIndexChanged.connect(lambda _i: self._set_look("glass", gmode.currentData()))
        tint = QSlider(Qt.Horizontal, minimum=0, maximum=100, value=cfg.glass_tint)
        tint.setToolTip("Left: clear glass. Right: more solid panels (easier to read)")
        tint.valueChanged.connect(lambda v: self._set_look("glass_tint", v))
        swatches = QHBoxLayout()
        for name, hexcol in ACCENTS.items():
            sw = QPushButton(objectName="swatch", checkable=True)
            sw.setToolTip(name)
            sw.setStyleSheet(f"background: {hexcol};")
            sw.setChecked(hexcol.lower() == cfg.accent.lower())
            sw.clicked.connect(lambda _c=False, h=hexcol: self._set_look("accent", h))
            swatches.addWidget(sw)
        custom = QPushButton("Custom\u2026")
        custom.clicked.connect(self._pick_accent)
        swatches.addWidget(custom)
        swatches.addStretch(1)
        form.addRow("Window effect", gmode)
        form.addRow("Glass see-through \u2194 solid", tint)
        form.addRow("Accent colour", swatches)
        form.addRow(opacity_row)
        form.use("General")
        form.addRow(QLabel("Ask me first before the assistant... (untick to let it just do it)", objectName="small", wordWrap=True))
        for key, label, tip in (
            ("confirm_links", "opens a web link", "Untick only if you trust the web pages, files and messages the assistant reads: a hostile page could try to make it open a link."),
            ("confirm_screen", "takes a screenshot of a window or screen", "A screenshot is sent to your AI model. If that is a cloud model, the picture leaves your PC. Local models keep it on this PC."),
            ("confirm_clipboard", "reads or replaces my clipboard", "The clipboard may hold passwords or private text."),
            ("confirm_routines", "runs one of my saved routines", "Saving or deleting a routine always asks."),
        ):
            box = QCheckBox(label)
            box.setToolTip(tip)
            box.setChecked(bool(getattr(cfg, key)))
            box.toggled.connect(lambda v, k=key: self._set_field(k, v))
            setattr(self, "ask_" + key.removeprefix("confirm_"), box)
            form.addRow(box)
        form.addRow(QLabel("Opening apps has its own box in the Apps tab. Shell commands, writing files, handing jobs to Claude/Codex, controlling windows and MCP tools always ask.", objectName="small", wordWrap=True))
        cfg_btn, setup_btn = QPushButton("Open settings file"), QPushButton("Run setup again")
        cfg_btn.clicked.connect(self._open_config)
        setup_btn.clicked.connect(self._rerun_setup)
        form.use("General")
        form.addRow(setup_btn)
        form.addRow(cfg_btn)
        return panel

    # ---- wiring ----------------------------------------------------------------
    def _confirm(self, prompt: str) -> bool:
        h = _Holder()
        self.bridge.confirm.emit(prompt, h)
        if self.cfg.voice_approval and getattr(self, "session", None) is not None:
            threading.Thread(target=self._voice_answer, args=(prompt, h), daemon=True).start()
        return h.event.wait(60) and h.result

    def _voice_answer(self, prompt: str, h: "_Holder") -> None:
        answer = self.session.ask_voice(prompt, stop=h.event)
        if answer is not None and not h.event.is_set():
            h.voice = answer
            self.bridge.confirm_close.emit(h)

    def _close_confirm(self, h: "_Holder") -> None:
        if h.box is not None and h.voice is not None:
            h.box.done(QMessageBox.Yes if h.voice else QMessageBox.No)

    def _build_session(self) -> None:
        cfg = self.cfg
        models = Path(cfg.models_dir)
        parts = {}
        for name, factory in (
            ("recognizer", lambda: SherpaRecognizer(models / "asr", end_silence_s=cfg.end_of_speech_s)),
            ("accurate", lambda: AccurateRecognizer(models / "asr2", cfg.tts_threads) if cfg.accurate_asr and (models / "asr2").is_dir() else None),
            ("synth", lambda: load_synth(models, cfg.tts_speed, cfg.tts_threads, cfg.tts_voice, cfg.custom_voice_dir)),
            ("wake", lambda: SherpaWakeWord(models / "kws", cfg.wake_phrase, threshold=cfg.wake_threshold) if cfg.wake_word_enabled else None),
        ):
            try:
                parts[name] = factory()
            except (ModelsMissing, Exception) as e:  # missing model or load failure: degrade, don't crash
                parts[name] = None
                self.warnings.append(f"{name} unavailable: {e}")
        llm = make_llm(cfg)
        registry = build_file_tools(self.policy, self._confirm)
        self.timers = Timers(lambda text: self.session.announce(text))
        self.catalog = AppCatalog()
        threading.Thread(target=self._scan_apps, daemon=True).start()
        registry.add(build_agent_tools(cfg, self.policy, self._confirm, lambda t: self.session.announce(t), self.timers, self.catalog))
        self.govee = make_govee(cfg)
        registry.add(build_home_tools(cfg, self._confirm, self.govee))
        self.vrchat = VRChat(cfg)
        registry.add(build_vrchat_tools(cfg, self.vrchat))
        self.vrchat.resolve_path = self.inbox.resolve  # 'import the menu from the folder I attached'
        self.vrchat.attached = lambda: [it["path"] for it in self.inbox.items.values()]
        registry.add(build_attachment_tools(self.inbox, lambda: self.session.agent.llm, lambda: cfg.main_language))
        self.interp = Interpreter(say=lambda t: self.vrchat.say(t), on_change=lambda: self._refresh_prompt(), chatbox_ok=lambda: self.cfg.vrchat_enabled)
        registry.add(build_interpreter_tools(self.interp))
        registry.add(build_profile_tools(cfg, self.profile, self._confirm))
        if cfg.delegate_enabled:
            registry.add(build_delegate_tools(cfg, self.policy, self._confirm, self.usage))
        registry.add(build_extra_tools(cfg, self._confirm, lambda name, args: registry.call(name, args), known_tools=registry.names, persist=lambda: save_config(self.cfg)))
        registry.add(self._music_and_computer_tools())
        registry.add(build_screen_tools(cfg, self._confirm, lambda: self.session.agent.llm))
        registry.log = CallLog()
        registry.add(build_diagnostic_tools(registry.log, lambda: self.warnings))
        self.pdfs = PdfStore()
        registry.add(build_skill_tools(self.skills, self.pdfs, self.policy))
        self._registry = registry
        agent = Agent(
            llm, self._system_prompt(), registry, max(cfg.max_tool_rounds, 16 if cfg.computer_control else 0),
            max_messages=cfg.history_messages,
            on_tool=lambda m: self.bridge.tool.emit(m),
        )
        self.store = ConversationStore(max_messages=cfg.history_messages)
        self.history = self.store.load() if cfg.remember_conversation else []
        agent.load_history(self.history)
        agent.on_update = self.store.save if cfg.remember_conversation else None
        self.audio = AudioIO(cfg, lambda x: self.session.feed_audio(x), lambda m: self.bridge.error.emit(m))
        self.synth = parts["synth"]
        speaker = Speaker(parts["synth"], self.audio.player) if parts["synth"] else None
        events = QtEvents(self.bridge)
        self.ducker = Ducker(Audio(), cfg.duck_level, enabled=lambda: self.cfg.duck_music, init_com=_init_com)
        events.on_state = self.ducker.on_state
        events.on_delta = self.interp.feed
        events.on_done = self.interp.finish
        self.session = Session(cfg, agent, speaker, parts["recognizer"], parts["wake"], events, accurate=parts["accurate"])
        self._add_voice_picker()
        self.mcp = None
        self._warm_pdfs()
        if cfg.mcp_servers:
            threading.Thread(target=self._start_mcp, daemon=True).start()
        if parts["recognizer"]:
            if not self.audio.start_input():
                self.warnings.append("microphone unavailable: pick another device in Settings")
            self._start_hotkey()
            self.session.start()

    def _system_prompt(self) -> str:
        return (self.cfg.system_prompt + (JARVIS_PERSONA if self.cfg.persona_jarvis else "") + self.profile.prompt() + capabilities_prompt(self.cfg, self._registry.names()) + self.skills.prompt()
                + self.interp.prompt() + self.inbox.prompt())

    def _refresh_prompt(self) -> None:
        try:
            self.session.agent.messages[0]["content"] = self._system_prompt()
        except Exception:
            pass

    def _warm_pdfs(self) -> None:
        """Index the skills' PDFs in the background so the first question is quick (cached on disk afterwards)."""
        pdfs = [p for s in self.skills.active for p in s.dir.rglob("*.pdf")]
        threading.Thread(target=self.pdfs.warm, args=(pdfs,), daemon=True).start()

    def _start_mcp(self) -> None:
        """Connect configured MCP servers in the background and add their tools to the agent."""
        try:
            self.mcp = McpHub(self.cfg.mcp_servers, self._confirm)
            self._registry.add(self.mcp.start())
            self.session.agent.messages[0]["content"] = self._system_prompt()
            self.bridge.note.emit("MCP: " + "; ".join(f"{n} {st}" for n, st in self.mcp.status.items()))
        except Exception as e:
            self.bridge.error.emit(f"MCP failed: {e}")

    def _add_voice_picker(self) -> None:
        if not self.synth:
            return
        box = QComboBox()
        for gender in ("female", "male", "other"):
            for v in self.synth.voices:
                if v.gender == gender:
                    box.addItem(f"{gender.title()} \u2014 {v.label}" if gender != "other" else v.label, v.id)
        cur = self.synth.set_voice(self.cfg.tts_voice)
        box.setCurrentIndex(max(box.findData(cur.id), 0))
        box.setToolTip("Install the Kokoro model (fetch-models kokoro) for more natural voices" if self.synth.engine == "piper" else "")
        box.currentIndexChanged.connect(lambda _i: self._set_voice(box.currentData()))
        self._voice_box = box
        self._form.use("Voice")
        self._form.insertRow(0, "Voice", box)

    def _set_voice(self, voice_id: str) -> None:
        v = self.synth.set_voice(voice_id)
        self._set_field("tts_voice", v.id)
        self.session.speaker.say(f"Hi, I'm {v.label.split(' (')[0]}. How does this sound?")

    def _start_hotkey(self) -> None:
        try:
            import keyboard

            keyboard.add_hotkey(self.cfg.hotkey, self.session.toggle)
        except Exception as e:
            self.warnings.append(f"hotkey {self.cfg.hotkey} unavailable: {e}")

    # ---- slots -----------------------------------------------------------------
    def _on_state(self, state: str) -> None:
        self.status.setText(f"<span style='color:{STATE_COLORS.get(state, '#888')}'>●</span>&nbsp; {state}")

    def _on_error(self, msg: str) -> None:
        self.chat.end_stream()
        self.chat.add("bubbleErr", msg)

    def _on_user(self, text: str) -> None:
        self.chat.add("bubbleUser", text)

    def _on_done(self, desc: str) -> None:
        self.chat.end_stream()
        self.latency.setText(desc)

    def _on_confirm(self, prompt: str, holder: _Holder) -> None:
        box = QMessageBox(QMessageBox.Question, "Confirm action", prompt, QMessageBox.Yes | QMessageBox.No, self)
        is_app = prompt.startswith("Open ") and "\n" not in prompt  # app launches only (links carry the URL on a second line)
        never = QCheckBox("Don't ask again when opening apps") if is_app else None
        if never:
            box.setCheckBox(never)
        holder.box = box
        shown = box.exec() == QMessageBox.Yes
        voice = getattr(holder, "voice", None)
        holder.result = voice if voice is not None else shown
        if never and never.isChecked() and holder.result:
            self.cfg.confirm_launch = False
            save_config(self.cfg)
            if hasattr(self, "ask_apps"):
                self.ask_apps.setChecked(False)
        holder.event.set()

    # ---- avatar memory files ---------------------------------------------------------
    def _refresh_avatar_files(self) -> None:
        self.avatar_box.clear()
        for name, path in AvatarNotes(config_dir() / "avatars").files():
            self.avatar_box.addItem(name, str(path))
        if not self.avatar_box.count():
            self.avatar_box.addItem("(nothing saved yet)", "")

    def _edit_avatar_file(self) -> None:
        path = self.avatar_box.currentData()
        if not path:
            return
        try:
            os.startfile(path, "edit")  # noqa: S606  the user's own editor for .json, when Windows has one
        except OSError:
            os.startfile(path)  # noqa: S606

    def _open_avatar_folder(self) -> None:
        folder = config_dir() / "avatars"
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)  # noqa: S606

    # ---- attachments -----------------------------------------------------------------
    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e) -> None:
        self._attach_paths([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def _pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach files or pictures to the chat")
        if paths:
            self._attach_paths(paths)

    def _attach_paths(self, paths: list[str]) -> None:
        for p in paths:
            name, err = self.inbox.add(p)
            self.chat.add("bubbleNote", f"Attached {name}." if name else err)
            looks_unity = Path(p).suffix.lower() in (".asset", ".controller", ".anim") or (Path(p).is_dir() and any(k in Path(p).name.lower() for k in ("menu", "controller", "animation")))
            if name and looks_unity and self.cfg.vrchat_enabled:
                self.chat.add("bubbleNote", "Looks like Unity avatar files. With the avatar worn in VRChat, say \"import this avatar\" and I will read everything attached.")

    def _attach_picture(self, png: bytes) -> None:
        name, err = self.inbox.add_image_bytes(png)
        self.chat.add("bubbleNote", f"Attached the pasted picture as {name}." if name else err)

    def _attachments_changed(self) -> None:
        text = self.inbox.summary()
        self.attach_label.setText("\U0001F4CE " + text if text else "")
        self.attach_label.setVisible(bool(text))
        self.attach_clear.setVisible(bool(text))
        self._refresh_prompt()  # the assistant is told what is attached, so voice and text both know

    def _send_text(self) -> None:
        self.session.submit_text(self.entry.text())
        self.entry.clear()

    def _set_pin(self, on: bool) -> None:
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        self.show()  # changing window flags hides the window
        self.cfg.always_on_top = on
        save_config(self.cfg)

    def _set_opacity(self, pct: int) -> None:
        self.cfg.opacity = pct / 100
        self.setWindowOpacity(self.cfg.opacity)
        save_config(self.cfg)

    def _set_field(self, name: str, value) -> None:
        setattr(self.cfg, name, value)
        save_config(self.cfg)

    def _set_forward(self, on: bool) -> None:
        self._set_field("mic_forward", on)
        self.mute.setVisible(on)
        if not on:
            self.mute.setChecked(False)
        if self.session.recognizer:
            self.audio.start_input()  # reopens at the right sample rate
        threading.Thread(target=self._check_routes, daemon=True).start()
        if on and not self.cfg.output_device_2:
            self._on_error("Pick a 2nd output (e.g. CABLE Input) for mic forwarding to work.")

    def _set_vr_autoswitch(self, on: bool) -> None:
        self._set_field("vr_autoswitch", on)
        self.vr_box.setVisible(on)
        if on:
            self.vr_watcher.start()
        else:
            self.vr_watcher.stop()  # reports "inactive", which restores the normal devices

    def _set_vr_device(self, key: str, value) -> None:
        self._set_field(key, value or "")
        if self.audio.vr_active:
            self._on_vr(True)  # apply the change right away

    def _on_vr(self, active: bool) -> None:
        self.audio.vr_active = active
        self._vr_gen += 1
        threading.Thread(target=self._apply_vr, args=(active, self._vr_gen), daemon=True).start()

    def _apply_vr(self, active: bool, gen: int) -> None:
        """Runs off the UI thread. Only the newest switch is applied, and applies never overlap, so flapping VR
        states cannot leave two microphone/forwarding pipelines open."""
        for attempt in (0, 1):
            if attempt:
                time.sleep(VR_SETTLE_S)
            with self._vr_apply_lock:
                if gen != self._vr_gen:
                    return  # a newer switch has taken over
                self.audio.refresh_devices()
                if self.session.recognizer and not self.audio.start_input():
                    self.bridge.error.emit("Microphone unavailable with that device.")
                if not attempt:
                    mic = self.cfg.device("input_device", active) or "default"
                    spk = self.cfg.device("output_device", active) or "default"
                    self.bridge.vr_note.emit(f"{'1' if active else '0'}|{mic}|{spk}")
                    if self.cfg.vr_announce:
                        self.session.announce("VR mode on." if active else "Desktop mode.")
        self._check_routes()

    # ---- routing checks ---------------------------------------------------------------
    def _check_routes(self) -> None:
        """Tell the user when Windows itself is also sending a mic into the cable (they would be heard twice)."""
        try:
            warnings = double_voice_warnings(self.cfg, listen_routes())
            summary = self.audio.routing_summary()
            if warnings:
                self.bridge.route.emit("⚠ " + " ".join(warnings), True)
                for w in warnings:
                    if w not in self._warned_routes:
                        self._warned_routes.add(w)
                        self.bridge.error.emit(w)
            else:
                self.bridge.route.emit(f"Right now — {summary}", False)
        except RuntimeError:
            pass  # the window was closed while this background check was running

    def _on_route(self, text: str, warn: bool) -> None:
        self.route_status.setText(text)
        self.route_status.setStyleSheet("color: #ffb86b;" if warn else "")

    def _open_sound_settings(self) -> None:
        subprocess.Popen(["control.exe", "mmsys.cpl,,1"])  # classic Sound panel, Recording tab

    def _on_vr_note(self, text: str) -> None:
        active, mic, spk = text.split("|", 2)
        on = active == "1"
        self.vr_status.setText(f"VR {'active' if on else 'not running'}: mic = {mic} | speaker = {spk}")
        self.chat.add("bubbleNote", f"SteamVR {'detected' if on else 'closed'}: mic = {mic}; speaker = {spk}")

    # ---- smart home --------------------------------------------------------------
    # ---- wake word tuning ------------------------------------------------------------------
    @staticmethod
    def _sens_from_threshold(thr: float) -> int:
        return max(0, min(100, round((0.32 - thr) / 0.30 * 100)))

    def _set_wake_sensitivity(self, v: int) -> None:
        thr = round(0.32 - 0.30 * v / 100, 3)
        self._set_field("wake_threshold", thr)
        if self.cfg.wake_word_enabled:
            try:
                self.session.wake = SherpaWakeWord(Path(self.cfg.models_dir) / "kws", self.cfg.wake_phrase, threshold=thr)
            except Exception as e:
                self.chat.add("bubbleNote", f"Could not apply the wake sensitivity: {e}")

    def _test_wake(self) -> None:
        s = self.session
        if s.wake is None:
            self.chat.add("bubbleNote", "The wake word is off or its model is missing. Tick Enabled (and restart), then test.")
            return
        from .session import State

        if s.state is not State.IDLE:
            self.chat.add("bubbleNote", "Wait until the assistant is idle, then test.")
            return
        stats = {"peak": 0.0, "hits": 0, "heard": []}
        rec = s.recognizer

        def probe(samples, hit):
            stats["peak"] = max(stats["peak"], float(abs(samples).max()) if len(samples) else 0.0)
            stats["hits"] += int(hit)
            if rec is not None:
                rec.accept(samples)
                if rec.is_endpoint():
                    text = rec.text().strip()
                    if text:
                        stats["heard"].append(text)
                    rec.reset()

        s.wake_probe = probe
        self.chat.add("bubbleNote", f"Wake test: say \"{self.cfg.wake_phrase}\" a few times over the next 10 seconds, with pauses.")
        QTimer.singleShot(10000, lambda: self._finish_wake_test(stats))

    def _finish_wake_test(self, stats: dict) -> None:
        self.session.wake_probe = None
        self.session._need_reset = True
        peak = stats["peak"]
        level = "very quiet: raise the microphone level in Windows Sound settings" if peak < 0.08 else "low: a louder mic helps" if peak < 0.2 else "too loud, it may be clipping: lower the mic level" if peak > 0.97 else "good"
        heard = "; ".join(f"\"{t}\"" for t in stats["heard"][:6]) or "nothing clear"
        advice = "" if stats["hits"] else " It did not trigger: raise Wake sensitivity, speak a little louder, or compare what it heard with your phrase."
        self.chat.add("bubbleNote", f"Wake test: caught {stats['hits']} time(s). Mic level {round(peak * 100)}% ({level}). It heard: {heard}.{advice}")

    # ---- look ------------------------------------------------------------------------------
    def _show_settings(self, on: bool) -> None:
        self.settings.setVisible(on)
        self.chat_page.setVisible(not on)
        self._gear.setText("\uE72B" if on else "\uE713")  # back arrow while settings are open
        self._gear.setToolTip("Back to chat" if on else "Settings")

    def _apply_look(self) -> None:
        on = self.cfg.glass != "off" and glass.supported() and QApplication.platformName() == "windows"
        self.setStyleSheet(build_style(self.cfg.accent, on, self.cfg.glass_tint))
        self._veil = window_tint(self.cfg.glass_tint) if on else 0.0
        self.update()
        if on or self.isVisible():
            self._apply_backdrop()

    def _apply_backdrop(self) -> None:
        if QApplication.platformName() == "windows":
            glass.apply(int(self.winId()), self.cfg.glass if glass.supported() else "off")

    def paintEvent(self, e) -> None:
        veil = getattr(self, "_veil", 0.0)
        if veil:
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(20, 22, 27, int(255 * veil)))  # the tint over the blur; the slider sets how strong
            p.end()

    def showEvent(self, e) -> None:  # also runs after the pin button re-creates the native window
        super().showEvent(e)
        self._apply_backdrop()

    def _set_look(self, name: str, value) -> None:
        self._set_field(name, value)
        self._apply_look()

    def _pick_accent(self) -> None:
        from PySide6.QtWidgets import QColorDialog

        c = QColorDialog.getColor(QColor(self.cfg.accent), self, "Accent colour")
        if c.isValid():
            self._set_look("accent", c.name())

    def _save_profile(self) -> None:
        saved = self.profile.save(self.profile_edit.toPlainText())
        self.profile_edit.setPlainText(saved)
        self.session.agent.messages[0]["content"] = self._system_prompt()
        self.chat.add("bubbleNote", f"Profile saved ({len(saved)} characters).")

    def _pick_voice_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Folder with your voice model (.onnx, tokens.txt, espeak-ng-data)")
        if d:
            self._set_field("custom_voice_dir", d)
            self.chat.add("bubbleNote", f"Custom voice set: {d}. Restart the app to use it.")

    def _apply_jarvis_preset(self) -> None:
        """One click: British male voice, butler manner, "Jarvis" wake phrase (the wake phrase applies after a restart)."""
        self._set_field("tts_voice", "bm_george")
        self._set_field("wake_phrase", "Jarvis")
        self._persona_cb.setChecked(True)
        box = getattr(self, "_voice_box", None)
        if box is not None and box.findData("bm_george") >= 0:
            box.setCurrentIndex(box.findData("bm_george"))  # also speaks a sample
        self.chat.add("bubbleNote", "Jarvis-style preset applied: George (UK) voice and butler personality. Restart to use \"Jarvis\" as the wake phrase.")

    def _music_and_computer_tools(self):
        """play_music, plus the computer tools when enabled; play_music then searches inside an already-open YouTube Music window."""
        ctools = self._computer_tools() if self.cfg.computer_control else []
        task = next((t.handler for t in ctools if t.name == "computer_task"), None)
        in_window = (lambda q: task(f"Search for '{q}' and play that song.", "YouTube Music", 12)) if task else None
        if not hasattr(self, "ytm"):
            self.ytm = YtmBrowser()
            self.library = Library()
            threading.Thread(target=self._library_loop, daemon=True).start()
            self.ytm_bridge = YtmBridge(extension_id=self.cfg.ytm_extension_id, on_trust=lambda i: self._set_field("ytm_extension_id", i))
            if self.cfg.ytm_extension:
                self.ytm_bridge.start()
        browser_play = make_player(self.ytm_bridge if self.cfg.ytm_extension else None, self.ytm if self.cfg.ytm_browser else None, open_tab=open_users_youtube_music,
                                  judge=make_judge(lambda: self.session.agent.llm), library=self.library)
        playlist_play = make_playlist_player(self.ytm_bridge if self.cfg.ytm_extension else None, self.library, open_tab=open_users_youtube_music)
        return build_music_tools(self.cfg, self._confirm, list_windows, in_window=in_window, browser_play=browser_play, playlist_play=playlist_play,
                                   playlist_for=self.library.playlist_intent,
                                   music_act=make_music_actions(self.ytm_bridge if self.cfg.ytm_extension else None),
                                   pause=lambda: self._registry.call("media_control", '{"action": "pause"}')) + ctools

    def _open_extension_folder(self) -> None:
        folder = extension_folder()
        if not (folder / "manifest.json").exists():
            self.chat.add("bubbleNote", f"The extension folder was not found at {folder}. It is in the repository's extension folder on GitHub.")
            return
        os.startfile(str(folder))  # noqa: S606 - a folder inside the program's own install

    def _refresh_library(self) -> None:
        try:
            self.bridge.tool.emit("Library: " + self.library.refresh(self.ytm_bridge))
        except Exception as e:
            self.bridge.tool.emit(f"Library not updated: {e}")

    def _library_loop(self) -> None:
        """When the extension is connected and the saved library is a day old (or missing), refresh it in the background; retry later after a failure."""
        time.sleep(20)
        while True:
            try:
                b = getattr(self, "ytm_bridge", None)
                if self.cfg.ytm_extension and b is not None and b.connected and self.library.stale():
                    self._refresh_library()
                    if self.library.stale():
                        time.sleep(600)
            except Exception:
                pass
            time.sleep(60)

    def _check_extension(self) -> None:
        self.library_label.setText(self.library.summary() if hasattr(self, "library") else "")
        b = getattr(self, "ytm_bridge", None)
        if b is None or b.port is None:
            self.ytm_ext_status.setText("The bridge is off (or its ports are busy). Turn the option on and restart the app.")
        elif b.connected:
            self.ytm_ext_status.setText(f"Connected: extension version {b.version or '?'}. Your own YouTube Music will be used.")
        else:
            self.ytm_ext_status.setText("Not connected yet. Install the extension (steps below), keep Chrome open, then check again in a few seconds.")

    def _computer_tools(self):
        return build_computer_tools(self.cfg, self._confirm, lambda: self.session.agent.llm, cancelled=lambda: self.session._cancel.is_set(),
                                    progress=lambda m: self.bridge.tool.emit(m))

    def _set_persona(self, on: bool) -> None:
        self._set_field("persona_jarvis", on)
        self.session.agent.messages[0]["content"] = self._system_prompt()

    def _set_home(self, name: str, value) -> None:
        self._set_field(name, value)
        self._refresh_home_tools()

    def _refresh_home_tools(self) -> None:
        """Apply smart-home settings now: swap the home tools in the live agent and tell it about them."""
        self.govee = make_govee(self.cfg)
        self._registry.remove(VRCHAT_TOOL_NAMES + HOME_TOOL_NAMES + ("delegate_task", "usage_report", "learn_about_user", "computer_task", "control_click", "control_type", "control_keys", "control_scroll", "control_focus", "play_music", "play_playlist", "music_action", "sleep_timer"))
        self._registry.add(self._music_and_computer_tools())
        self._registry.add(build_profile_tools(self.cfg, self.profile, self._confirm))
        self._registry.add(build_home_tools(self.cfg, self._confirm, self.govee))
        self._registry.add(build_vrchat_tools(self.cfg, self.vrchat))
        if self.cfg.delegate_enabled:
            self._registry.add(build_delegate_tools(self.cfg, self.policy, self._confirm, self.usage))
        self.session.agent.messages[0]["content"] = self._system_prompt()
        have = [n for n in HOME_TOOL_NAMES + VRCHAT_TOOL_NAMES + ("delegate_task",) if n in self._registry.names()]
        self.chat.add("bubbleNote", "Tools ready: " + ", ".join(have) if have else "Home and delegation tools off.")

    def _find_lights(self) -> None:
        self.govee = make_govee(self.cfg)  # picks up a newly saved key
        try:
            found = self.govee.discover(2.5)
        except Exception as e:
            self.bridge.tool.emit(f"Govee: {e}")
            return
        msg = ("Found: " + ", ".join(self.govee.label(i) for i in found)) if found else (
            "No Govee lights answered. Turn on LAN Control for each light in the Govee Home app (not every model has it) and make sure this PC is on the same network.")
        self.bridge.tool.emit(msg)

    def _blink_lights(self) -> None:
        try:
            ips = list(self.govee.devices) or list(self.govee.discover(2.5))
        except Exception as e:
            self.bridge.tool.emit(f"Govee: {e}")
            return
        if not ips:
            self.bridge.tool.emit("No Govee lights to test; click Find lights first.")
            return
        for _ in range(2):
            self.govee.apply(ips, False, None, None)
            time.sleep(0.7)
            self.govee.apply(ips, True, None, None)
            time.sleep(0.7)
        self.bridge.tool.emit(f"Blinked {len(ips)} light(s).")

    def _ask_secret(self, name: str, title: str, text: str) -> None:
        key, ok = QInputDialog.getText(self, title, text, QLineEdit.Password)
        if ok and key.strip():
            try:
                save_key(name, key)
            except Exception as e:
                self._on_error(f"Could not save it: {e}")
                return
            self._refresh_home_tools()

    def _ask_ha_token(self) -> None:
        self._ask_secret("homeassistant", "Home Assistant", "Paste a long-lived access token (Profile > Security in Home Assistant):")

    def _test_ha(self) -> None:
        from .smart_home import HomeAssistant
        try:
            n = len(HomeAssistant(self.cfg.ha_url, get_key("homeassistant")).states())
            self.bridge.tool.emit(f"Home Assistant connected: {n} entities.")
        except Exception as e:
            self.bridge.tool.emit(f"Home Assistant test failed: {e}")

    # ---- model provider ----------------------------------------------------------
    def _rebuild_llm(self) -> None:
        self.session.agent.llm = make_llm(self.cfg)
        p = self.cfg.llm_provider
        has_key = not needs_key(p) or bool(get_key(p, key_env(self.cfg, p)))
        self.prov_status.setText(
            f"Using {PROVIDERS[p]}: {model_for(self.cfg) or '(no model set)'}" + ("" if has_key else " | no API key saved yet")
        )
        self.chat.add("bubbleNote", self.prov_status.text())

    def _set_provider(self, pid: str) -> None:
        self._prev_provider = self.cfg.llm_provider
        self.cfg.llm_provider = pid  # so is_cloud reflects the choice (custom/ollama on this PC need no consent)
        if self.cfg.is_cloud and not self.cfg.cloud_ack:
            ok = QMessageBox.question(
                self, "Send conversations to the cloud?",
                f"With {PROVIDERS[pid]} your messages, and any file, web or clipboard text the agent reads, are sent to that company's servers "
                "and may incur charges on your account.\n\nContinue?",
            ) == QMessageBox.Yes
            if not ok:
                self.cfg.llm_provider = self._prev_provider
                self.prov.blockSignals(True)
                self.prov.setCurrentIndex(max(self.prov.findData(self.cfg.llm_provider), 0))
                self.prov.blockSignals(False)
                return
            self.cfg.cloud_ack = True
        self.cfg.llm_provider = pid
        save_config(self.cfg)
        self.model_edit.setText(model_for(self.cfg))
        self._show_url()
        self._rebuild_llm()

    def _show_url(self) -> None:
        f = URL_FIELD.get(self.cfg.llm_provider)
        self.url_edit.setEnabled(bool(f))
        self.url_edit.setText(getattr(self.cfg, f) if f else "(fixed by the provider)")

    def _set_url(self) -> None:
        f = URL_FIELD.get(self.cfg.llm_provider)
        if f:
            setattr(self.cfg, f, self.url_edit.text().strip())
            save_config(self.cfg)
            self._rebuild_llm()

    def _set_model_name(self) -> None:
        set_model(self.cfg, self.cfg.llm_provider, self.model_edit.text())
        save_config(self.cfg)
        self._rebuild_llm()

    def _ask_key(self) -> None:
        p = self.cfg.llm_provider
        if p in ("local", "ollama"):
            self.chat.add("bubbleNote", "This local server needs no API key.")
            return
        key, ok = QInputDialog.getText(self, "API key", f"Paste your {PROVIDERS[p]} API key:", QLineEdit.Password)
        if ok and key.strip():
            try:
                save_key(p, key)
            except Exception as e:
                self._on_error(f"Could not save the key: {e}")
                return
            self._rebuild_llm()

    # ---- skills ------------------------------------------------------------------------
    def _fill_skills(self) -> None:
        self.skills_list.blockSignals(True)
        self.skills_list.clear()
        for sk in self.skills.skills:
            item = QListWidgetItem(f"{sk.display_name} \u2014 {sk.short_description[:90]}")
            item.setData(Qt.UserRole, sk.name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked if sk.name in self.skills.disabled else Qt.Checked)
            item.setToolTip(sk.description)
            self.skills_list.addItem(item)
        self.skills_list.blockSignals(False)
        self.skill_folders_list.clear()
        for f in self.cfg.skill_folders:
            self.skill_folders_list.addItem(f)
        n = len(self.skills.active)
        self.skills_status.setText(
            (f"{n} skill{'s' if n != 1 else ''} active. " if self.skills.skills else "No skills yet. ") + " ".join(self.skills.problems)
        )

    def _apply_skills(self) -> None:
        self.skills.folders, self.skills.disabled = list(self.cfg.skill_folders), set(self.cfg.disabled_skills)
        self.skills.reload()
        self._fill_skills()
        self.session.agent.messages[0]["content"] = self._system_prompt()
        self._warm_pdfs()

    def _toggle_skill(self, item) -> None:
        name, on = item.data(Qt.UserRole), item.checkState() == Qt.Checked
        disabled = set(self.cfg.disabled_skills)
        (disabled.discard if on else disabled.add)(name)
        self.cfg.disabled_skills = sorted(disabled)
        save_config(self.cfg)
        self._apply_skills()

    def _add_skill_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose a skill folder (has SKILL.md) or a folder of skills")
        if d and d not in self.cfg.skill_folders:
            self.cfg.skill_folders.append(d)
            save_config(self.cfg)
            self._apply_skills()
            if not self.skills.skills:
                self.chat.add("bubbleNote", "No SKILL.md found in that folder.")

    def _remove_skill_folder(self) -> None:
        row = self.skill_folders_list.currentRow()
        if row >= 0:
            self.cfg.skill_folders.pop(row)
            save_config(self.cfg)
            self._apply_skills()

    def _rescan_skills(self) -> None:
        self._apply_skills()

    def _import_mcp(self) -> None:
        default = str(Path.home() / ".lmstudio" / "mcp.json")
        path, _ = QFileDialog.getOpenFileName(self, "Choose an mcp.json", default if Path(default).exists() else "", "JSON (*.json)")
        if not path:
            return
        try:
            servers, skipped = read_mcp_json(path)
        except (OSError, ValueError) as e:
            self._on_error(f"Could not read that file: {e}")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Import tool servers")
        col = QVBoxLayout(dlg)
        col.addWidget(QLabel("Tick the servers to add. They start with the app and run programs on this PC; tools that change things still ask first. "
                             "Environment values (keys) are copied into the settings file.", wordWrap=True))
        boxes, trust = {}, {}
        for name, spec in servers.items():
            cb = QCheckBox(f"{name}  —  {spec.get('command')} {' '.join(map(str, spec.get('args', [])))}"[:110])
            cb.setChecked(False)
            cb.setEnabled(name not in self.cfg.mcp_servers)
            tr = QCheckBox("run its tools without asking each time (only for servers you wrote or trust)")
            tr.setEnabled(False)
            cb.toggled.connect(tr.setEnabled)
            tr.setStyleSheet("margin-left: 22px; font-size: 11px;")
            boxes[name], trust[name] = cb, tr
            col.addWidget(cb)
            col.addWidget(tr)
        if skipped:
            col.addWidget(QLabel("Not importable (remote servers): " + ", ".join(skipped), objectName="small", wordWrap=True))
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        col.addWidget(bb)
        if dlg.exec() == QDialog.Accepted:
            added = [n for n, cb in boxes.items() if cb.isChecked() and cb.isEnabled()]
            for n in added:
                self.cfg.mcp_servers[n] = {**servers[n], **({"auto_approve": True} if trust[n].isChecked() else {})}
            save_config(self.cfg)
            self.chat.add("bubbleNote", f"Added {', '.join(added)}. Restart the assistant to connect." if added else "Nothing added.")

    # ---- apps ------------------------------------------------------------------------
    def _scan_apps(self) -> None:
        n = self.catalog.refresh()
        try:
            self.bridge.apps_count.emit(n)
        except RuntimeError:
            pass  # window closed during the scan

    def _on_apps_count(self, n: int) -> None:
        self.apps_count.setText(f"{n} installed apps found automatically" if n else "Could not read the installed app list; add apps below.")

    def _fill_apps(self) -> None:
        self.apps_list.clear()
        for name, path in self.cfg.app_shortcuts.items():
            self.apps_list.addItem(f"{name}  \u2192  {path}")

    def _add_app(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose an app or shortcut", "", "Programs (*.exe *.lnk *.bat *.cmd *.url);;All files (*)")
        if not path:
            return
        name, ok = QInputDialog.getText(self, "Name", "What should you call it when you ask?", text=Path(path).stem)
        if ok and name.strip():
            self.cfg.app_shortcuts[name.strip()] = path
            save_config(self.cfg)
            self._fill_apps()

    def _remove_app(self) -> None:
        row = self.apps_list.currentRow()
        if row >= 0:
            del self.cfg.app_shortcuts[list(self.cfg.app_shortcuts)[row]]
            save_config(self.cfg)
            self._fill_apps()

    def _rerun_setup(self) -> None:
        if run_wizard(self.cfg, self):
            QMessageBox.information(self, "Setup", "Saved. Restart the assistant to apply the changes.")

    def _open_config(self) -> None:
        save_config(self.cfg)
        os.startfile(config_dir() / "config.json")  # noqa: S606

    # ---- conversation memory ---------------------------------------------------------
    def _restore_chat(self) -> None:
        if not self.history:
            return
        self.chat.add("bubbleNote", f"Continuing our last conversation ({len(self.history)} messages). Press New chat to start over.")
        for m in self.history:
            self.chat.add("bubbleUser" if m["role"] == "user" else "bubbleAI", m["content"])
        self.chat.end_stream()

    def _new_chat(self) -> None:
        self.session.stop()
        self.session.agent.reset()
        self.store.clear()
        self.inbox.clear()
        self.chat.clear()
        self.chat.add("bubbleNote", "New conversation started.")

    def _set_remember(self, on: bool) -> None:
        self._set_field("remember_conversation", on)
        if on:
            self.session.agent.on_update = self.store.save
            self.store.save(self.session.agent.messages)
        else:
            self.session.agent.on_update = None
            self.store.clear()  # turning it off erases what was saved

    def _set_mode(self, mode: str) -> None:
        self.cfg.interrupt_mode = mode
        save_config(self.cfg)

    def _set_listen(self, mode: str) -> None:
        self.cfg.listen_mode = mode
        save_config(self.cfg)
        self.session.stop()  # re-evaluates the resting state (open mic vs idle)

    def _fill_folders(self) -> None:
        self.folders_list.clear()
        for f in self.cfg.approved_folders:
            self.folders_list.addItem(f)

    def _remove_folder(self) -> None:
        row = self.folders_list.currentRow()
        if row >= 0:
            folder = self.cfg.approved_folders.pop(row)
            self.policy.remove(folder)
            save_config(self.cfg)
            self._fill_folders()

    def _add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Approve a folder for file tasks")
        if d:
            self.policy.add(d)
            if d not in self.cfg.approved_folders:
                self.cfg.approved_folders.append(d)
            save_config(self.cfg)
            self._fill_folders()
            self.chat.add("bubbleNote", f"Approved folder: {d}")

    def _set_device(self, key: str, value) -> None:
        setattr(self.cfg, key, value or "")
        save_config(self.cfg)
        if key.startswith("input_device") and self.session.recognizer and not self.audio.start_input():
            self._on_error("Microphone unavailable with that device.")

    def _test_speaker(self) -> None:
        if self.session.speaker:
            self.session.speaker.say("This is a test of the speaker output.")
        else:
            self._on_error("No text-to-speech model loaded.")

    def _tick(self) -> None:
        db = 20 * math.log10(self.audio.level + 1e-6)
        self.meter.setValue(int(max(0.0, min(1.0, (db + 60) / 60)) * 100))

    def closeEvent(self, e) -> None:
        self.cfg.window_size = [self.width(), self.height()]
        save_config(self.cfg)
        self.timer.stop()
        self.timers.cancel_all()
        self.ducker.restore()  # never leave the music quiet when the assistant closes
        if self.mcp:
            self.mcp.close()
        self.vr_watcher._stop.set()
        self.audio.stop_input()
        self.audio.close_outputs()
        self.session.close()
        super().closeEvent(e)


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtCore import QLockFile

    argv = argv if argv is not None else sys.argv[1:]
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    app._opaque_dialogs = _OpaqueDialogs()
    app.installEventFilter(app._opaque_dialogs)
    config_dir().mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(config_dir() / "app.lock"))
    if not lock.tryLock(200):  # a second copy would fight over the mic and hotkey
        QMessageBox.information(None, "Voice Assistant", "Voice Assistant is already running.")
        return 0
    cfg = load_config()
    if not cfg.setup_done or "--setup" in argv:
        if not run_wizard(cfg) and not cfg.setup_done:
            cfg.setup_done = True  # user skipped it: do not nag on every start (Settings > Run setup again)
            save_config(cfg)
    win = MainWindow(load_config())
    win.show()
    code = app.exec()
    lock.unlock()
    return code
