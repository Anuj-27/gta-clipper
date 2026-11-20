"""Central path definitions and external tool discovery for GTA Clipper."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_NAME = "GTA Clipper"
APP_VERSION = "1.0.0"

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT_DIR / "config"
DATABASE_DIR = ROOT_DIR / "database"
ASSETS_DIR = ROOT_DIR / "assets"
TEMP_DIR = ROOT_DIR / "temp"
CLIPS_DIR = ROOT_DIR / "clips"
LOGS_DIR = ROOT_DIR / "logs"
BIN_DIR = ROOT_DIR / "bin"
MODELS_DIR = ROOT_DIR / "models"

for _d in (CONFIG_DIR, DATABASE_DIR, ASSETS_DIR, TEMP_DIR, CLIPS_DIR, LOGS_DIR, BIN_DIR, MODELS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Make bundled binaries (ffmpeg/ffprobe) visible to child processes such as yt-dlp.
if str(BIN_DIR) not in os.environ.get("PATH", ""):
    os.environ["PATH"] = str(BIN_DIR) + os.pathsep + os.environ.get("PATH", "")

# Hide console windows for subprocesses on Windows.
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _find_tool(name: str) -> str | None:
    exe = name + (".exe" if sys.platform == "win32" else "")
    local = BIN_DIR / exe
    if local.exists():
        return str(local)
    return shutil.which(name)


def ffmpeg_path() -> str:
    path = _find_tool("ffmpeg")
    if not path:
        raise FileNotFoundError(
            "FFmpeg was not found. Place ffmpeg.exe in the 'bin' folder or install it on PATH."
        )
    return path


def ffprobe_path() -> str:
    path = _find_tool("ffprobe")
    if not path:
        raise FileNotFoundError(
            "FFprobe was not found. Place ffprobe.exe in the 'bin' folder or install it on PATH."
        )
    return path


def run_quiet(cmd: list[str], timeout: float | None = None, check: bool = False) -> subprocess.CompletedProcess:
    """Run a subprocess without a console window, capturing output as text."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=check,
        creationflags=CREATE_NO_WINDOW,
    )


def check_dependencies() -> dict[str, tuple[bool, str]]:
    """Return {component: (ok, detail)} for all required runtime dependencies."""
    results: dict[str, tuple[bool, str]] = {}

    results["python"] = (sys.version_info >= (3, 10), sys.version.split()[0])

    for tool, getter in (("ffmpeg", ffmpeg_path), ("ffprobe", ffprobe_path)):
        try:
            p = getter()
            out = run_quiet([p, "-version"], timeout=15).stdout.splitlines()
            results[tool] = (True, out[0][:80] if out else p)
        except Exception as e:  # noqa: BLE001
            results[tool] = (False, str(e))

    modules = {
        "yt_dlp": "yt-dlp",
        "faster_whisper": "faster-whisper",
        "numpy": "numpy",
        "cv2": "opencv",
        "soundfile": "soundfile",
        "scenedetect": "PySceneDetect",
        "PySide6": "PySide6",
        "chat_downloader": "chat-downloader",
    }
    import importlib

    for mod, label in modules.items():
        try:
            m = importlib.import_module(mod)
            ver = getattr(m, "__version__", None)
            if ver is None and mod == "yt_dlp":
                from yt_dlp.version import __version__ as ver  # type: ignore
            results[label] = (True, str(ver or "installed"))
        except Exception as e:  # noqa: BLE001
            results[label] = (False, f"{type(e).__name__}: {e}")
    return results
