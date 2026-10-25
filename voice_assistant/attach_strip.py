"""A row of previews for what is attached to the chat before it is sent: a thumbnail for each picture, a frame for each video (found in the
background), and an icon with the name for PDFs, text files and folders. Each has a button to take just that one off.
"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QToolButton, QVBoxLayout, QWidget

THUMB = QSize(120, 90)
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
ICONS = {"video": "\U0001F3AC", "pdf": "\U0001F4C4", "text": "\U0001F4DD", "folder": "\U0001F4C1", "image": "\U0001F5BC", "other": "\U0001F4CE"}


def size_text(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB" if n >= 1_000_000 else f"{max(n // 1000, 1)} KB" if n >= 1000 else f"{n} bytes"


def video_frame(path: Path) -> bytes | None:
    """The first picture of a video as a JPEG (the video reader runs in its own process, so this can take a few seconds), or None."""
    from .video_look import Quality, VideoError, extract_frames

    try:
        frames, _duration = extract_frames(Path(path), Quality("thumb", 240, 1.0, 1.0, 1), timeout_s=40)
    except (VideoError, OSError):
        return None
    return frames[0].jpeg if frames else None


class _Tile(QFrame):
    def __init__(self, name: str, item: dict, on_remove):
        super().__init__()
        self.setObjectName("attachTile")
        self.setStyleSheet("#attachTile { background: rgba(127,127,127,38); border-radius: 8px; }")
        self.path = Path(item["path"])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(2)
        self.picture = QLabel(alignment=Qt.AlignCenter)
        self.picture.setFixedSize(THUMB)
        kind = item.get("kind", "other")
        pix = QPixmap(str(self.path)) if kind == "image" and self.path.suffix.lower() in IMAGE_SUFFIXES else QPixmap()
        if not pix.isNull():
            self.picture.setPixmap(pix.scaled(THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.picture.setText(ICONS.get(kind, ICONS["other"]))
            self.picture.setStyleSheet("font-size: 34px;")
        lay.addWidget(self.picture)
        label = QLabel(name)
        label.setMaximumWidth(THUMB.width())
        label.setToolTip(f"{name}\n{kind}" + ("" if kind == "folder" else f", {size_text(item.get('size', 0))}") + "\nClick the picture to open it")
        label.setText(label.fontMetrics().elidedText(name, Qt.ElideMiddle, THUMB.width()))
        lay.addWidget(label)
        info = QLabel("" if kind == "folder" else size_text(item.get("size", 0)), objectName="small")
        lay.addWidget(info)
        remove = QToolButton(self)
        remove.setText("✕")
        remove.setToolTip("Take this off the chat")
        remove.setAutoRaise(True)
        remove.move(THUMB.width() - 12, 4)
        remove.clicked.connect(lambda: on_remove(name))
        self.picture.setCursor(Qt.PointingHandCursor)
        self.picture.mousePressEvent = lambda _e: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path)))

    def set_frame(self, jpeg: bytes) -> None:
        image = QImage.fromData(jpeg)
        if not image.isNull():
            self.picture.setStyleSheet("")
            self.picture.setPixmap(QPixmap.fromImage(image).scaled(THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation))


class AttachStrip(QScrollArea):
    """Show what is attached; `removed(name)` is emitted when its button is pressed."""

    removed = Signal(str)
    _frame_found = Signal(str, bytes)  # from the background thread: (video path, jpeg)

    def __init__(self, frame_for_video=video_frame):
        super().__init__()
        self._frame_for_video = frame_for_video
        self._frames: dict[str, bytes] = {}
        self._asked: set[str] = set()
        self._tiles: dict[str, _Tile] = {}
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedHeight(THUMB.height() + 62)
        body = QWidget()
        self._row = QHBoxLayout(body)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(8)
        self._row.addStretch(1)
        self.setWidget(body)
        self._frame_found.connect(self._got_frame)
        self.setVisible(False)

    def set_items(self, items: dict[str, dict]) -> None:
        while self._row.count() > 1:
            w = self._row.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._tiles.clear()
        for name, item in items.items():
            tile = _Tile(name, item, self.removed.emit)
            self._row.insertWidget(self._row.count() - 1, tile)
            self._tiles[str(item["path"])] = tile
            if item.get("kind") == "video":
                path = str(item["path"])
                if path in self._frames:
                    tile.set_frame(self._frames[path])
                elif path not in self._asked:
                    self._asked.add(path)
                    threading.Thread(target=self._find_frame, args=(path,), daemon=True).start()
        self.setVisible(bool(items))

    def frames(self) -> dict[str, bytes]:
        """The video frames found so far, by file path (the chat shows them in the bubble when the message is sent)."""
        return dict(self._frames)

    def _find_frame(self, path: str) -> None:
        try:
            jpeg = self._frame_for_video(Path(path))
        except Exception:
            jpeg = None
        if jpeg:
            self._frame_found.emit(path, jpeg)

    def _got_frame(self, path: str, jpeg: bytes) -> None:
        self._frames[path] = jpeg
        tile = self._tiles.get(path)
        if tile is not None:
            tile.set_frame(jpeg)
