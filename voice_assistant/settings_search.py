"""Search for a setting: type a word (video, sound, camera, timer...) and jump straight to it."""
from __future__ import annotations

import re
from dataclasses import dataclass

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractSlider, QAbstractSpinBox, QComboBox, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QScrollArea, QTabWidget, QWidget,
)

# a few words people use for the same thing, so "camera" finds the video settings and "mic" finds the sound ones
SYNONYMS = {
    "camera": ("photo", "video", "picture", "clip"),
    "video": ("clip", "frames", "film", "movie", "camera"),
    "sound": ("audio", "microphone", "mic", "listen", "speaker"),
    "audio": ("sound", "microphone", "speaker"),
    "mic": ("microphone", "input"),
    "history": ("chats", "conversation", "remember"),
    "schedule": ("tasks", "reminder", "timer"),
    "reminder": ("tasks", "schedule", "timer"),
    "web": ("internet", "search", "online"),
    "internet": ("web", "search", "online"),
    "voice": ("speech", "speak", "tts"),
    "update": ("version", "upgrade"),
    "privacy": ("safety", "ask first", "confirm"),
}
# what each page is about, so a search finds a setting by its page too ("camera" lands on Video)
TAB_KEYWORDS = {
    "Video": "camera photo video clip film frames plant research",
    "Tasks": "reminders schedule timer jobs scheduled",
    "AI": "model web internet search shell odysseus agent",
    "Voice": "speech speak listen wake word microphone",
    "Audio": "sound microphone speaker output input device",
    "Phone": "pair qr tailscale link",
    "Memory": "profile remember privacy",
    "Delegate": "claude code codex coding helper odysseus",
    "Look": "theme glass colour accent opacity appearance",
    "General": "update language setup first run",
    "Safety": "ask first confirm privacy permission web internet shell powershell folders screenshot clipboard link",
    "Home": "lights govee home assistant smart",
    "VR": "vrchat steamvr headset",
    "Apps": "music youtube open programs",
}
SEARCHABLE = (QAbstractButton, QComboBox, QLineEdit, QAbstractSpinBox, QAbstractSlider, QPlainTextEdit, QLabel)


@dataclass
class Entry:
    tab: int
    tab_name: str
    title: str
    detail: str  # tooltip and nearby text, searched but not shown
    widget: QWidget
    tab_label: str = ""  # the page's name as the list shows it (tab_name is its internal key)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def _own_text(w: QWidget) -> str:
    if isinstance(w, (QAbstractButton, QLabel)):
        return _clean(w.text())
    if isinstance(w, QLineEdit):
        return _clean(w.placeholderText())
    if isinstance(w, QPlainTextEdit):
        return _clean(w.placeholderText())
    if isinstance(w, QAbstractSpinBox):
        return _clean(w.suffix() if hasattr(w, "suffix") else "")
    return ""


class SettingsIndex:
    """Everything that can be searched on the settings pages, found by looking at the widgets themselves, so new settings are searchable automatically."""

    def __init__(self, tabs: QTabWidget, names: list[str], labels: dict[str, str] | None = None):
        self.tabs, self.names, self.labels = tabs, names, labels or {}
        self._frozen: list[Entry] | None = None

    def freeze(self) -> None:
        """Remember the entries as they are now (call on the GUI thread), so search_text can run on the assistant's thread without touching widgets."""
        self._frozen = self.entries()

    def search_text(self, query: str, limit: int = 4) -> list[str]:
        """"Page › Setting" for the best matches, from the frozen entries; safe off the GUI thread."""
        return [f"{e.tab_label or e.tab_name} › {e.title}" for e in self.search(query, limit, self._frozen or [])]

    def entries(self) -> list[Entry]:
        out: list[Entry] = []
        for i, name in enumerate(self.names):
            scroll = self.tabs.widget(i)
            page = scroll.widget() if isinstance(scroll, QScrollArea) else scroll
            if page is None:
                continue
            for w in page.findChildren(QWidget):
                if not isinstance(w, SEARCHABLE) or w.objectName() in ("panel",):
                    continue
                title = _own_text(w)
                tip = _clean(w.toolTip())
                label = self._label_for(w)
                if isinstance(w, QLabel) and (not title or len(title) > 90):
                    continue  # a long paragraph of help text is not itself a setting
                if not (title or label or tip):
                    continue
                shown = title if isinstance(w, (QAbstractButton, QLabel)) and title else (label or title or tip.split(". ")[0].rstrip("."))
                if not shown:
                    continue
                items = " ".join(w.itemText(k) for k in range(w.count())) if isinstance(w, QComboBox) else ""
                out.append(Entry(i, name, shown[:90], " ".join(x for x in (label, title, tip, items, TAB_KEYWORDS.get(name, ""), self.labels.get(name, "")) if x), w,
                                 self.labels.get(name, name)))
        return out

    @staticmethod
    def _label_for(w: QWidget) -> str:
        """The caption of a field: the label the form put beside or above it."""
        p = w.parentWidget()
        while p is not None:
            lay = p.layout()
            if lay is not None and hasattr(lay, "labelForField"):
                lab = lay.labelForField(w)
                if isinstance(lab, QLabel):
                    return _clean(lab.text())
            p = p.parentWidget()
        return ""

    def search(self, query: str, limit: int = 14, entries: list[Entry] | None = None) -> list[Entry]:
        words = [t for t in re.findall(r"[a-z0-9']+", query.lower()) if len(t) >= 2]
        if not words:
            return []
        scored: list[tuple[int, Entry]] = []
        for e in self.entries() if entries is None else entries:
            hay = f"{e.title} {e.detail} {e.tab_name}".lower()
            title = e.title.lower()
            score, ok = 0, True
            for t in words:
                variants = (t, *SYNONYMS.get(t, ()))
                if t in title:
                    score += 5
                elif any(v in title for v in variants):
                    score += 4
                elif t in hay:
                    score += 2
                elif any(v in hay for v in variants):
                    score += 1
                else:
                    ok = False
                    break
            if ok:
                scored.append((score, e))
        scored.sort(key=lambda x: (-x[0], x[1].tab))
        seen: set[tuple[int, str]] = set()
        out: list[Entry] = []
        for _s, e in scored:
            key = (e.tab, e.title)
            if key not in seen:
                seen.add(key)
                out.append(e)
        return out[:limit]


def fill_results(box: QListWidget, found: list[Entry]) -> None:
    box.clear()
    for e in found:
        item = QListWidgetItem(f"{e.tab_label or e.tab_name}  ›  {e.title}")
        item.setData(Qt.UserRole, e)
        box.addItem(item)
    if not found:
        none = QListWidgetItem("Nothing matches. Try another word, for example video, sound, timer or web.")
        none.setFlags(Qt.NoItemFlags)
        box.addItem(none)


def reveal(entry: Entry, tabs: QTabWidget, flash_ms: int = 1800) -> None:
    """Show the setting's page, scroll to it, give it the focus and outline it for a moment."""
    tabs.setCurrentIndex(entry.tab)
    w = entry.widget
    p = w.parentWidget()
    while p is not None:  # a match inside "Advanced settings" opens it
        if hasattr(p, "expand"):
            p.expand()
        p = p.parentWidget()
    scroll = tabs.widget(entry.tab)
    if isinstance(scroll, QScrollArea):
        QTimer.singleShot(0, lambda: scroll.ensureWidgetVisible(w, 0, 120))
    if w.focusPolicy() != Qt.NoFocus:
        w.setFocus()
    before = w.styleSheet()
    w.setStyleSheet(before + " " + f"{w.metaObject().className()} {{ border: 2px solid #6c8cff; border-radius: 8px; }}")
    QTimer.singleShot(flash_ms, lambda: w.setStyleSheet(before))
