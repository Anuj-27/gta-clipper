"""Phase 5 & 6 tests: Twitch/YouTube live chat integration, 10s audio+chat excitement detection, and clip triggering."""
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.audio_analyzer import AudioAnalyzer  # noqa: E402
from core.excitement_detector import LiveExcitementDetector  # noqa: E402
from core.twitch_chat import TwitchChatReader, parse_irc_line  # noqa: E402
from core.youtube_chat import YouTubeChatReader  # noqa: E402


def test_irc_parser_and_buffers():
    raw = "@badge-info=;badges=broadcaster/1;display-name=Shroud;user-type= :shroud!shroud@shroud.tmi.twitch.tv PRIVMSG #shroud :WHAT A CLUTCH CLIP IT!!"
    parsed = parse_irc_line(raw, now=100.0)
    assert parsed is not None
    assert parsed["author"] == "Shroud"
    assert "CLUTCH" in parsed["message"]

    tc = TwitchChatReader("shroud")
    tc.add_message("u1", "hello", timestamp=10.0)
    tc.add_message("u2", "OMG CLIP IT!!", timestamp=25.0)
    assert len(tc.messages_since(20.0, 30.0)) == 1

    yc = YouTubeChatReader("https://www.youtube.com/watch?v=jfKfPfyJRdk")
    yc.add_message("v1", "PogChamp", timestamp=50.0)
    assert len(yc.messages_since(45.0, 55.0)) == 1


def test_live_excitement_trigger():
    analyzer = AudioAnalyzer(sample_rate=16000)
    detector = LiveExcitementDetector()
    t_arr = np.linspace(0, 10, 16000 * 10, endpoint=False, dtype=np.float32)
    quiet = 0.015 * np.sin(2 * np.pi * 220 * t_arr)

    # First 3 calm 10-second windows establish baseline
    for w in range(3):
        st = analyzer.analyze_samples(quiet, start_time=w * 10.0)
        calm_chat = [{"author": f"u{i}", "message": "nice stream"} for i in range(3)]
        res = detector.evaluate_window((w + 1) * 10.0, st, calm_chat)
        assert not res["triggered"], f"Calm window should not trigger: {res}"

    # Window 4 (t=40s): sudden loud shouting + chat explosion
    loud = 0.55 * np.sin(2 * np.pi * 440 * t_arr) + 0.35 * np.sin(2 * np.pi * 1760 * t_arr)
    st_loud = analyzer.analyze_samples(loud.astype(np.float32), start_time=30.0)
    hype_chat = [{"author": f"u{i}", "message": "OMG NO WAY CLIP IT!! LULW POGGERS"} for i in range(18)]
    res_spike = detector.evaluate_window(40.0, st_loud, hype_chat, scene_change_score=0.7)
    assert res_spike["triggered"], f"Spike window should trigger: {res_spike}"
    assert res_spike["score"] > 0.55

    # Window 5 (t=50s): immediate repeat should be suppressed by cooldown
    res_cooldown = detector.evaluate_window(50.0, st_loud, hype_chat)
    assert not res_cooldown["triggered"], "Cooldown must prevent back-to-back duplicate trigger"


if __name__ == "__main__":
    test_irc_parser_and_buffers()
    print("PASS test_irc_parser_and_buffers")
    test_live_excitement_trigger()
    print("PASS test_live_excitement_trigger")
