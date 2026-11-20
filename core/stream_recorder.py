"""Continuous livestream recorder using yt-dlp stream resolution + FFmpeg MPEG-TS segment muxer."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, Callable

import yt_dlp

from core.paths import BIN_DIR, CREATE_NO_WINDOW, ffmpeg_path
from core.rolling_buffer import RollingBuffer
from core.url_utils import parse_url

log = logging.getLogger(__name__)


@dataclass
class StreamInfo:
    platform: str
    stream_id: str
    channel: str
    title: str
    stream_url: str
    audio_url: str = ""
    headers: dict[str, str] | None = None
    is_live: bool = True


def resolve_live_stream(url: str) -> StreamInfo:
    """Resolve a YouTube or Twitch livestream URL into direct HLS/DASH manifest URL(s) via yt-dlp."""
    parsed = parse_url(url)
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "best[height<=1080]/bestvideo[height<=1080]+bestaudio/best",
        "ffmpeg_location": str(BIN_DIR),
        "socket_timeout": 20,
    }
    deno = BIN_DIR / "deno.exe"
    if deno.exists():
        opts["js_runtimes"] = {"deno": {"path": str(deno)}}

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise RuntimeError("Could not resolve livestream information.")
    if "entries" in info and info["entries"]:
        info = info["entries"][0]

    live_status = info.get("live_status") or ""
    is_live = bool(info.get("is_live") or live_status == "is_live" or parsed.platform == "twitch")
    if not is_live and live_status in ("not_live", "was_live", "post_live"):
        raise RuntimeError(
            f"Stream is not currently live (status: {live_status}). Use YouTube Video Mode for uploaded videos."
        )

    stream_url = info.get("url") or ""
    audio_url = ""
    headers = dict(info.get("http_headers") or {})
    if not stream_url and info.get("requested_formats"):
        rf = info["requested_formats"]
        stream_url = rf[0].get("url") or ""
        headers = dict(rf[0].get("http_headers") or headers)
        if len(rf) > 1:
            audio_url = rf[1].get("url") or ""
    if not stream_url:
        raise RuntimeError("No playable stream URL returned by yt-dlp.")

    channel = (
        info.get("uploader")
        or info.get("channel")
        or info.get(" uploader_id")
        or (parsed.ident if parsed.platform == "twitch" else "")
    )
    return StreamInfo(
        platform=parsed.platform or "youtube",
        stream_id=str(info.get("id") or parsed.ident or "live"),
        channel=str(channel or parsed.ident),
        title=str(info.get("title") or f"{parsed.ident} Live"),
        stream_url=stream_url,
        audio_url=audio_url,
        headers=headers,
        is_live=True,
    )


class StreamRecorder:
    """Records a live stream into RollingBuffer segments with automatic reconnection on network drops."""

    def __init__(
        self,
        url: str,
        rolling_buffer: RollingBuffer,
        segment_seconds: int = 5,
        max_reconnects: int = 20,
        on_status: Callable[[str], None] | None = None,
    ):
        self.url = url
        self.buffer = rolling_buffer
        self.segment_seconds = int(segment_seconds)
        self.max_reconnects = int(max_reconnects)
        self.on_status = on_status
        self.info: StreamInfo | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen | None = None
        self._next_seg_index: int = 0

    def start(self) -> StreamInfo:
        """Resolve stream metadata synchronously, then launch background FFmpeg recording thread."""
        self.info = resolve_live_stream(self.url)
        self._stop.clear()
        self._thread = threading.Thread(target=self._record_loop, name="stream-recorder", daemon=True)
        self._thread.start()
        return self.info

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def stop(self) -> None:
        self._stop.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _next_start_number(self) -> int:
        existing = list(self.buffer.buffer_dir.glob("seg_*.ts"))
        if not existing:
            return self._next_seg_index
        nums = []
        for p in existing:
            stem = p.stem.replace("seg_", "")
            if stem.isdigit():
                nums.append(int(stem))
        return max(nums, default=-1) + 1

    def _build_ffmpeg_cmd(self, info: StreamInfo, start_number: int) -> list[str]:
        cmd = [
            ffmpeg_path(),
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "warning",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "10",
            "-rw_timeout",
            "15000000",
        ]
        if info.headers:
            hdr_lines = "".join(f"{k}: {v}\r\n" for k, v in info.headers.items() if k and v)
            if hdr_lines:
                cmd += ["-headers", hdr_lines]
        cmd += ["-i", info.stream_url]
        if info.audio_url:
            cmd += ["-i", info.audio_url, "-map", "0:v:0", "-map", "1:a:0"]
        pattern = str(self.buffer.buffer_dir / "seg_%06d.ts")
        cmd += [
            "-c:v", "copy",
            "-c:a", "aac",
            "-f", "segment",
            "-segment_time", str(self.segment_seconds),
            "-segment_format", "mpegts",
            "-segment_start_number", str(start_number),
            "-reset_timestamps", "1",
            pattern,
        ]
        return cmd

    def _record_loop(self) -> None:
        attempts = 0
        while not self._stop.is_set():
            try:
                if attempts > 0:
                    if self.on_status:
                        self.on_status(f"Reconnecting to live stream (attempt {attempts}/{self.max_reconnects})…")
                    self.info = resolve_live_stream(self.url)
                assert self.info is not None
                start_num = self._next_start_number()
                cmd = self._build_ffmpeg_cmd(self.info, start_num)
                if self.on_status:
                    self.on_status("Recording live stream")
                self._proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=CREATE_NO_WINDOW,
                )
                while self._proc.poll() is None:
                    if self._stop.is_set():
                        self._proc.terminate()
                        break
                    self.buffer.scan()
                    time.sleep(1.0)
                if self._stop.is_set():
                    break
                rc = self._proc.returncode
                err = self._proc.stderr.read()[-400:] if self._proc.stderr else ""
                log.warning("Live FFmpeg exited with code %s: %s", rc, err.strip())
            except Exception as e:  # noqa: BLE001
                log.warning("Stream recorder error: %s", e)

            attempts += 1
            if attempts > self.max_reconnects or self._stop.is_set():
                if self.on_status and not self._stop.is_set():
                    self.on_status("Stream ended or connection lost.")
                break
            time.sleep(min(2.0 * attempts, 12.0))
