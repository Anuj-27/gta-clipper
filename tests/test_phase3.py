"""Phase 3 tests: audio analysis, scene cut detection, subject tracking, candidate ranking, and YouTube download."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.audio_analyzer import AudioAnalyzer  # noqa: E402
from core.excitement_detector import VideoExcitementDetector  # noqa: E402
from core.media import duration, run_ffmpeg, video_size  # noqa: E402
from core.transcription import Segment, Transcript, Word, parse_json3, parse_vtt  # noqa: E402
from core.video_analyzer import (  # noqa: E402
    detect_scene_changes,
    download_youtube_video,
    estimate_subject_center_x,
    fetch_metadata,
)


def _make_synthetic_video(path: Path) -> None:
    """Create a 12-second 640x360 video with a quiet start, loud burst at 6..9s, and color change at 6s."""
    # Video: blue 0..6s, bright red/yellow 6..12s; Audio: -35 dB sine 0..6s, -4 dB dual tone 6..9s, -35 dB 9..12s
    vf = (
        "color=c=navy:s=640x360:d=12,drawbox=enable='between(t,6,9)':x=380:y=80:w=180:h=200:c=yellow@1:t=fill"
    )
    af = (
        "aevalsrc='if(between(t,6,9),0.45*sin(2*PI*440*t)+0.35*sin(2*PI*1320*t),0.02*sin(2*PI*220*t))':s=16000:d=12"
    )
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", vf,
            "-f", "lavfi", "-i", af,
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ]
    )


def test_synthetic_analysis():
    with tempfile.TemporaryDirectory() as d:
        vp = Path(d) / "synth.mp4"
        _make_synthetic_video(vp)
        dur = duration(vp)
        w, h, _ = video_size(vp)
        assert abs(dur - 12.0) < 0.8 and (w, h) == (640, 360)

        analyzer = AudioAnalyzer()
        timeline = analyzer.analyze_timeline(vp, step_seconds=1.0)
        assert len(timeline) >= 11
        quiet_score = max(s.excitement for s in timeline[:5])
        loud_score = max(s.excitement for s in timeline[6:9])
        assert loud_score > quiet_score + 0.2, f"Expected loud_score ({loud_score}) > quiet_score ({quiet_score})"

        cuts = detect_scene_changes(vp, threshold=0.25)
        assert any(5.2 <= c <= 9.8 for c in cuts), f"Expected scene cut near 6s..9s, got {cuts}"

        cx = estimate_subject_center_x(vp, start=5.5, end=9.5)
        assert 0.15 <= cx <= 0.85

        segs = [
            Segment(0.5, 4.0, "Walking around the city streets quietly.", [Word(0.5, 4.0, "Walking")]),
            Segment(6.0, 9.0, "OMG no way! That heist explosion was insane!! [laughter]", [Word(6.0, 9.0, "OMG")]),
        ]
        tr = Transcript(segs, "en", "subtitles")
        det = VideoExcitementDetector()
        cands, series = det.detect_candidates(dur, timeline, tr, cuts, clip_duration=5, max_clips=2)
        assert len(cands) >= 1
        best = cands[0]
        assert 5.0 <= best.peak_time <= 9.5, f"Expected peak in 5..9.5s, got {best}"


def test_subtitle_parsers():
    with tempfile.TemporaryDirectory() as d:
        j3 = Path(d) / "sub.json3"
        j3.write_text(
            json.dumps(
                {
                    "events": [
                        {"tStartMs": 1000, "dDurationMs": 2000, "segs": [{"utf8": "Hello "}, {"utf8": "world!", "tOffsetMs": 900}]},
                        {"tStartMs": 3500, "dDurationMs": 1500, "segs": [{"utf8": "[laughter]"}]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        segs = parse_json3(j3)
        assert len(segs) >= 1 and "Hello" in segs[0].text

        vtt = Path(d) / "sub.vtt"
        vtt.write_text(
            "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nWhat an insane clip!\n\n00:00:03.000 --> 00:00:05.000\nWhat an insane clip!\nBro look at that!\n",
            encoding="utf-8",
        )
        vsegs = parse_vtt(vtt)
        assert len(vsegs) >= 1 and "Bro look at that!" in " ".join(s.text for s in vsegs)


def test_real_youtube_download():
    with tempfile.TemporaryDirectory() as d:
        url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
        meta = fetch_metadata(url)
        assert meta.video_id == "jNQXAC9IVRw" and not meta.is_live
        vpath, meta2, sub = download_youtube_video(url, quality="360p", work_dir=Path(d))
        assert vpath.exists() and duration(vpath) > 15.0
        print(f"Downloaded real YouTube video: {meta2.title} ({duration(vpath):.1f}s) sub={sub}")


if __name__ == "__main__":
    test_synthetic_analysis()
    print("PASS test_synthetic_analysis")
    test_subtitle_parsers()
    print("PASS test_subtitle_parsers")
    test_real_youtube_download()
    print("PASS test_real_youtube_download")
