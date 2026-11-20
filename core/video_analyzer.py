"""YouTube video downloading, metadata, subtitle retrieval, scene cut detection, and smart crop tracking."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import threading
from typing import Any, Callable

import cv2
import numpy as np
import yt_dlp

from core.media import Cancelled, duration, is_valid_media
from core.paths import BIN_DIR, TEMP_DIR, ffmpeg_path
from core.transcription import Transcript, WhisperTranscriber, load_subtitle_file

log = logging.getLogger(__name__)

QUALITY_HEIGHTS = {
    "best": None,
    "1080p": 1080,
    "720p": 720,
    "480p": 480,
    "360p": 360,
}


@dataclass
class VideoMetadata:
    video_id: str
    title: str
    uploader: str
    duration: float
    is_live: bool
    webpage_url: str
    thumbnail: str = ""


def _ydl_base_opts() -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "ffmpeg_location": str(BIN_DIR),
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 25,
    }
    deno = BIN_DIR / "deno.exe"
    if deno.exists():
        opts["js_runtimes"] = {"deno": {"path": str(deno)}}
    return opts


def fetch_metadata(url: str) -> VideoMetadata:
    """Fetch video or stream metadata via yt-dlp without downloading the media."""
    opts = _ydl_base_opts()
    opts["extract_flat"] = False
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise RuntimeError("Could not retrieve video metadata.")
    if "entries" in info and info["entries"]:
        info = info["entries"][0]
    live_status = info.get("live_status") or ""
    is_live = bool(info.get("is_live") or live_status in ("is_live", "is_upcoming"))
    return VideoMetadata(
        video_id=str(info.get("id") or "video"),
        title=str(info.get("title") or "Untitled"),
        uploader=str(info.get("uploader") or info.get("channel") or ""),
        duration=float(info.get("duration") or 0.0),
        is_live=is_live,
        webpage_url=str(info.get("webpage_url") or url),
        thumbnail=str(info.get("thumbnail") or ""),
    )


def download_youtube_video(
    url: str,
    quality: str = "720p",
    work_dir: Path | None = None,
    stop: threading.Event | None = None,
    progress: Callable[[float, str], None] | None = None,
    preferred_lang: str = "en",
) -> tuple[Path, VideoMetadata, Path | None]:
    """Download video + available subtitles. Resumes cleanly if file already exists."""
    meta = fetch_metadata(url)
    if meta.is_live:
        raise RuntimeError("This URL is an active livestream. Switch to Live Stream Mode.")
    job_dir = work_dir or (TEMP_DIR / f"video_{meta.video_id}")
    job_dir.mkdir(parents=True, exist_ok=True)

    height = QUALITY_HEIGHTS.get(quality, 720)
    tag = quality if quality in QUALITY_HEIGHTS else "720p"
    target = job_dir / f"{meta.video_id}_{tag}.mp4"

    # Try downloading subtitles alongside (fast, non-fatal)
    sub_file = _find_existing_subtitle(job_dir, meta.video_id)
    if sub_file is None:
        sub_file = _download_subtitles(url, job_dir, meta.video_id, preferred_lang or "en")

    if is_valid_media(target, min_duration=1.0):
        log.info("Reusing already downloaded video: %s", target)
        if progress:
            progress(1.0, "Video already downloaded")
        if meta.duration <= 0:
            meta.duration = duration(target)
        return target, meta, sub_file

    if height:
        fmt = (
            f"bestvideo[height<={height}][vcodec^=avc1]+bestaudio[acodec^=mp4a]/"
            f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"
        )
    else:
        fmt = "bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]/bestvideo+bestaudio/best"

    def _hook(d: dict[str, Any]) -> None:
        if stop is not None and stop.is_set():
            raise Cancelled("Download cancelled by user")
        if d.get("status") == "downloading" and progress:
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            frac = min(done / total, 0.99) if total else 0.0
            spd = d.get("speed")
            spd_str = f" ({spd / 1_048_576:.1f} MB/s)" if spd else ""
            progress(frac, f"Downloading video ({quality}){spd_str}…")
        elif d.get("status") == "finished" and progress:
            progress(0.99, "Merging audio & video streams…")

    opts = _ydl_base_opts()
    opts.update(
        {
            "format": fmt,
            "merge_output_format": "mp4",
            "outtmpl": str(job_dir / f"{meta.video_id}_{tag}.%(ext)s"),
            "continuedl": True,
            "nopart": False,
            "progress_hooks": [_hook],
            "ffmpeg_location": str(Path(ffmpeg_path()).parent),
        }
    )

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    if not is_valid_media(target):
        # Look for any produced media file matching prefix
        candidates = sorted(job_dir.glob(f"{meta.video_id}_{tag}.*"), key=lambda p: p.stat().st_size, reverse=True)
        candidates = [c for c in candidates if c.suffix.lower() in (".mp4", ".mkv", ".webm") and is_valid_media(c)]
        if not candidates:
            raise RuntimeError("Downloaded video file is missing or corrupted.")
        target = candidates[0]

    if meta.duration <= 0:
        meta.duration = duration(target)
    if progress:
        progress(1.0, "Download complete")
    return target, meta, sub_file


def _find_existing_subtitle(job_dir: Path, video_id: str) -> Path | None:
    for ext in (".json3", ".vtt"):
        matches = sorted(job_dir.glob(f"{video_id}*{ext}"))
        if matches:
            return matches[0]
    return None


def _download_subtitles(url: str, job_dir: Path, video_id: str, lang: str = "en") -> Path | None:
    langs = [lang, f"{lang}-*", "en", "en-*"] if lang != "en" else ["en", "en-*", "en-US", "en-GB"]
    opts = _ydl_base_opts()
    opts.update(
        {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": langs,
            "subtitlesformat": "json3/vtt/best",
            "outtmpl": str(job_dir / f"{video_id}.%(ext)s"),
        }
    )
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as e:  # noqa: BLE001
        log.debug("Subtitle fetch skipped: %s", e)
    return _find_existing_subtitle(job_dir, video_id)


def obtain_transcript(
    video_path: Path,
    subtitle_path: Path | None,
    whisper_cfg: dict[str, Any],
    cache_path: Path | None = None,
    stop: threading.Event | None = None,
    progress: Callable[[float], None] | None = None,
    status: Callable[[str], None] | None = None,
) -> Transcript:
    """Use cached transcript or available YouTube subtitles first, then fall back to local faster-whisper."""
    if cache_path and cache_path.exists():
        try:
            tr = Transcript.load(cache_path)
            if tr.segments:
                return tr
        except Exception:  # noqa: BLE001
            pass

    if subtitle_path and subtitle_path.exists():
        try:
            if status:
                status(f"Parsing available subtitles ({subtitle_path.name})…")
            segs = load_subtitle_file(subtitle_path)
            if segs:
                tr = Transcript(segs, language=whisper_cfg.get("language") or "en", source="subtitles")
                if cache_path:
                    tr.save(cache_path)
                if progress:
                    progress(1.0)
                return tr
        except Exception as e:  # noqa: BLE001
            log.warning("Subtitle parsing failed (%s); falling back to faster-whisper.", e)

    if status:
        status("Generating transcript with faster-whisper…")
    transcriber = WhisperTranscriber(
        model=whisper_cfg.get("model", "small"),
        device=whisper_cfg.get("device", "auto"),
        compute_type=whisper_cfg.get("compute_type", "auto"),
        language=whisper_cfg.get("language", ""),
    )
    dur = duration(video_path)
    tr = transcriber.transcribe(video_path, stop=stop, progress=progress, total_duration=dur, status=status)
    if cache_path:
        tr.save(cache_path)
    return tr


def detect_scene_changes(
    video_path: str | Path,
    stop: threading.Event | None = None,
    threshold: float = 0.38,
    sample_fps: float = 4.0,
) -> list[float]:
    """Detect scene/action cuts quickly by sampling frames at `sample_fps` and comparing HSV histograms."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(int(round(fps / sample_fps)), 1)
    prev_hist = None
    cuts: list[float] = []
    frame_idx = 0
    try:
        while True:
            if stop is not None and stop.is_set():
                raise Cancelled()
            ok = cap.grab()
            if not ok:
                break
            if frame_idx % step == 0:
                ret, frame = cap.retrieve()
                if not ret or frame is None:
                    break
                small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
                hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
                hist = cv2.calcHist([hsv], [0, 1], None, [24, 16], [0, 180, 0, 256])
                cv2.normalize(hist, hist)
                if prev_hist is not None:
                    dist = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA))
                    if dist >= threshold:
                        cuts.append(round(frame_idx / fps, 2))
                prev_hist = hist
            frame_idx += 1
    finally:
        cap.release()
    return cuts


_FACE_CASCADE: cv2.CascadeClassifier | None = None


def _get_face_cascade() -> cv2.CascadeClassifier | None:
    global _FACE_CASCADE
    if _FACE_CASCADE is None:
        xml = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        if xml.exists():
            _FACE_CASCADE = cv2.CascadeClassifier(str(xml))
    return _FACE_CASCADE


def estimate_subject_center_x(
    video_path: str | Path,
    start: float = 0.0,
    end: float | None = None,
    samples: int = 14,
) -> float:
    """Estimate normalized horizontal center (0..1) of the main subject (face or visual saliency/motion)."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return 0.5
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        vid_dur = total_frames / fps if total_frames > 0 else 30.0
        t0 = max(0.0, start)
        t1 = min(vid_dur, end if end is not None else vid_dur)
        if t1 <= t0:
            t0, t1 = 0.0, max(vid_dur, 1.0)

        cascade = _get_face_cascade()
        xs_face: list[float] = []
        xs_motion: list[float] = []
        prev_gray = None

        for ts in np.linspace(t0, max(t1 - 0.05, t0), num=max(samples, 3)):
            cap.set(cv2.CAP_PROP_POS_MSEC, float(ts * 1000.0))
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            h, w = frame.shape[:2]
            scale = 360.0 / max(w, 1)
            small = cv2.resize(frame, (360, max(int(h * scale), 90)), interpolation=cv2.INTER_AREA)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

            if cascade is not None:
                faces = cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=4, minSize=(24, 24))
                if len(faces) > 0:
                    # Pick largest face
                    fx, fy, fw, fh = max(faces, key=lambda b: b[2] * b[3])
                    xs_face.append(float((fx + fw * 0.5) / gray.shape[1]))

            if prev_gray is not None and prev_gray.shape == gray.shape:
                diff = cv2.absdiff(gray, prev_gray)
                col_energy = diff.mean(axis=0)
                total_e = float(col_energy.sum())
                if total_e > 1e-3:
                    coords = np.linspace(0.0, 1.0, len(col_energy))
                    cx = float(np.sum(coords * col_energy) / total_e)
                    xs_motion.append(cx)
            prev_gray = gray

        if len(xs_face) >= 2:
            return float(np.clip(np.median(xs_face), 0.15, 0.85))
        if xs_face and xs_motion:
            return float(np.clip(0.65 * np.median(xs_face) + 0.35 * np.median(xs_motion), 0.18, 0.82))
        if xs_motion:
            return float(np.clip(0.7 * np.median(xs_motion) + 0.3 * 0.5, 0.2, 0.8))
        return 0.5
    finally:
        cap.release()
