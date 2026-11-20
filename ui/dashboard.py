"""Main dashboard page: mode selection, URL input, live status, graphs, moments, progress."""
from __future__ import annotations

import logging
import os
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.config import get_config
from core.paths import CLIPS_DIR
from core.url_utils import validate_for_mode
from ui import theme
from ui.job_controller import JobController
from ui.widgets import Card, LineGraph, StatCard, fmt_time

log = logging.getLogger(__name__)


class Dashboard(QWidget):
    clip_created = Signal(dict)

    def __init__(self, controller: JobController, parent: QWidget | None = None):
        super().__init__(parent)
        self.controller = controller
        self.cfg = get_config()
        self.mode = "live"
        self._started_at: float | None = None
        self._clip_count = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        # ---- header -----------------------------------------------------
        h1 = QLabel("Dashboard")
        h1.setObjectName("H1")
        sub = QLabel("Paste a link, press Start, and GTA Clipper finds the best moments for Shorts & Reels.")
        sub.setObjectName("Dim")
        root.addWidget(h1)
        root.addWidget(sub)

        # ---- mode selector ---------------------------------------------
        mode_row = QHBoxLayout()
        self.btn_live = QPushButton("🔴  Live Stream Mode")
        self.btn_video = QPushButton("▶  YouTube Video Mode")
        self.mode_group = QButtonGroup(self)
        for b in (self.btn_live, self.btn_video):
            b.setObjectName("ModeButton")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            self.mode_group.addButton(b)
            mode_row.addWidget(b)
        self.btn_live.setChecked(True)
        self.btn_live.clicked.connect(lambda: self.set_mode("live"))
        self.btn_video.clicked.connect(lambda: self.set_mode("video"))
        root.addLayout(mode_row)

        # ---- URL card ---------------------------------------------------
        url_card = Card("Source")
        url_row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setObjectName("UrlInput")
        self.url_input.setClearButtonEnabled(True)
        self.url_input.textChanged.connect(self._validate)
        self.url_input.returnPressed.connect(self._on_start)
        url_row.addWidget(self.url_input, 1)
        self.btn_start = QPushButton("Start")
        self.btn_start.setObjectName("Primary")
        self.btn_start.setCursor(Qt.PointingHandCursor)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setObjectName("Danger")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.controller.stop)
        url_row.addWidget(self.btn_start)
        url_row.addWidget(self.btn_stop)
        url_card.layout_.addLayout(url_row)
        self.url_status = QLabel(" ")
        self.url_status.setObjectName("Dim")
        url_card.add(self.url_status)

        opt_row = QHBoxLayout()
        self.quality = QComboBox()
        self.quality.addItems(["best", "1080p", "720p", "480p", "360p"])
        self.quality.setCurrentText(self.cfg.get("download.quality"))
        self.duration = QSpinBox()
        self.duration.setRange(5, 180)
        self.duration.setSuffix(" s")
        self.duration.setValue(self.cfg.get("clip.duration"))
        self.num_clips = QSpinBox()
        self.num_clips.setRange(1, 50)
        self.num_clips.setValue(self.cfg.get("clip.max_clips"))
        self.lbl_quality = QLabel("Quality")
        self.lbl_duration = QLabel("Clip length")
        self.lbl_num = QLabel("Clips")
        for lbl, w in ((self.lbl_quality, self.quality), (self.lbl_duration, self.duration), (self.lbl_num, self.num_clips)):
            lbl.setObjectName("Dim")
            opt_row.addWidget(lbl)
            opt_row.addWidget(w)
            opt_row.addSpacing(10)
        self.live_hint = QLabel("")
        self.live_hint.setObjectName("Dim")
        opt_row.addWidget(self.live_hint)
        opt_row.addStretch(1)
        self.btn_folder = QPushButton("📂  Open clips folder")
        self.btn_folder.clicked.connect(lambda: os.startfile(str(CLIPS_DIR)))  # noqa: S606
        opt_row.addWidget(self.btn_folder)
        url_card.layout_.addLayout(opt_row)
        root.addWidget(url_card)

        # ---- status cards ----------------------------------------------
        stats = QHBoxLayout()
        self.st_status = StatCard("Status", "Idle")
        self.st_title = StatCard("Stream / video title", "—")
        self.st_time = StatCard("Elapsed", "0:00")
        self.st_clips = StatCard("Clips generated", "0")
        self.st_title.value.setStyleSheet("font-size: 14px;")
        stats.addWidget(self.st_status, 2)
        stats.addWidget(self.st_title, 4)
        stats.addWidget(self.st_time, 2)
        stats.addWidget(self.st_clips, 2)
        root.addLayout(stats)

        # ---- graphs -----------------------------------------------------
        graphs = QHBoxLayout()
        self.chat_card = Card("Chat activity (messages / 10 s)")
        self.chat_graph = LineGraph(theme.CYAN)
        self.chat_card.add(self.chat_graph, 1)
        self.audio_card = Card("Audio excitement")
        self.audio_graph = LineGraph(theme.ACCENT)
        self.audio_graph.fixed_max = 1.0
        self.audio_card.add(self.audio_graph, 1)
        graphs.addWidget(self.chat_card)
        graphs.addWidget(self.audio_card)
        root.addLayout(graphs, 3)

        # ---- moments / progress / log ----------------------------------
        bottom = QGridLayout()
        bottom.setSpacing(14)
        mcard = Card("Detected moments")
        self.moments = QListWidget()
        self.moments.setWordWrap(True)
        self.moments.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        mcard.add(self.moments, 1)
        bottom.addWidget(mcard, 0, 0)
        pcard = Card("Clip generation progress")
        self.stage = QLabel("Waiting to start")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumBlockCount(500)
        pcard.add(self.stage)
        pcard.add(self.progress)
        pcard.add(self.logbox, 1)
        bottom.addWidget(pcard, 0, 1)
        bottom.setColumnStretch(0, 1)
        bottom.setColumnStretch(1, 1)
        root.addLayout(bottom, 3)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)

        controller.event.connect(self.on_event)
        controller.running_changed.connect(self._on_running)
        self.set_mode("live")

    # ------------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        if self.controller.running:
            (self.btn_live if self.mode == "live" else self.btn_video).setChecked(True)
            return
        self.mode = mode
        is_video = mode == "video"
        for w in (self.quality, self.lbl_quality, self.duration, self.lbl_duration):
            w.setVisible(is_video)
        self.live_hint.setVisible(not is_video)
        self.live_hint.setText(
            f"Clips: {self.cfg.get('clip.pre_event')} s before + {self.cfg.get('clip.post_event')} s after each event"
        )
        self.lbl_num.setText("Clips" if is_video else "Max clips")
        self.url_input.setPlaceholderText(
            "https://www.youtube.com/watch?v=…" if is_video else "https://www.twitch.tv/<channel>  or  YouTube live URL"
        )
        self.chat_card.findChild(QLabel).setText(
            "TRANSCRIPT ENGAGEMENT (SPEECH + EMOTION)" if is_video else "CHAT ACTIVITY (MESSAGES / 10 S)"
        )
        self.audio_card.findChild(QLabel).setText("AUDIO EXCITEMENT")
        self._validate()

    def _validate(self) -> bool:
        text = self.url_input.text().strip()
        if not text:
            self.url_status.setText("Paste a URL to begin.")
            self.url_status.setObjectName("Dim")
            ok = False
        else:
            p = validate_for_mode(text, self.mode)
            ok = p.valid
            self.url_status.setText(("✓ " if ok else "✗ ") + p.message)
            self.url_status.setObjectName("Valid" if ok else "Invalid")
        self.url_status.style().unpolish(self.url_status)
        self.url_status.style().polish(self.url_status)
        self.btn_start.setEnabled(ok and not self.controller.running)
        return ok

    def _on_start(self) -> None:
        if self.controller.running or not self._validate():
            return
        p = validate_for_mode(self.url_input.text(), self.mode)
        self.cfg.set("download.quality", self.quality.currentText(), save=False)
        self.cfg.set("clip.duration", self.duration.value(), save=False)
        self.cfg.set("clip.max_clips", self.num_clips.value())
        self._reset_view()
        opts = {
            "quality": self.quality.currentText(),
            "duration": self.duration.value(),
            "max_clips": self.num_clips.value(),
        }
        if self.controller.start(self.mode, p.url, opts):
            self._started_at = time.time()
            self.st_status.set("Starting…", theme.YELLOW)

    def _reset_view(self) -> None:
        self.chat_graph.clear()
        self.audio_graph.clear()
        self.moments.clear()
        self.progress.setValue(0)
        self.stage.setText("Starting…")
        self.st_title.set("—")
        self._clip_count = 0
        self.st_clips.set("0")

    def _on_running(self, running: bool) -> None:
        self.btn_stop.setEnabled(running)
        self.btn_live.setEnabled(not running)
        self.btn_video.setEnabled(not running)
        self.url_input.setReadOnly(running)
        self._validate()
        if not running:
            self._started_at = None

    def _tick(self) -> None:
        if self._started_at:
            self.st_time.set(fmt_time(time.time() - self._started_at))

    def append_log(self, text: str, level: int) -> None:
        color = theme.RED if level >= logging.ERROR else theme.YELLOW if level >= logging.WARNING else theme.TEXT_DIM
        self.logbox.appendHtml(f'<span style="color:{color}">{text.replace("<", "&lt;")}</span>')

    # ------------------------------------------------------------------
    def on_event(self, name: str, d: dict) -> None:
        if name == "status":
            self.st_status.set(d.get("text", ""), d.get("color", theme.YELLOW))
        elif name == "info":
            self.st_title.set(d.get("title") or "—")
        elif name == "progress":
            if "stage" in d:
                self.stage.setText(d["stage"])
            if "percent" in d:
                self.progress.setValue(int(max(0, min(100, d["percent"]))))
        elif name == "metrics":  # live: one point per analysis window
            t = d["t"]
            self.chat_graph.add_point(t, d.get("chat", 0))
            self.audio_graph.add_point(t, d.get("audio", 0))
            thr = d.get("audio_threshold")
            if thr is not None:
                self.audio_graph.threshold = thr
        elif name == "timeline":  # video: full series at once
            self.chat_graph.set_series(d["t"], d["engagement"])
            self.chat_graph.fixed_max = 1.0
            self.audio_graph.set_series(d["t"], d["audio"])
        elif name == "moment":
            self.chat_graph.add_marker(d["t"])
            self.audio_graph.add_marker(d["t"])
            reasons = ", ".join(d.get("reasons", [])[:3])
            it = QListWidgetItem(
                f"⚡ {fmt_time(d['start'])} – {fmt_time(d['end'])}   score {d['score']:.2f}   {reasons}"
            )
            it.setToolTip("\n".join(d.get("reasons", [])))
            self.moments.insertItem(0, it)
        elif name == "clip":
            self._clip_count += 1
            self.st_clips.set(str(self._clip_count), theme.GREEN)
            self.clip_created.emit(d)
        elif name == "error":
            self.append_log(d.get("message", "Error"), logging.ERROR)
            self.st_status.set("Error", theme.RED)
        elif name == "finished":
            ok = d.get("ok", True)
            self.st_status.set("Finished" if ok else "Failed", theme.GREEN if ok else theme.RED)
            self.stage.setText(d.get("message", ""))
            if ok:
                self.progress.setValue(100)
            it = QListWidgetItem(("✅ " if ok else "❌ ") + d.get("message", ""))
            it.setForeground(QColor(theme.GREEN if ok else theme.RED))
            self.moments.insertItem(0, it)
