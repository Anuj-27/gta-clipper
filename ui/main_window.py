"""Main application window: sidebar navigation + stacked pages."""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.logger import CallbackHandler
from core.paths import APP_NAME, APP_VERSION, ASSETS_DIR
from ui import theme
from ui.clip_gallery import ClipGallery
from ui.dashboard import Dashboard
from ui.job_controller import JobController
from ui.settings import SettingsPage


class _LogBridge(QObject):
    message = Signal(str, int)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        icon = ASSETS_DIR / "icon.png"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1400, 920)
        self.setMinimumSize(1100, 760)
        self.setStyleSheet(theme.STYLESHEET)

        self.controller = JobController(self)

        root = QWidget()
        root.setObjectName("Root")
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ---- sidebar ----------------------------------------------------
        side = QWidget()
        side.setObjectName("Sidebar")
        side.setFixedWidth(210)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 18, 12, 18)
        sl.setSpacing(6)
        logo_row = QHBoxLayout()
        a = QLabel("GTA")
        a.setObjectName("LogoAccent")
        b = QLabel("Clipper")
        b.setObjectName("Logo")
        logo_row.addWidget(a)
        logo_row.addWidget(b)
        logo_row.addStretch(1)
        sl.addLayout(logo_row)
        tag = QLabel("AI Shorts & Reels maker")
        tag.setObjectName("Dim")
        sl.addWidget(tag)
        sl.addSpacing(18)

        self.stack = QStackedWidget()
        self.dashboard = Dashboard(self.controller)
        self.gallery = ClipGallery()
        self.settings = SettingsPage()
        self.nav = QButtonGroup(self)
        for i, (label, page) in enumerate(
            (("🏠  Dashboard", self.dashboard), ("🎬  Clips Gallery", self.gallery), ("⚙  Settings", self.settings))
        ):
            btn = QPushButton(label)
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, idx=i: self._go(idx))
            self.nav.addButton(btn, i)
            sl.addWidget(btn)
            self.stack.addWidget(page)
        self.nav.button(0).setChecked(True)
        sl.addStretch(1)
        foot = QLabel("100% local · no uploads\nOnly clip content you have\npermission to use.")
        foot.setObjectName("Dim")
        foot.setStyleSheet("font-size: 11px;")
        sl.addWidget(foot)

        lay.addWidget(side)
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.dashboard.clip_created.connect(self.gallery.add_clip)
        self.settings.saved.connect(lambda: self.dashboard.set_mode(self.dashboard.mode))
        self.settings.saved.connect(lambda: self.statusBar().showMessage("Settings saved", 3000))
        self.statusBar().setStyleSheet(f"color: {theme.TEXT_DIM};")

        # Route log records to the dashboard's log panel (thread-safe via signal).
        self._log_bridge = _LogBridge()
        self._log_bridge.message.connect(self.dashboard.append_log)
        self._log_handler = CallbackHandler(self._log_bridge.message.emit)
        logging.getLogger().addHandler(self._log_handler)
        logging.getLogger(__name__).info("%s %s ready.", APP_NAME, APP_VERSION)

    def _go(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        if self.stack.currentWidget() is self.gallery:
            self.gallery.refresh()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.controller.running:
            ans = QMessageBox.question(
                self,
                "Job running",
                "A job is still running. Stop it safely and exit?\n(Already saved clips are kept; "
                "video jobs can be resumed later.)",
            )
            if ans != QMessageBox.Yes:
                event.ignore()
                return
            self.controller.stop()
            self.controller.wait(20)
        logging.getLogger().removeHandler(self._log_handler)
        event.accept()
