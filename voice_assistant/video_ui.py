"""The 'Video detail' settings: a level (normal, mid, high or your own), your own numbers, and whether still frames are skipped."""
from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from .video_look import QUALITIES, clock, custom_quality, estimate_frame_bytes, frame_count, quality_named

LEVEL_LABELS = (
    ("normal", "Normal: 640 px, up to 5 MB of frames"),
    ("mid", "Mid: 960 px, up to 10 MB"),
    ("high", "High: 1280 px, up to 50 MB"),
    ("custom", "Custom: your own numbers"),
)


def example(cfg, seconds: float) -> str:
    q = quality_named(cfg.video_quality, "normal", cfg)
    n = frame_count(seconds, q)
    mb = n * estimate_frame_bytes(q.edge) / 1_000_000
    return f"{clock(seconds)} of video: about {n} frames, around {mb:.1f} MB"


class VideoDetailBox(QWidget):
    def __init__(self, cfg, set_field: Callable[[str, object], None]):
        super().__init__()
        self.cfg, self.set_field = cfg, set_field
        self.level = QComboBox()
        for key, label in LEVEL_LABELS:
            self.level.addItem(label, key)
        self.level.setCurrentIndex(max(self.level.findData(cfg.video_quality), 0))
        self.level.setToolTip("How closely videos are watched. Each level is a picture size and a data budget for all the frames of one video: the number of frames is what the budget allows.")
        self.edge, self.mb, self.fps, self.frames = QSpinBox(), QDoubleSpinBox(), QDoubleSpinBox(), QSpinBox()
        self.edge.setRange(320, 1920)
        self.edge.setSingleStep(80)
        self.edge.setSuffix(" px")
        self.mb.setRange(0.5, 60.0)
        self.mb.setSingleStep(0.5)
        self.mb.setSuffix(" MB")
        self.fps.setRange(0.1, 10.0)
        self.fps.setSingleStep(0.1)
        self.fps.setSuffix(" per second")
        self.frames.setRange(2, 500)
        self.frames.setSingleStep(10)
        self.edge.setValue(cfg.video_custom_edge)
        self.mb.setValue(cfg.video_custom_mb)
        self.fps.setValue(cfg.video_custom_fps)
        self.frames.setValue(cfg.video_custom_frames)
        self.custom = QWidget()
        form = QFormLayout(self.custom)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Picture size (longest side)", self.edge)
        form.addRow("Data budget for one video", self.mb)
        form.addRow("Most frames per second", self.fps)
        form.addRow("Most frames in one video", self.frames)
        self.sound = QCheckBox("Listen to the video's sound")
        self.sound.setToolTip("Turns what is said into text for the timeline (it uses the speech recognizer on this PC). A clip recorded on the phone is then recorded with sound.")
        self.sound.setChecked(cfg.video_use_audio)
        self.overview = QCheckBox("Work out what the video is about")
        self.overview.setToolTip("One more pass that names the game, activity or event and how it is going. A little slower, much better at understanding.")
        self.overview.setChecked(cfg.video_overview)
        self.research = QCheckBox("Look things up online to name a game or activity")
        self.research.setToolTip("Short searches describing what is visible, never the video. Needs 'Let the agent search/read the web' to be on.")
        self.research.setChecked(cfg.video_research)
        self.skip = QCheckBox("Skip frames where nothing changes")
        self.skip.setToolTip("Faster, but the model sees less. Skipped stretches are reported to it, so it does not guess what happened there.")
        self.skip.setChecked(cfg.video_skip_still)
        self.hint = QLabel("", objectName="small", wordWrap=True)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        for w in (self.level, self.custom, self.hint, self.sound, self.overview, self.research, self.skip):
            col.addWidget(w)

        self.level.currentIndexChanged.connect(lambda _i: self._changed("video_quality", self.level.currentData()))
        self.edge.valueChanged.connect(lambda v: self._changed("video_custom_edge", int(v)))
        self.mb.valueChanged.connect(lambda v: self._changed("video_custom_mb", float(v)))
        self.fps.valueChanged.connect(lambda v: self._changed("video_custom_fps", float(v)))
        self.frames.valueChanged.connect(lambda v: self._changed("video_custom_frames", int(v)))
        self.skip.toggled.connect(lambda v: self._changed("video_skip_still", bool(v)))
        self.sound.toggled.connect(lambda v: self._changed("video_use_audio", bool(v)))
        self.overview.toggled.connect(lambda v: self._changed("video_overview", bool(v)))
        self.research.toggled.connect(lambda v: self._changed("video_research", bool(v)))
        self._refresh()

    def _changed(self, key: str, value) -> None:
        self.set_field(key, value)
        self._refresh()

    def _refresh(self) -> None:
        self.custom.setVisible(self.level.currentData() == "custom")
        self.hint.setText(" · ".join((example(self.cfg, 20), example(self.cfg, 120))))
