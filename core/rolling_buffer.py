"""Rolling video buffer backed by timestamped MPEG-TS segments on disk."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
import threading
import time

import numpy as np

from core.media import duration, load_audio, run_ffmpeg

log = logging.getLogger(__name__)

_SEG_RE = re.compile(r"seg_(\d+)\.ts$", re.I)


@dataclass
class BufferSegment:
    index: int
    path: Path
    start_time: float      # stream-relative seconds
    end_time: float        # stream-relative seconds
    wall_mtime: float      # wall-clock creation/modification timestamp

    @property
    def duration(self) -> float:
        return max(0.0, self.end_time - self.start_time)


class RollingBuffer:
    """Maintains a rolling window of completed MPEG-TS segments and extracts arbitrary time ranges."""

    def __init__(self, buffer_dir: Path, segment_seconds: float = 5.0, max_minutes: float = 10.0):
        self.buffer_dir = Path(buffer_dir)
        self.buffer_dir.mkdir(parents=True, exist_ok=True)
        self.segment_seconds = float(segment_seconds)
        self.max_seconds = float(max_minutes) * 60.0
        self._lock = threading.RLock()
        self._segments: dict[int, BufferSegment] = {}
        self._timeline_cursor: float = 0.0

    def scan(self) -> list[BufferSegment]:
        """Scan buffer directory for completed segments (excludes the currently-writing segment)."""
        with self._lock:
            files = sorted(
                (p for p in self.buffer_dir.glob("seg_*.ts") if _SEG_RE.search(p.name)),
                key=lambda p: int(_SEG_RE.search(p.name).group(1)),  # type: ignore[union-attr]
            )
            # If at least 2 segments exist, the newest one may still be open by FFmpeg;
            # also accept the newest segment if its mtime is older than segment_seconds * 0.95.
            now = time.time()
            completed: list[Path] = []
            for idx_pos, f in enumerate(files):
                try:
                    st = f.stat()
                except OSError:
                    continue
                if st.st_size < 1024:
                    continue
                is_last = idx_pos == len(files) - 1
                if not is_last or (now - st.st_mtime) >= self.segment_seconds * 0.9:
                    completed.append(f)

            new_added: list[BufferSegment] = []
            for f in completed:
                m = _SEG_RE.search(f.name)
                if not m:
                    continue
                seg_idx = int(m.group(1))
                if seg_idx in self._segments:
                    continue
                try:
                    seg_dur = duration(f)
                    if seg_dur <= 0.1:
                        seg_dur = self.segment_seconds
                except Exception:  # noqa: BLE001
                    seg_dur = self.segment_seconds
                try:
                    mtime = f.stat().st_mtime
                except OSError:
                    mtime = now
                start_t = self._timeline_cursor
                end_t = start_t + seg_dur
                self._timeline_cursor = end_t
                seg = BufferSegment(seg_idx, f, start_t, end_t, mtime)
                self._segments[seg_idx] = seg
                new_added.append(seg)

            self._prune_old()
            return new_added

    def _prune_old(self) -> None:
        if not self._segments:
            return
        latest_end = max(s.end_time for s in self._segments.values())
        cutoff = latest_end - self.max_seconds
        to_remove = [idx for idx, s in self._segments.items() if s.end_time < cutoff]
        for idx in to_remove:
            seg = self._segments.pop(idx, None)
            if seg and seg.path.exists():
                try:
                    seg.path.unlink()
                except OSError:
                    pass

    @property
    def latest_time(self) -> float:
        with self._lock:
            if not self._segments:
                return 0.0
            return max(s.end_time for s in self._segments.values())

    @property
    def earliest_time(self) -> float:
        with self._lock:
            if not self._segments:
                return 0.0
            return min(s.start_time for s in self._segments.values())

    def segments_covering(self, start_time: float, end_time: float) -> list[BufferSegment]:
        with self._lock:
            ordered = sorted(self._segments.values(), key=lambda s: s.index)
            return [s for s in ordered if s.end_time > start_time and s.start_time < end_time]

    def load_window_audio(
        self,
        start_time: float,
        end_time: float,
        sr: int = 16000,
        stop: threading.Event | None = None,
    ) -> np.ndarray:
        """Decode mono float32 audio across the segments covering [start_time, end_time]."""
        segs = self.segments_covering(start_time, end_time)
        if not segs:
            return np.zeros(0, dtype=np.float32)
        pieces: list[np.ndarray] = []
        for s in segs:
            if not s.path.exists():
                continue
            local_start = max(0.0, start_time - s.start_time)
            local_end = min(s.duration, end_time - s.start_time)
            if local_end <= local_start:
                continue
            try:
                arr = load_audio(s.path, sr=sr, start=local_start, end=local_end, stop=stop)
                if len(arr):
                    pieces.append(arr)
            except Exception as e:  # noqa: BLE001
                log.debug("Audio decode skipped on segment %s: %s", s.path.name, e)
        return np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)

    def extract_clip(
        self,
        start_time: float,
        end_time: float,
        output_path: Path,
        stop: threading.Event | None = None,
    ) -> Path:
        """Concatenate and trim buffered MPEG-TS segments covering [start_time, end_time] into an MP4 file."""
        self.scan()
        segs = self.segments_covering(start_time, end_time)
        if not segs:
            raise RuntimeError(f"No buffered footage available for {start_time:.1f}s..{end_time:.1f}s")

        first_start = segs[0].start_time
        trim_offset = max(0.0, start_time - first_start)
        clip_dur = max(1.0, end_time - max(start_time, first_start))

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        concat_file = self.buffer_dir / f"concat_{int(time.time() * 1000)}.txt"
        tmp_out = output_path.with_suffix(".part.mp4")
        try:
            lines = []
            for s in segs:
                if s.path.exists():
                    safe_path = str(s.path.resolve()).replace("\\", "/").replace("'", "'\\''")
                    lines.append(f"file '{safe_path}'")
            if not lines:
                raise RuntimeError("Buffered segments were removed before extraction.")
            concat_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

            run_ffmpeg(
                [
                    "-f", "concat", "-safe", "0",
                    "-i", str(concat_file),
                    "-ss", f"{trim_offset:.3f}",
                    "-t", f"{clip_dur:.3f}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart",
                    str(tmp_out),
                ],
                stop=stop,
            )
            tmp_out.replace(output_path)
            return output_path
        finally:
            if concat_file.exists():
                try:
                    concat_file.unlink()
                except OSError:
                    pass
            if tmp_out.exists():
                try:
                    tmp_out.unlink()
                except OSError:
                    pass
