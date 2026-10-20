"""Builds the store-page images from the real app window (clean profile, staged demo chat).
Run:  .venv\Scripts\python store\make_assets.py   (close the running assistant first)"""
import os, sys, tempfile, time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
profile = tempfile.mkdtemp(prefix="va_shots_")
os.environ["APPDATA"] = profile  # clean profile: no personal settings, devices or conversation in the screenshots
import voice_assistant.app as A
A.save_config = lambda *a, **k: None
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QListWidget, QTabWidget
from voice_assistant.config import Config
from voice_assistant.theme import STYLE

out = root / "store" / "screenshots"
app = QApplication([])
app.setStyleSheet(STYLE)
cfg = Config(setup_done=True, glass="off", vr_autoswitch=True, mic_forward=True, output_device_2="CABLE Input (VB-Audio Virtual C",
             models_dir=str(Path.home() / "AppData/Roaming/VoiceAssistant/models"))
# two sample skills so the Skills tab is not empty in the screenshot
skills_dir = Path(profile) / "sample-skills"
for n, d in (("garage-mechanic", "Maintenance help that searches your owner's manuals."), ("science-tutor", "Chemistry and physics tutor with cited sources.")):
    (skills_dir / n).mkdir(parents=True)
    (skills_dir / n / "SKILL.md").write_text("\n".join(["---", f"name: {n}", f"description: {d}", "---", "Sample."]), encoding="utf-8")
cfg.skill_folders = [str(skills_dir)]
w = A.MainWindow(cfg)
w.resize(520, 860)
w.show()

def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents(); time.sleep(0.03)

pump(3)
w.chat.clear()
w.chat.add("bubbleUser", "Open YouTube Music.")
w.chat.add("bubbleNote", "⚙ launch_app {\"name\":\"YouTube Music\"}")
w.chat.add("bubbleAI", "Opening YouTube Music.")
w.chat.add("bubbleUser", "Set a timer for ten minutes called pasta.")
w.chat.add("bubbleNote", "⚙ set_timer {\"label\":\"pasta\",\"seconds\":600}")
w.chat.add("bubbleAI", "Done. Your pasta timer is set for ten minutes.")
w.chat.add("bubbleUser", "What can you help me with?")
w.chat.add("bubbleAI", "I can chat, open your apps, search the web, read files in folders you allow, set timers, and use skills you add, like a mechanic or chemistry helper. Just ask.")
w.latency.setText("[local] first text 0.4 s | first audio 0.9 s")
w._on_state("listening")
pump(1)
w.grab().save(str(out / "1-main.png"))
w._gear.setChecked(True)
tabs = w.settings.findChild(QTabWidget)
nav = w.settings.findChild(QListWidget)
for i in range(tabs.count()):
    nav.setCurrentRow(i); pump(0.4)
    w.grab().save(str(out / f"2-settings-{i}-{tabs.tabText(i).lower()}.png"))

# cover images drawn with Qt (itch.io cover 630x500, wide banner 1280x720)
def cover(width, height, path, big):
    img = QImage(width, height, QImage.Format_ARGB32); img.fill(QColor("#14161b"))
    p = QPainter(img); p.setRenderHint(QPainter.Antialiasing); p.setRenderHint(QPainter.TextAntialiasing)
    g = QLinearGradient(0, 0, width, height); g.setColorAt(0, QColor("#1c2340")); g.setColorAt(1, QColor("#14161b"))
    p.fillRect(0, 0, width, height, g)
    icon = QPixmap(str(root / "packaging" / "icon.png")).scaled(int(height * 0.32), int(height * 0.32), Qt.KeepAspectRatio, Qt.SmoothTransformation)
    p.drawPixmap(int(width * 0.08), int(height * 0.12), icon)
    p.setPen(QColor("#e6e8ee")); f = QFont("Segoe UI Variable", int(height * 0.085), QFont.Bold); p.setFont(f)
    p.drawText(QRectF(width * 0.08, height * 0.46, width * 0.84, height * 0.14), Qt.AlignLeft | Qt.AlignVCenter, "Voice Assistant")
    p.setPen(QColor("#8a90a2")); p.setFont(QFont("Segoe UI Variable", int(height * 0.038)))
    p.drawText(QRectF(width * 0.08, height * 0.62, width * 0.84, height * 0.30), Qt.AlignLeft | Qt.TextWordWrap,
               "Hands-free voice assistant for Windows.\nLocal AI, VR game voice, apps, files, skills.")
    if big:  # put the app window on the right of the wide banner
        shot = QPixmap(str(out / "1-main.png")).scaledToHeight(int(height * 0.9), Qt.SmoothTransformation)
        p.drawPixmap(width - shot.width() - int(width * 0.05), int(height * 0.05), shot)
    p.end(); img.save(str(path))

cover(630, 500, out.parent / "cover-630x500.png", False)
cover(1280, 720, out.parent / "banner-1280x720.png", True)
print("saved:", sorted(p.name for p in out.glob("*.png")), "+ cover/banner")
w.close()
