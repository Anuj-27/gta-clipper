"""Phase 9 end-to-end integration test: full VideoClipJob + faster-whisper + ClipGallery UI verification."""
import os
import sys
import threading
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import get_config  # noqa: E402
from core.media import load_audio, video_size  # noqa: E402
from core.pipeline import VideoClipJob  # noqa: E402
from core.transcription import WhisperTranscriber  # noqa: E402
from database.db import get_db  # noqa: E402


def test_full_video_pipeline_and_whisper():
    cfg = get_config()
    db = get_db()
    events: list[tuple[str, dict]] = []

    def _emit(name: str, payload: dict) -> None:
        events.append((name, payload))

    stop = threading.Event()
    url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    job = VideoClipJob(
        url=url,
        cfg=cfg,
        db=db,
        emit=_emit,
        stop=stop,
        options={"quality": "360p", "duration": 12, "max_clips": 1},
    )
    job.run()

    clip_events = [p for n, p in events if n == "clip"]
    fin_events = [p for n, p in events if n == "finished"]
    assert fin_events and fin_events[-1].get("ok"), f"Job did not finish cleanly: {fin_events}"
    assert len(clip_events) >= 1, "Expected at least 1 generated vertical clip"

    clip_info = clip_events[0]
    clip_path = Path(clip_info["path"])
    thumb_path = Path(clip_info["thumbnail"])
    assert clip_path.exists() and thumb_path.exists()
    w, h, _ = video_size(clip_path)
    assert (w, h) == (1080, 1920)
    print(f"Generated 1080x1920 vertical clip: {clip_path.name} | Title: {clip_info['title']}")

    # Verify faster-whisper local speech transcription on the generated clip's audio
    tr = WhisperTranscriber(model="tiny", device="cpu", compute_type="int8")
    audio = load_audio(clip_path, sr=16000)
    w_res = tr.transcribe(audio)
    assert len(w_res.segments) >= 1 and len(w_res.words) >= 3
    print(f"Whisper tiny transcribed ({w_res.language}): {' '.join(s.text for s in w_res.segments)}")


if __name__ == "__main__":
    test_full_video_pipeline_and_whisper()
    print("PASS test_full_video_pipeline_and_whisper")
