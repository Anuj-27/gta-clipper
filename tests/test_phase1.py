"""Phase 1 tests: paths, config, database."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import Config  # noqa: E402
from core.paths import ROOT_DIR, ffmpeg_path, ffprobe_path  # noqa: E402
from database.db import Database  # noqa: E402


def test_paths():
    assert (ROOT_DIR / "main.py").exists()
    assert Path(ffmpeg_path()).exists()
    assert Path(ffprobe_path()).exists()


def test_config_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.json"
        c = Config(p)
        assert c.get("clip.pre_event") == 20
        c.set("clip.duration", 999)          # clamped
        assert c.get("clip.duration") == 180
        c2 = Config(p)
        assert c2.get("clip.duration") == 180
        w = c2.normalized_weights("video")
        assert w["chat"] == 0 and abs(sum(w.values()) - 1) < 1e-9
        w = c2.normalized_weights("live")
        assert abs(w["chat"] - 0.30) < 1e-9


def test_database():
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "h.db")
        jid = db.create_job("video", "https://youtu.be/x", "youtube", "T")
        db.update_job(jid, status="stopped", state={"stage": "download"})
        assert db.get_job(jid)["state"]["stage"] == "download"
        assert db.find_resumable_job("video", "https://youtu.be/x")["id"] == jid
        db.add_moment(jid, 1, 31, 0.8, ["loud"])
        assert len(db.moments_for_job(jid)) == 1
        f = Path(d) / "c.mp4"
        f.write_bytes(b"x")
        db.add_clip(job_id=jid, path=str(f), title="t", score=0.8, start=1, end=31, duration=30)
        assert len(db.list_clips()) == 1
        db.close()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
