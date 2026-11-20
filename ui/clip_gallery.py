"""Gallery of generated clips (thumbnails, titles, play / reveal / copy / delete)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication, QIcon, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.paths import CLIPS_DIR
from database.db import get_db
from ui.widgets import Card, fmt_time


class ClipGallery(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        head = QHBoxLayout()
        h1 = QLabel("Generated clips")
        h1.setObjectName("H1")
        head.addWidget(h1)
        head.addStretch(1)
        self.count = QLabel("")
        self.count.setObjectName("Dim")
        head.addWidget(self.count)
        refresh = QPushButton("⟳  Refresh")
        refresh.clicked.connect(self.refresh)
        folder = QPushButton("📂  Open clips folder")
        folder.clicked.connect(lambda: os.startfile(str(CLIPS_DIR)))  # noqa: S606
        head.addWidget(refresh)
        head.addWidget(folder)
        root.addLayout(head)

        body = QHBoxLayout()
        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setIconSize(QSize(162, 288))
        self.grid.setGridSize(QSize(190, 360))
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setWordWrap(True)
        self.grid.setSpacing(8)
        self.grid.currentItemChanged.connect(self._show_details)
        self.grid.itemDoubleClicked.connect(lambda _: self._play())
        body.addWidget(self.grid, 3)

        self.details = Card("Clip details")
        self.d_title = QLabel("Select a clip")
        self.d_title.setWordWrap(True)
        self.d_title.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.d_title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.d_meta = QLabel("")
        self.d_meta.setObjectName("Dim")
        self.d_meta.setWordWrap(True)
        self.d_transcript = QPlainTextEdit()
        self.d_transcript.setReadOnly(True)
        self.details.add(self.d_title)
        self.details.add(self.d_meta)
        self.details.add(QLabel("Transcript"))
        self.details.add(self.d_transcript, 1)
        brow = QHBoxLayout()
        self.b_play = QPushButton("▶ Play")
        self.b_show = QPushButton("Show in folder")
        self.b_copy = QPushButton("Copy title")
        self.b_del = QPushButton("Delete")
        self.b_play.clicked.connect(self._play)
        self.b_show.clicked.connect(self._reveal)
        self.b_copy.clicked.connect(self._copy)
        self.b_del.clicked.connect(self._delete)
        for b in (self.b_play, self.b_show, self.b_copy, self.b_del):
            brow.addWidget(b)
        self.details.layout_.addLayout(brow)
        self.details.setMinimumWidth(340)
        body.addWidget(self.details, 2)
        root.addLayout(body, 1)
        self._set_buttons(False)
        self.refresh()

    def _set_buttons(self, on: bool) -> None:
        for b in (self.b_play, self.b_show, self.b_copy, self.b_del):
            b.setEnabled(on)

    def refresh(self) -> None:
        self.grid.clear()
        clips = get_db().list_clips()
        for c in clips:
            it = QListWidgetItem(c.get("title") or Path(c["path"]).stem)
            thumb = c.get("thumbnail")
            if thumb and Path(thumb).exists():
                it.setIcon(QIcon(QPixmap(thumb)))
            it.setData(Qt.UserRole, c)
            it.setToolTip(c.get("title") or "")
            self.grid.addItem(it)
        self.count.setText(f"{len(clips)} clip{'s' if len(clips) != 1 else ''}")
        if clips:
            self.grid.setCurrentRow(0)
        else:
            self.d_title.setText("No clips yet — start a job on the Dashboard.")
            self.d_meta.setText("")
            self.d_transcript.clear()
            self._set_buttons(False)

    def add_clip(self, _clip: dict) -> None:
        self.refresh()

    def _current(self) -> dict | None:
        it = self.grid.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _show_details(self, cur: QListWidgetItem | None, _prev=None) -> None:
        c = cur.data(Qt.UserRole) if cur else None
        self._set_buttons(bool(c))
        if not c:
            return
        self.d_title.setText(c.get("title") or "(untitled)")
        meta = [
            f"Duration: {c.get('duration') or 0:.1f} s",
            f"Score: {c.get('score') or 0:.2f}",
            f"Source time: {fmt_time(c.get('start') or 0)} – {fmt_time(c.get('end') or 0)}",
            f"File: {c['path']}",
        ]
        if c.get("source_url"):
            meta.append(f"Source: {c['source_url']}")
        self.d_meta.setText("\n".join(meta))
        self.d_transcript.setPlainText(c.get("transcript") or "")

    def _play(self) -> None:
        c = self._current()
        if c and Path(c["path"]).exists():
            os.startfile(c["path"])  # noqa: S606

    def _reveal(self) -> None:
        c = self._current()
        if c:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(c["path"])])  # noqa: S603,S607

    def _copy(self) -> None:
        c = self._current()
        if c:
            QGuiApplication.clipboard().setText(c.get("title") or "")

    def _delete(self) -> None:
        c = self._current()
        if not c:
            return
        if QMessageBox.question(self, "Delete clip", f"Delete this clip file?\n\n{c['path']}") != QMessageBox.Yes:
            return
        for p in (c["path"], c.get("thumbnail"), str(Path(c["path"]).with_suffix(".txt"))):
            if p and Path(p).exists():
                try:
                    Path(p).unlink()
                except OSError as e:
                    QMessageBox.warning(self, "Delete clip", f"Could not delete {p}: {e}")
        get_db().delete_clip(c["id"])
        self.refresh()
