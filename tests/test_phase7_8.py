"""Phase 7 & 8 tests: ASS animated subtitles, context-grounded titles, 1080x1920 vertical rendering, and faster-whisper."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.caption_generator import generate_ass_subtitles  # noqa: E402
from core.clip_generator import ClipGenerator  # noqa: E402
from core.config import Config  # noqa: E402
from core.media import duration, has_audio, run_ffmpeg, video_size  # noqa: E402
from core.title_generator import generate_clip_title  # noqa: E402
from core.transcription import Segment, Transcript, WhisperTranscriber, Word  # noqa: E402


def _sample_transcript() -> Transcript:
    words = [
        Word(0.2, 0.7, "Bro"),
        Word(0.7, 1.3, "that"),
        Word(1.3, 2.0, "heist"),
        Word(2.0, 2.8, "explosion"),
        Word(2.8, 3.4, "was"),
        Word(3.4, 4.3, "INSANE!"),
    ]
    return Transcript([Segment(0.2, 4.3, "Bro that heist explosion was INSANE!", words)], "en", "whisper")


def test_captions_and_titles():
    tr = _sample_transcript()
    with tempfile.TemporaryDirectory() as d:
        for style in ("bold_pop", "karaoke", "clean", "minimal"):
            ass = Path(d) / f"{style}.ass"
            generate_ass_subtitles(tr, ass, {"style": style, "words_per_line": 3})
            content = ass.read_text(encoding="utf-8")
            assert "PlayResX: 1080" in content and "PlayResY: 1920" in content
            assert "Dialogue:" in content and "INSANE" in content.upper()

        title = generate_clip_title(
            transcript=tr,
            chat_messages=[{"author": "u1", "message": "HEIST EXPLOSION CLIP IT"}],
            source_title="GTA RP Vault Heist Live",
            reasons=["chat spike x4.2", "loud audio"],
        )
        assert "heist explosion was INSANE!" in title
        assert len(title) <= 75


def test_vertical_clip_rendering():
    tr = _sample_transcript()
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "src_16_9.mp4"
        run_ffmpeg(
            [
                "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=6",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=6",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-shortest", str(src),
            ]
        )
        cfg = Config(Path(d) / "settings.json")
        gen = ClipGenerator(cfg, output_dir=Path(d) / "clips")
        res = gen.render_clip(
            source_video=src,
            start=0.5,
            end=5.5,
            title="Bro that heist explosion was INSANE!",
            transcript=tr,
            score=0.92,
            clip_index=1,
        )
        assert res.path.exists() and res.thumbnail.exists()
        w, h, _ = video_size(res.path)
        assert (w, h) == (1080, 1920), f"Expected 1080x1920 vertical clip, got {w}x{h}"
        assert has_audio(res.path)
        assert 4.4 <= duration(res.path) <= 5.5


if __name__ == "__main__":
    test_captions_and_titles()
    print("PASS test_captions_and_titles")
    test_vertical_clip_rendering()
    print("PASS test_vertical_clip_rendering")
