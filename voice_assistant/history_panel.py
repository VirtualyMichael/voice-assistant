"""The chat history sidebar: every saved chat, searchable, with open / rename / delete, and the switch for saving history at all."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QVBoxLayout, QWidget

from .memory import ChatInfo

PANEL_WIDTH = 240


def when(ts: float, now: datetime | None = None) -> str:
    """'14:05' today, 'Yesterday', a weekday within the last week, else '20 Oct'."""
    now = now or datetime.now()
    d = datetime.fromtimestamp(ts)
    days = (now.date() - d.date()).days
    if days <= 0:
        return d.strftime("%H:%M")
    if days == 1:
        return "Yesterday"
    if days < 7:
        return d.strftime("%A")
    return d.strftime("%d %b").lstrip("0")


class HistoryPanel(QWidget):
    open_chat = Signal(str)
    new_chat = Signal()
    delete_chat = Signal(str)
    rename_chat = Signal(str, str)
    delete_all = Signal()
    remember_toggled = Signal(bool)
    query_changed = Signal(str)

    def __init__(self, remember: bool):
        super().__init__()
        self.setFixedWidth(PANEL_WIDTH)
        title = QLabel("Chats", objectName="status")
        self.new_btn = QPushButton("New chat")
        self.new_btn.setToolTip("Start a fresh chat. The current one stays in this list.")
        self.search = QLineEdit(placeholderText="Search chats")
        self.search.setClearButtonEnabled(True)
        self.list = QListWidget(objectName="history")
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.setWordWrap(False)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.rename_btn, self.delete_btn = QPushButton("Rename"), QPushButton("Delete")
        self.delete_btn.setToolTip("Delete the selected chat from this PC")
        self.remember = QCheckBox("Save my chats")
        self.remember.setToolTip("Off: new conversations are not kept. Chats already saved stay until you delete them.")
        self.remember.setChecked(remember)
        self.banner = QLabel("History is off: new chats are not saved.", objectName="small")
        self.banner.setWordWrap(True)
        self.banner.setVisible(not remember)
        self.clear_all = QPushButton("Delete all history…")
        self.empty = QLabel("No saved chats yet.", objectName="small")

        row = QHBoxLayout()
        row.addWidget(self.rename_btn)
        row.addWidget(self.delete_btn)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(8)
        for w in (title, self.new_btn, self.search):
            col.addWidget(w)
        col.addWidget(self.empty)
        col.addWidget(self.list, 1)
        col.addLayout(row)
        col.addWidget(self.remember)
        col.addWidget(self.banner)
        col.addWidget(self.clear_all)

        self.new_btn.clicked.connect(self.new_chat)
        self.list.itemActivated.connect(lambda it: self.open_chat.emit(it.data(Qt.UserRole)))
        self.list.itemClicked.connect(lambda it: self.open_chat.emit(it.data(Qt.UserRole)))
        self.list.customContextMenuRequested.connect(self._menu)
        self.rename_btn.clicked.connect(lambda: self._rename(self.list.currentItem()))
        self.delete_btn.clicked.connect(lambda: self._delete(self.list.currentItem()))
        self.clear_all.clicked.connect(self.delete_all)
        self.remember.toggled.connect(self._remembered)
        self.search.textChanged.connect(self.query_changed)

    def _remembered(self, on: bool) -> None:
        self.banner.setVisible(not on)
        self.remember_toggled.emit(on)

    def set_remember(self, on: bool) -> None:
        self.remember.blockSignals(True)
        self.remember.setChecked(on)
        self.remember.blockSignals(False)
        self.banner.setVisible(not on)

    def refresh(self, chats: list[ChatInfo], current: str | None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for c in chats:
            item = QListWidgetItem(f"{c.title}\n{when(c.updated)} · {c.count} messages")
            item.setData(Qt.UserRole, c.id)
            item.setToolTip(c.title)
            self.list.addItem(item)
            if c.id == current:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        self.empty.setVisible(not chats)
        self.list.setVisible(bool(chats))
        for b in (self.rename_btn, self.delete_btn):
            b.setEnabled(bool(chats))

    def selected_id(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        menu.addAction("Rename", lambda: self._rename(item))
        menu.addAction("Delete", lambda: self._delete(item))
        menu.exec(self.list.viewport().mapToGlobal(pos))

    def _rename(self, item) -> None:
        if item is None:
            return
        from PySide6.QtWidgets import QInputDialog

        text, ok = QInputDialog.getText(self, "Rename chat", "Name:", text=item.toolTip())
        if ok and text.strip():
            self.rename_chat.emit(item.data(Qt.UserRole), text.strip())

    def _delete(self, item) -> None:
        if item is not None:
            self.delete_chat.emit(item.data(Qt.UserRole))
