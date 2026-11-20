"""Logging setup: rotating file log + console + optional UI sink."""
from __future__ import annotations

import logging
import logging.handlers
from typing import Callable

from core.paths import LOGS_DIR

_FMT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_configured = False


class CallbackHandler(logging.Handler):
    """Forwards formatted log records to a callable (used by the UI log panel)."""

    def __init__(self, callback: Callable[[str, int], None]):
        super().__init__(level=logging.INFO)
        self.callback = callback
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.callback(self.format(record), record.levelno)
        except Exception:  # noqa: BLE001
            pass


def setup_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger()
    root.setLevel(level)
    fh = logging.handlers.RotatingFileHandler(
        LOGS_DIR / "gta_clipper.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter(_FMT))
    root.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter(_FMT))
    root.addHandler(ch)
    for noisy in ("urllib3", "httpx", "httpx2", "httpcore", "httpcore2", "faster_whisper", "chat_downloader", "pyscenedetect"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True
