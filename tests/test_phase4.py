"""Phase 4 tests: rolling MPEG-TS segment buffer, cross-segment audio analysis, and 20s-pre + 10s-post clip extraction."""
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.media import duration, run_ffmpeg  # noqa: E402
from core.rolling_buffer import RollingBuffer  # noqa: E402


def test_rolling_buffer_extraction():
    with tempfile.TemporaryDirectory() as d:
        buf_dir = Path(d) / "buffer"
        buf_dir.mkdir(parents=True, exist_ok=True)
        # Generate 40 seconds of segmented MPEG-TS footage (8 x 5-second segments) via FFmpeg segment muxer
        run_ffmpeg(
            [
                "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=40",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=40",
                "-c:v", "libx264", "-preset", "ultrafast", "-g", "25", "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-f", "segment", "-segment_time", "5", "-segment_format", "mpegts",
                "-reset_timestamps", "1",
                str(buf_dir / "seg_%06d.ts"),
            ]
        )
        # Ensure mtime is slightly in the past so even the final segment is recognized as closed
        past = time.time() - 10
        for p in buf_dir.glob("seg_*.ts"):
            import os
            os.utime(p, (past, past))

        rb = RollingBuffer(buf_dir, segment_seconds=5.0, max_minutes=10.0)
        added = rb.scan()
        assert len(added) == 8, f"Expected 8 segments, got {len(added)}"
        assert abs(rb.latest_time - 40.0) < 2.0

        # Decode 10-second audio window across segment boundaries (18s..28s)
        samples = rb.load_window_audio(18.0, 28.0, sr=16000)
        assert len(samples) >= 16000 * 8

        # Event at t=27s -> extract 20s before (7s) and 10s after (37s) = 30s clip across 7 segments!
        out_clip = Path(d) / "event_clip.mp4"
        rb.extract_clip(7.0, 37.0, out_clip)
        clip_dur = duration(out_clip)
        assert out_clip.exists() and 28.0 <= clip_dur <= 31.5, f"Expected ~30s clip, got {clip_dur:.2f}s"


if __name__ == "__main__":
    test_rolling_buffer_extraction()
    print("PASS test_rolling_buffer_extraction")
