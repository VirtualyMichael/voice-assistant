"""The Tasks tab: scheduled reminders and jobs, with a form to add one by hand (you can also just ask the assistant)."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QDate, QTime, Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTimeEdit, QVBoxLayout, QWidget,
)

from .scheduler import DAYS, KINDS, Scheduler, Task, TaskStore, describe, make_task

REPEATS = {"once": "Once", "daily": "Every day", "weekdays": "Every weekday", "weekly": "On chosen days", "every": "Every few minutes or hours"}


def status_line(t: Task, now: datetime | None = None) -> str:
    nxt = datetime.fromisoformat(t.next_run) if t.next_run else None
    if not t.enabled:
        return "done" if t.kind == "once" and t.last_status else "paused"
    return ("next " + nxt.strftime("%a %d %b %H:%M")) if nxt else "no future run"


class AddTaskDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("New scheduled task")
        self.name = QLineEdit(placeholderText="Morning briefing")
        self.mode = QComboBox()
        self.mode.addItem("Say a reminder", "remind")
        self.mode.addItem("Ask the assistant to do it, then tell me", "do")
        self.what = QPlainTextEdit(placeholderText="What to say, or what to do (for example: tell me the weather and my timers)")
        self.what.setFixedHeight(80)
        self.repeat = QComboBox()
        for k in KINDS:
            self.repeat.addItem(REPEATS[k], k)
        self.time = QTimeEdit(QTime(8, 0))
        self.time.setDisplayFormat("HH:mm")
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.days = [QCheckBox(d.title()) for d in DAYS]
        days_row = QWidget()
        dl = QHBoxLayout(days_row)
        dl.setContentsMargins(0, 0, 0, 0)
        for c in self.days:
            dl.addWidget(c)
        self.every = QSpinBox()
        self.every.setRange(5, 7 * 24 * 60)
        self.every.setValue(30)
        self.every.setSuffix(" minutes")
        self.hint = QLabel("A job runs on its own while you are away; anything that needs your approval is skipped.", objectName="small", wordWrap=True)
        self.error = QLabel("", objectName="small", wordWrap=True)
        self.form = QFormLayout()
        for label, w in (("Name", self.name), ("Type", self.mode), ("Text", self.what), ("Repeat", self.repeat), ("Date", self.date), ("Time", self.time),
                         ("Days", days_row), ("Every", self.every)):
            self.form.addRow(label, w)
        self._rows = {"date": (self.date,), "days": (days_row,), "every": (self.every,), "time": (self.time,)}
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        col = QVBoxLayout(self)
        col.addLayout(self.form)
        col.addWidget(self.hint)
        col.addWidget(self.error)
        col.addWidget(buttons)
        self.repeat.currentIndexChanged.connect(self._layout)
        self._layout()
        self.task: Task | None = None

    def _layout(self) -> None:
        kind = self.repeat.currentData()
        show = {"date": kind == "once", "time": kind != "every", "days": kind == "weekly", "every": kind == "every"}
        for key, widgets in self._rows.items():
            for w in widgets:
                w.setVisible(show[key])
                label = self.form.labelForField(w)
                if label:
                    label.setVisible(show[key])

    def _ok(self) -> None:
        try:
            self.task = make_task(
                self.name.text(), self.what.toPlainText(), self.mode.currentData(), self.repeat.currentData(), self.time.time().toString("HH:mm"),
                self.date.date().toString("yyyy-MM-dd"), [i for i, c in enumerate(self.days) if c.isChecked()], self.every.value(),
            )
        except ValueError as e:
            self.error.setText(f"Cannot save: {e}.")
            return
        self.accept()


class TasksPage(QWidget):
    def __init__(self):
        super().__init__()
        self.store: TaskStore | None = None
        self.scheduler: Scheduler | None = None
        self.enabled = QCheckBox("Run scheduled tasks")
        self.enabled.setToolTip("Off: nothing runs on a schedule. Tasks are kept. Tasks run only while this app is open.")
        self.list = QListWidget()
        self.list.setMinimumHeight(150)
        self.detail = QLabel("Ask the assistant (“remind me at 5 to call Sam”, “every weekday at 8 tell me the weather”) or press Add.", objectName="small", wordWrap=True)
        self.add_btn, self.toggle_btn, self.run_btn, self.delete_btn = QPushButton("Add…"), QPushButton("Pause"), QPushButton("Run now"), QPushButton("Delete")
        row = QHBoxLayout()
        for b in (self.add_btn, self.toggle_btn, self.run_btn, self.delete_btn):
            row.addWidget(b)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        for w in (self.enabled, self.list):
            col.addWidget(w)
        col.addLayout(row)
        col.addWidget(self.detail)
        self.add_btn.clicked.connect(self._add)
        self.toggle_btn.clicked.connect(self._toggle)
        self.run_btn.clicked.connect(self._run)
        self.delete_btn.clicked.connect(self._delete)
        self.list.currentItemChanged.connect(lambda *_: self._show_detail())

    def bind(self, store: TaskStore, scheduler: Scheduler, enabled: bool, on_enabled) -> None:
        self.store, self.scheduler = store, scheduler
        self.enabled.setChecked(enabled)
        self.enabled.toggled.connect(on_enabled)
        self.refresh()

    def _selected(self) -> Task | None:
        item = self.list.currentItem()
        if item is None or self.store is None:
            return None
        return next((t for t in self.store.tasks if t.id == item.data(Qt.UserRole)), None)

    def refresh(self) -> None:
        if self.store is None:
            return
        keep = self.list.currentItem().data(Qt.UserRole) if self.list.currentItem() else None
        self.list.blockSignals(True)
        self.list.clear()
        for t in sorted(self.store.tasks, key=lambda t: (not t.enabled, t.next_run or "z")):
            item = QListWidgetItem(f"{t.name}\n{describe(t)} · {status_line(t)}" + ("" if t.mode == "remind" else " · job"))
            item.setData(Qt.UserRole, t.id)
            self.list.addItem(item)
            if t.id == keep:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        self._show_detail()

    def _show_detail(self) -> None:
        t = self._selected()
        for b in (self.toggle_btn, self.run_btn, self.delete_btn):
            b.setEnabled(t is not None)
        if t is None:
            return
        self.toggle_btn.setText("Resume" if not t.enabled else "Pause")
        kind = "says" if t.mode == "remind" else "does"
        last = f"\nLast run ({t.last_status}): {t.last_result[:300]}" if t.last_status else ""
        self.detail.setText(f"It {kind}: {t.prompt[:300]}{last}")

    def _add(self) -> None:
        dlg = AddTaskDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.task is not None and self.store is not None:
            try:
                self.store.add(dlg.task)
            except ValueError as e:
                QMessageBox.warning(self, "Scheduled tasks", str(e))
                return
            self.refresh()

    def _toggle(self) -> None:
        t = self._selected()
        if t is None or self.store is None:
            return
        from .scheduler import compute_next

        t.enabled = not t.enabled
        if t.enabled:
            nxt = compute_next(t, datetime.now())
            t.next_run = nxt.isoformat(timespec="seconds") if nxt else ""
            if not nxt:
                t.enabled = False
                QMessageBox.information(self, "Scheduled tasks", "This task has no future run, so it stays off.")
        self.store.save()
        self.refresh()

    def _run(self) -> None:
        t = self._selected()
        if t is not None and self.scheduler is not None:
            self.scheduler.run_now(t.id)

    def _delete(self) -> None:
        t = self._selected()
        if t is None or self.store is None:
            return
        if QMessageBox.question(self, "Delete task", f"Delete “{t.name}”?") == QMessageBox.Yes:
            self.store.remove(t.id)
            self.refresh()
