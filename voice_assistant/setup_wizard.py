"""First-run wizard: checks the PC, downloads models (with consent), picks devices and the AI to use."""
from __future__ import annotations

import webbrowser
from pathlib import Path

import httpx
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QRadioButton,
    QVBoxLayout, QWizard, QWizardPage,
)

from .audio_io import list_devices
from .config import Config, save_config
from .hardware import detect_gpus, recommend
from .models import MODEL_SPECS, fetch_model
from .providers import save_key

LMSTUDIO_URL = "https://lmstudio.ai/download"
VBCABLE_URL = "https://vb-audio.com/Cable/"


def check_items(cfg: Config) -> list[tuple[bool, str]]:
    """(ok, message) rows for the welcome page."""
    items: list[tuple[bool, str]] = []
    try:
        ins, outs = list_devices("input"), list_devices("output")
    except Exception:
        ins = outs = []
    items.append((bool(ins), f"Microphone: {len(ins) - 1 if ins else 0} found" if ins else "No microphone found. Plug one in."))
    items.append((bool(outs), "Speakers: found" if outs else "No audio output found."))
    gpus = detect_gpus()
    items.append((True, f"{gpus[0].name}: " + recommend(gpus[0].vram_gb, cfg.vram_reserve_gb) if gpus else "No dedicated GPU found: pick a small local model or a cloud model."))
    try:
        r = httpx.get(cfg.llm_base_url.rstrip("/") + "/models", timeout=2)
        items.append((r.status_code == 200, "Local AI server (LM Studio etc.): running" if r.status_code == 200 else "Local AI server answered with an error."))
    except httpx.HTTPError:
        items.append((False, "No local AI server found. Optional: install LM Studio, or use Claude/OpenAI on the AI page."))
    cable = any("cable" in n.lower() for n in ins + outs)
    items.append((cable, "VB-Cable (virtual audio cable): found" if cable else "VB-Cable not found. Optional, only needed to use the assistant as a game voice."))
    return items


class _Downloader(QThread):
    progress = Signal(str, float)
    finished_all = Signal(list)

    def __init__(self, specs, models_dir: Path):
        super().__init__()
        self.specs, self.models_dir = specs, models_dir

    def run(self) -> None:
        errors = []
        for spec in self.specs:
            try:
                fetch_model(spec, self.models_dir, lambda f, s=spec: self.progress.emit(s.name, f))
            except Exception as e:  # network down, disk full, ...
                errors.append(f"{spec.name}: {e}")
        self.finished_all.emit(errors)


class WelcomePage(QWizardPage):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.setTitle("Welcome")
        self.setSubTitle("A quick check of your PC, then we'll download what the assistant needs.")
        self.col = QVBoxLayout(self)
        self.rows = QLabel(wordWrap=True)
        self.col.addWidget(self.rows)
        row = QHBoxLayout()
        for text, url in (("Get LM Studio (local AI)", LMSTUDIO_URL), ("Get VB-Cable (game voice)", VBCABLE_URL)):
            b = QPushButton(text)
            b.clicked.connect(lambda _c, u=url: webbrowser.open(u))
            row.addWidget(b)
        self.col.addLayout(row)
        recheck = QPushButton("Check again")
        recheck.clicked.connect(self.refresh)
        self.col.addWidget(recheck)

    def initializePage(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        self.rows.setText("<br>".join(f"{'✅' if ok else '⚠️'} {msg}" for ok, msg in check_items(self.cfg)))


class ModelsPage(QWizardPage):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg, self.worker, self.done = cfg, None, False
        self.setTitle("Voice models")
        self.setSubTitle("These run on your PC. Nothing is downloaded until you press Download.")
        col = QVBoxLayout(self)
        self.boxes = []
        for spec in MODEL_SPECS:
            installed = (Path(cfg.models_dir) / spec.slot / "tokens.txt").exists()
            cb = QCheckBox(f"{spec.name} — {spec.note} (~{spec.size_mb} MB)" + ("  [installed]" if installed else ""))
            cb.setChecked(not installed and spec.slot in ("asr", "kokoro", "asr2"))
            cb.setEnabled(not installed)
            self.boxes.append((spec, cb))
            col.addWidget(cb)
        col.addWidget(QLabel("Source: github.com/k2-fsa/sherpa-onnx releases. Check each model's license before redistributing the app.", wordWrap=True))
        self.bar = QProgressBar()
        self.status = QLabel("")
        self.btn = QPushButton("Download selected")
        self.btn.clicked.connect(self.start)
        for w in (self.btn, self.bar, self.status):
            col.addWidget(w)

    def start(self) -> None:
        chosen = [s for s, cb in self.boxes if cb.isChecked() and cb.isEnabled()]
        if not chosen:
            self.status.setText("Nothing selected.")
            return
        self.btn.setEnabled(False)
        self.worker = _Downloader(chosen, Path(self.cfg.models_dir))
        self.worker.progress.connect(lambda n, f: (self.bar.setValue(int(f * 100)), self.status.setText(f"Downloading {n} ...")))
        self.worker.finished_all.connect(self._finished)
        self.worker.start()

    def _finished(self, errors: list) -> None:
        self.done = not errors
        self.btn.setEnabled(True)
        self.bar.setValue(100 if not errors else self.bar.value())
        self.status.setText("Done." if not errors else "Some downloads failed: " + "; ".join(errors))
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        asr = (Path(self.cfg.models_dir) / "asr" / "tokens.txt").exists()
        return asr or self.done or (self.worker is None)  # speech recognition is optional: typed chat still works


class DevicesPage(QWizardPage):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.setTitle("Microphone and speakers")
        self.setSubTitle("Pick your devices (you can change them later in Settings).")
        form = QFormLayout(self)
        self.mic, self.spk = QComboBox(), QComboBox()
        for box, kind in ((self.mic, "input"), (self.spk, "output")):
            box.addItem("System default", "")
            try:
                for n in list_devices(kind):
                    box.addItem(n, n)
            except Exception:
                pass
        form.addRow("Microphone", self.mic)
        form.addRow("Speakers", self.spk)

    def validatePage(self) -> bool:
        self.cfg.input_device, self.cfg.output_device = self.mic.currentData() or "", self.spk.currentData() or ""
        return True


class BrainPage(QWizardPage):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.setTitle("Which AI should answer?")
        self.setSubTitle("Local keeps everything on your PC. Cloud models are smarter but send your conversation to the provider.")
        col = QVBoxLayout(self)
        self.local = QRadioButton("Local server (LM Studio, Ollama, llama.cpp)")
        self.claude = QRadioButton("Claude (Anthropic) — needs an API key, costs money")
        self.openai = QRadioButton("OpenAI — needs an API key, costs money")
        self.local.setChecked(True)
        for r in (self.local, self.claude, self.openai):
            col.addWidget(r)
        form = QFormLayout()
        self.model = QLineEdit(cfg.llm_model)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("API key (saved in Windows Credential Manager)")
        detect = QPushButton("Detect local model")
        detect.clicked.connect(self.detect)
        form.addRow("Model name", self.model)
        form.addRow("API key", self.key)
        col.addLayout(form)
        col.addWidget(detect)
        self.note = QLabel("", wordWrap=True)
        col.addWidget(self.note)

    def detect(self) -> None:
        try:
            data = httpx.get(self.cfg.llm_base_url.rstrip("/") + "/models", timeout=3).json()["data"]
            ids = [m["id"] for m in data if "embed" not in m["id"].lower()]
            if ids:
                self.model.setText(ids[0])
                self.note.setText(f"Found {len(ids)} models; using {ids[0]}. You can change it in Settings.")
        except Exception:
            self.note.setText("No local server found. Start LM Studio's server (Developer tab), or choose a cloud model.")

    def validatePage(self) -> bool:
        c = self.cfg
        if self.claude.isChecked() or self.openai.isChecked():
            p = "anthropic" if self.claude.isChecked() else "openai"
            c.llm_provider, c.cloud_ack = p, True  # the page text above is the disclosure
            if self.key.text().strip():
                try:
                    save_key(p, self.key.text())
                except Exception as e:
                    self.note.setText(f"Could not save the key: {e}")
                    return False
            if p == "openai" and self.model.text().strip():
                c.openai_model = self.model.text().strip()
        else:
            c.llm_provider = "local"
            c.llm_model = self.model.text().strip() or c.llm_model
        return True


class DonePage(QWizardPage):
    def __init__(self):
        super().__init__()
        self.setTitle("All set")
        self.setSubTitle("Say your wake word, press the hotkey (Ctrl+Alt+Space) or just type. Settings has everything else.")
        col = QVBoxLayout(self)
        col.addWidget(QLabel("You can rerun this setup any time from Settings. Your conversation is remembered on this PC only; "
                             "use New chat to erase it.", wordWrap=True))


def run_wizard(cfg: Config, parent=None) -> bool:
    w = QWizard(parent)
    w.setWindowTitle("Voice Assistant setup")
    w.setWizardStyle(QWizard.ModernStyle)
    for page in (WelcomePage(cfg), ModelsPage(cfg), DevicesPage(cfg), BrainPage(cfg), DonePage()):
        w.addPage(page)
    ok = w.exec() == QWizard.Accepted
    if ok:
        cfg.setup_done = True
        save_config(cfg)
    return ok
