"""FFmpeg / FFprobe helpers: probing, cancellable runs, audio decoding to NumPy."""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from core.paths import CREATE_NO_WINDOW, ffmpeg_path, ffprobe_path, run_quiet

log = logging.getLogger(__name__)


class Cancelled(Exception):
    """Raised when the user stops a job."""


class FFmpegError(RuntimeError):
    pass


def probe(path: str | Path) -> dict:
    r = run_quiet(
        [ffprobe_path(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        timeout=60,
    )
    if r.returncode != 0:
        raise FFmpegError(f"ffprobe failed for {path}: {r.stderr.strip()[-300:]}")
    return json.loads(r.stdout or "{}")


def duration(path: str | Path) -> float:
    info = probe(path)
    d = info.get("format", {}).get("duration")
    if d is None:
        for s in info.get("streams", []):
            if s.get("duration"):
                d = s["duration"]
                break
    return float(d or 0.0)


def video_size(path: str | Path) -> tuple[int, int, float]:
    """Return (width, height, fps) of the first video stream."""
    for s in probe(path).get("streams", []):
        if s.get("codec_type") == "video":
            num, _, den = (s.get("avg_frame_rate") or s.get("r_frame_rate") or "30/1").partition("/")
            fps = float(num) / float(den or 1) if float(den or 1) else 30.0
            return int(s["width"]), int(s["height"]), fps or 30.0
    raise FFmpegError(f"No video stream in {path}")


def has_audio(path: str | Path) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe(path).get("streams", []))


def is_valid_media(path: str | Path, min_duration: float = 0.5) -> bool:
    try:
        return Path(path).exists() and duration(path) >= min_duration
    except Exception:  # noqa: BLE001
        return False


_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def run_ffmpeg(
    args: list[str],
    stop: threading.Event | None = None,
    progress: Callable[[float], None] | None = None,
    total: float | None = None,
    timeout: float | None = None,
    cwd: str | Path | None = None,
) -> None:
    """Run ffmpeg with args (without the binary). Kills the process if `stop` is set."""
    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-y", *args]
    log.debug("ffmpeg %s", " ".join(args))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=CREATE_NO_WINDOW,
        cwd=str(cwd) if cwd else None,
    )
    tail: list[str] = []
    start = time.time()

    def _reader() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            tail.append(line)
            if len(tail) > 40:
                del tail[0]
            if progress and total:
                m = _TIME_RE.search(line)
                if m:
                    t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
                    progress(min(t / total, 1.0))

    th = threading.Thread(target=_reader, daemon=True)
    th.start()
    while proc.poll() is None:
        if stop is not None and stop.is_set():
            proc.kill()
            proc.wait()
            raise Cancelled()
        if timeout and time.time() - start > timeout:
            proc.kill()
            proc.wait()
            raise FFmpegError(f"ffmpeg timed out after {timeout:.0f}s")
        time.sleep(0.1)
    th.join(2)
    if proc.returncode != 0:
        msg = "".join(tail[-8:]).strip()
        raise FFmpegError(f"ffmpeg failed (code {proc.returncode}): {msg[-800:]}")


def load_audio(
    path: str | Path,
    sr: int = 16000,
    start: float | None = None,
    end: float | None = None,
    stop: threading.Event | None = None,
) -> np.ndarray:
    """Decode audio to mono float32 at `sr` Hz using ffmpeg (no temp files)."""
    args = [ffmpeg_path(), "-hide_banner", "-nostdin", "-v", "error"]
    if start is not None:
        args += ["-ss", f"{max(start, 0):.3f}"]
    args += ["-i", str(path)]
    if end is not None:
        args += ["-t", f"{max(end - (start or 0), 0.01):.3f}"]
    args += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "pipe:1"]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
    chunks: list[bytes] = []
    assert proc.stdout is not None
    while True:
        if stop is not None and stop.is_set():
            proc.kill()
            raise Cancelled()
        buf = proc.stdout.read(1 << 20)
        if not buf:
            break
        chunks.append(buf)
    proc.wait()
    data = b"".join(chunks)
    if proc.returncode != 0 and not data:
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        raise FFmpegError(f"Audio decode failed: {err.strip()[-300:]}")
    usable = len(data) - (len(data) % 4)
    return np.frombuffer(data[:usable], dtype=np.float32).copy()


def extract_wav(src: str | Path, dst: str | Path, sr: int = 16000, stop: threading.Event | None = None,
                start: float | None = None, end: float | None = None) -> Path:
    args: list[str] = []
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(src)]
    if end is not None:
        args += ["-t", f"{end - (start or 0):.3f}"]
    tmp = Path(dst).with_suffix(".part.wav")
    run_ffmpeg([*args, "-vn", "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", str(tmp)], stop=stop)
    tmp.replace(dst)
    return Path(dst)
