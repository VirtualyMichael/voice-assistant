"""Draws the app icon (PNG + ICO) with Qt so no image tools are needed."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

app = QGuiApplication(sys.argv)
size = 256
img = QImage(size, size, QImage.Format_ARGB32)
img.fill(Qt.transparent)
p = QPainter(img)
p.setRenderHint(QPainter.Antialiasing)
bg = QPainterPath()
bg.addRoundedRect(QRectF(8, 8, size - 16, size - 16), 56, 56)
p.fillPath(bg, QColor("#6c8cff"))
white = QColor("#0b0d12")
p.setPen(Qt.NoPen)
p.setBrush(white)
p.drawRoundedRect(QRectF(98, 52, 60, 100), 30, 30)  # mic capsule
pen = QPen(white, 12, Qt.SolidLine, Qt.RoundCap)
p.setPen(pen)
p.setBrush(Qt.NoBrush)
p.drawArc(QRectF(74, 90, 108, 100), 180 * 16, 180 * 16)  # holder
p.drawLine(128, 190, 128, 212)
p.drawLine(100, 212, 156, 212)
p.end()
out = sys.argv[1] if len(sys.argv) > 1 else "packaging"
img.save(os.path.join(out, "icon.png"))
ok = img.save(os.path.join(out, "icon.ico"))
print("icon.ico written:", ok)
