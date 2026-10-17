"""PySide6 window and wiring of real components."""
from __future__ import annotations

import math
import os
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QInputDialog, QListWidget, QTabWidget, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget,
)

from .agent import Agent
from .audio_io import AudioIO, list_devices
from .hardware import detect_gpus, recommend
from .config import INTERRUPT_MODES, LISTEN_MODES, Config, config_dir, load_config, save_config
from .mcp_client import McpHub
from .memory import ConversationStore
from .metrics import TurnMetrics
from .models import AccurateRecognizer, ModelsMissing, SherpaRecognizer, SherpaWakeWord, load_synth
from .permissions import FolderPolicy
from .providers import PROVIDERS, make_llm, model_for, save_key, set_model, get_key
from .session import Session, SessionEvents
from .speaker import Speaker
from .setup_wizard import run_wizard
from .theme import STATE_COLORS, STYLE
from .agent_tools import Timers, build_agent_tools, capabilities_prompt
from .apps import AppCatalog
from .tools import build_file_tools
from .vr import VrWatcher


class Bridge(QObject):
    state = Signal(str)
    partial = Signal(str)
    user = Signal(str)
    delta = Signal(str)
    done = Signal(str)
    error = Signal(str)
    confirm = Signal(str, object)
    vr = Signal(bool)
    tool = Signal(str)
    vr_note = Signal(str)
    note = Signal(str)
    apps_count = Signal(int)


class _Holder:
    def __init__(self):
        self.event, self.result = threading.Event(), False


class QtEvents(SessionEvents):
    def __init__(self, b: Bridge):
        self.b = b

    def state(self, s): self.b.state.emit(s.value)
    def partial(self, t): self.b.partial.emit(t)
    def user_final(self, t): self.b.user.emit(t)
    def assistant_delta(self, t): self.b.delta.emit(t)
    def assistant_done(self, m: TurnMetrics): self.b.done.emit(m.describe())
    def error(self, msg): self.b.error.emit(msg)


class TabbedForm:
    """Presents the old single-form API (addRow / insertRow) but spreads rows over scrollable tabs."""

    def __init__(self, tabs: QTabWidget, names: list[str]):
        self.forms: dict[str, QFormLayout] = {}
        for n in names:
            page = QWidget()
            form = QFormLayout(page)
            form.setContentsMargins(10, 12, 10, 10)
            form.setVerticalSpacing(10)
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
        if kind != "bubbleUser":
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


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config):
        super().__init__()
        self.setWindowTitle("Voice Assistant")
        self.resize(*(cfg.window_size or [440, 700]))
        self.cfg, self.bridge, self.policy = cfg, Bridge(), FolderPolicy(cfg.approved_folders)
        self.warnings: list[str] = []

        self.status = QLabel()
        self.meter = QProgressBar(textVisible=False, maximumHeight=4)
        self.pin = QPushButton("Pin on top", objectName="icon", checkable=True)
        self.pin.setToolTip("Keep this window above other apps")
        self.mute = QPushButton("Mute game mic", objectName="icon", checkable=True)
        new_chat = QPushButton("New chat", objectName="icon")
        new_chat.setToolTip("Forget the conversation so far and start fresh")
        self.mute.setVisible(cfg.mic_forward)
        gear = QPushButton("Settings", objectName="icon", checkable=True)
        head = QHBoxLayout()
        head.addWidget(self.status)
        head.addStretch(1)
        head.addWidget(self.mute)
        head.addWidget(new_chat)
        head.addWidget(self.pin)
        head.addWidget(gear)

        self.chat = Chat()
        self.partial = QLabel("", objectName="partial")
        self.partial.setWordWrap(True)
        self.latency = QLabel("", objectName="small")
        self.entry = QLineEdit(placeholderText="Message the assistant…")
        talk, stop = QPushButton("Talk", objectName="talk"), QPushButton("Stop")
        inp = QHBoxLayout()
        inp.addWidget(self.entry, 1)
        inp.addWidget(talk)
        inp.addWidget(stop)

        self.settings = self._build_settings()
        self.settings.setVisible(False)

        col = QVBoxLayout()
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(8)
        col.addLayout(head)
        col.addWidget(self.meter)
        col.addWidget(self.settings)
        col.addWidget(self.chat, 1)
        col.addWidget(self.partial)
        col.addLayout(inp)
        col.addWidget(self.latency)
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
        talk.clicked.connect(self.session.activate)
        stop.clicked.connect(self.session.stop)
        gear.toggled.connect(self.settings.setVisible)
        self.pin.toggled.connect(self._set_pin)
        new_chat.clicked.connect(self._new_chat)
        self.mute.toggled.connect(lambda m: setattr(self.audio, "forward_muted", m))
        self.entry.returnPressed.connect(self._send_text)
        self.timer = QTimer(self, interval=100)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        b.vr.connect(self._on_vr)
        b.vr_note.connect(self._on_vr_note)
        b.note.connect(lambda m: self.chat.add("bubbleNote", m))
        b.apps_count.connect(self._on_apps_count)
        b.tool.connect(lambda m: self.chat.add("bubbleNote", "\u2699 " + m[:140]))
        self.vr_watcher = VrWatcher(b.vr.emit, cfg.vr_processes, cfg.vr_poll_s)
        if cfg.vr_autoswitch:
            self.vr_watcher.start()
        self._on_state("idle")
        self.setWindowOpacity(cfg.opacity)
        if cfg.always_on_top:
            self.pin.setChecked(True)
        self._restore_chat()
        for w in self.warnings:
            self.chat.add("bubbleNote", w)
        if not cfg.approved_folders:
            self.chat.add("bubbleNote", "No approved folders yet: file tasks are refused until you add one (Settings).")

    # ---- settings panel --------------------------------------------------------
    def _build_settings(self) -> QFrame:
        cfg = self.cfg
        panel = QFrame(objectName="panel")
        panel.setMaximumHeight(360)
        tabs = QTabWidget()
        tabs.setUsesScrollButtons(False)
        QVBoxLayout(panel).addWidget(tabs)
        panel.layout().setContentsMargins(8, 6, 8, 6)
        form = TabbedForm(tabs, ["Voice", "Audio", "VR", "AI", "Apps", "General"])
        self._form = form
        form.use("Audio")
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
        form.use("General")
        form.addRow("Opacity", opacity)
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
        form.use("Voice")
        form.addRow("Wake word", wake)
        form.addRow("Wake phrase", phrase)
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
        form.addRow(ask)
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
        return h.event.wait(60) and h.result

    def _build_session(self) -> None:
        cfg = self.cfg
        models = Path(cfg.models_dir)
        parts = {}
        for name, factory in (
            ("recognizer", lambda: SherpaRecognizer(models / "asr", end_silence_s=cfg.end_of_speech_s)),
            ("accurate", lambda: AccurateRecognizer(models / "asr2", cfg.tts_threads) if cfg.accurate_asr and (models / "asr2").is_dir() else None),
            ("synth", lambda: load_synth(models, cfg.tts_speed, cfg.tts_threads, cfg.tts_voice)),
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
        agent = Agent(
            llm, cfg.system_prompt + capabilities_prompt(cfg, registry.names()), registry, cfg.max_tool_rounds,
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
        self.session = Session(cfg, agent, speaker, parts["recognizer"], parts["wake"], QtEvents(self.bridge), accurate=parts["accurate"])
        self._add_voice_picker()
        self._registry, self.mcp = registry, None
        if cfg.mcp_servers:
            threading.Thread(target=self._start_mcp, daemon=True).start()
        if parts["recognizer"]:
            if not self.audio.start_input():
                self.warnings.append("microphone unavailable: pick another device in Settings")
            self._start_hotkey()
            self.session.start()

    def _start_mcp(self) -> None:
        """Connect configured MCP servers in the background and add their tools to the agent."""
        try:
            self.mcp = McpHub(self.cfg.mcp_servers, self._confirm)
            self._registry.add(self.mcp.start())
            self.session.agent.messages[0]["content"] = self.cfg.system_prompt + capabilities_prompt(self.cfg, self._registry.names())
            self.bridge.note.emit("MCP: " + "; ".join(f"{n} {st}" for n, st in self.mcp.status.items()))
        except Exception as e:
            self.bridge.error.emit(f"MCP failed: {e}")

    def _add_voice_picker(self) -> None:
        if not self.synth:
            return
        box = QComboBox()
        for gender in ("female", "male"):
            for v in self.synth.voices:
                if v.gender == gender:
                    box.addItem(f"{gender.title()} \u2014 {v.label}", v.id)
        cur = self.synth.set_voice(self.cfg.tts_voice)
        box.setCurrentIndex(max(box.findData(cur.id), 0))
        box.setToolTip("Install the Kokoro model (fetch-models kokoro) for more natural voices" if self.synth.engine == "piper" else "")
        box.currentIndexChanged.connect(lambda _i: self._set_voice(box.currentData()))
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
        holder.result = box.exec() == QMessageBox.Yes
        if never and never.isChecked() and holder.result:
            self.cfg.confirm_launch = False
            save_config(self.cfg)
            if hasattr(self, "ask_apps"):
                self.ask_apps.setChecked(False)
        holder.event.set()

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
        threading.Thread(target=self._apply_vr, args=(active,), daemon=True).start()

    def _apply_vr(self, active: bool) -> None:
        """Runs off the UI thread: re-reads the device list, restarts the mic, then checks again once the headset settles."""
        for attempt in (0, 1):
            if attempt:
                time.sleep(6)  # the headset's audio devices can appear a few seconds after SteamVR starts
                if self.audio.vr_active != active:
                    return
            self.audio.refresh_devices()
            if self.session.recognizer and not self.audio.start_input():
                self.bridge.error.emit("Microphone unavailable with that device.")
            if not attempt:
                mic = self.cfg.device("input_device", active) or "default"
                spk = self.cfg.device("output_device", active) or "default"
                self.bridge.vr_note.emit(f"{'1' if active else '0'}|{mic}|{spk}")
                if self.cfg.vr_announce:
                    self.session.announce("VR mode on." if active else "Desktop mode.")

    def _on_vr_note(self, text: str) -> None:
        active, mic, spk = text.split("|", 2)
        on = active == "1"
        self.vr_status.setText(f"VR {'active' if on else 'not running'}: mic = {mic} | speaker = {spk}")
        self.chat.add("bubbleNote", f"SteamVR {'detected' if on else 'closed'}: mic = {mic}; speaker = {spk}")

    # ---- model provider ----------------------------------------------------------
    def _rebuild_llm(self) -> None:
        self.session.agent.llm = make_llm(self.cfg)
        p = self.cfg.llm_provider
        has_key = p == "local" or bool(get_key(p, self.cfg.openai_key_env if p == "openai" else self.cfg.anthropic_key_env))
        self.prov_status.setText(
            f"Using {PROVIDERS[p]}: {model_for(self.cfg) or '(no model set)'}" + ("" if has_key else " | no API key saved yet")
        )
        self.chat.add("bubbleNote", self.prov_status.text())

    def _set_provider(self, pid: str) -> None:
        if pid != "local" and not self.cfg.cloud_ack:
            ok = QMessageBox.question(
                self, "Send conversations to the cloud?",
                f"With {PROVIDERS[pid]} your messages, and any file, web or clipboard text the agent reads, are sent to that company's servers "
                "and may incur charges on your account.\n\nContinue?",
            ) == QMessageBox.Yes
            if not ok:
                self.prov.blockSignals(True)
                self.prov.setCurrentIndex(max(self.prov.findData(self.cfg.llm_provider), 0))
                self.prov.blockSignals(False)
                return
            self.cfg.cloud_ack = True
        self.cfg.llm_provider = pid
        save_config(self.cfg)
        self.model_edit.setText(model_for(self.cfg))
        self._rebuild_llm()

    def _set_model_name(self) -> None:
        set_model(self.cfg, self.cfg.llm_provider, self.model_edit.text())
        save_config(self.cfg)
        self._rebuild_llm()

    def _ask_key(self) -> None:
        p = self.cfg.llm_provider
        if p == "local":
            self.chat.add("bubbleNote", "The local server needs no API key.")
            return
        key, ok = QInputDialog.getText(self, "API key", f"Paste your {PROVIDERS[p]} API key:", QLineEdit.Password)
        if ok and key.strip():
            try:
                save_key(p, key)
            except Exception as e:
                self._on_error(f"Could not save the key: {e}")
                return
            self._rebuild_llm()

    # ---- apps ------------------------------------------------------------------------
    def _scan_apps(self) -> None:
        self.bridge.apps_count.emit(self.catalog.refresh())

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
