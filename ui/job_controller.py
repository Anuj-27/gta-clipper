"""Bridges background jobs (plain Python threads) to the Qt UI via signals."""
from __future__ import annotations

import logging
import threading
from typing import Any

from PySide6.QtCore import QObject, Signal

from core.config import get_config
from database.db import get_db

log = logging.getLogger(__name__)


class JobController(QObject):
    """Owns at most one running job. Events are delivered on the GUI thread via `event`."""

    event = Signal(str, dict)
    running_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._job: Any = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _emit(self, name: str, payload: dict | None = None) -> None:
        # Qt queues cross-thread signal emissions onto the receiver's thread.
        self.event.emit(name, payload or {})

    def start(self, mode: str, url: str, options: dict[str, Any] | None = None) -> bool:
        if self.running:
            self._emit("error", {"message": "A job is already running. Stop it first."})
            return False
        options = options or {}
        self._stop = threading.Event()
        cfg, db = get_config(), get_db()
        if mode == "video":
            from core.pipeline import VideoClipJob

            self._job = VideoClipJob(url, cfg, db, self._emit, self._stop, options)
        elif mode == "live":
            from core.pipeline import LiveClipJob

            self._job = LiveClipJob(url, cfg, db, self._emit, self._stop, options)
        else:
            raise ValueError(mode)
        self._thread = threading.Thread(target=self._run, name=f"{mode}-job", daemon=True)
        self._thread.start()
        self.running_changed.emit(True)
        return True

    def _run(self) -> None:
        try:
            self._job.run()
        except Exception as e:  # noqa: BLE001
            log.exception("Job crashed")
            self._emit("finished", {"ok": False, "message": f"{type(e).__name__}: {e}"})
        finally:
            self.running_changed.emit(False)

    def stop(self) -> None:
        if self.running:
            log.info("Stopping job… (finishing current step safely)")
            self._stop.set()
            self._emit("status", {"text": "Stopping…"})

    def wait(self, timeout: float = 15.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)
