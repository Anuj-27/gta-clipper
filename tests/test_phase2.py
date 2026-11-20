"""Phase 2 tests: URL validation (UI is smoke-tested separately)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.url_utils import parse_url, validate_for_mode  # noqa: E402


def test_youtube_variants():
    for u in (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10",
        "youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?si=abc",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
    ):
        p = parse_url(u)
        assert p.valid and p.platform == "youtube" and p.ident == "dQw4w9WgXcQ", u
        assert p.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    p = parse_url("https://www.youtube.com/live/jfKfPfyJRdk?feature=share")
    assert p.kind == "live" and p.ident == "jfKfPfyJRdk"
    p = parse_url("https://www.youtube.com/@LofiGirl/live")
    assert p.kind == "channel_live" and p.url.endswith("/@LofiGirl/live")


def test_twitch_variants():
    p = parse_url("https://www.twitch.tv/Shroud")
    assert p.valid and p.platform == "twitch" and p.ident == "shroud" and p.kind == "channel_live"
    assert parse_url("twitch.tv/videos/123456").kind == "vod"
    assert not parse_url("https://twitch.tv/directory").valid


def test_invalid():
    for u in ("", "hello", "https://vimeo.com/123", "https://youtube.com/watch?v=short"):
        assert not parse_url(u).valid, u


def test_modes():
    assert validate_for_mode("https://twitch.tv/xqc", "live").valid
    assert not validate_for_mode("https://twitch.tv/xqc", "video").valid
    assert validate_for_mode("https://youtu.be/dQw4w9WgXcQ", "video").valid
    assert validate_for_mode("https://youtu.be/dQw4w9WgXcQ", "live").valid  # may be a live id
    assert not validate_for_mode("https://twitch.tv/videos/1", "live").valid


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
