"""GTA Clipper - automatic AI clipping for YouTube & Twitch.

Usage:
    python main.py            Launch the desktop app
    python main.py --check    Print a dependency / environment report
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.logger import setup_logging  # noqa: E402
from core.paths import APP_NAME, APP_VERSION, check_dependencies  # noqa: E402

log = logging.getLogger("main")


def run_check() -> int:
    print(f"{APP_NAME} {APP_VERSION} - environment check")
    results = check_dependencies()
    ok_all = True
    for name, (ok, detail) in results.items():
        ok_all &= ok
        print(f"  [{'OK ' if ok else 'MISSING'}] {name:<16} {detail}")
    print("All dependencies available." if ok_all else "Some dependencies are missing - see README.md.")
    return 0 if ok_all else 1


def run_gui() -> int:
    from PySide6.QtWidgets import QApplication

    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    win = MainWindow()
    win.show()
    return app.exec()


def main() -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--check", action="store_true", help="check dependencies and exit")
    args = parser.parse_args()
    setup_logging()
    if args.check:
        return run_check()
    try:
        return run_gui()
    except Exception:
        log.exception("Fatal error")
        raise


if __name__ == "__main__":
    sys.exit(main())
