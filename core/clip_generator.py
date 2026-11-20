"""Vertical 9:16 clip renderer using FFmpeg: subject-aware crop, animated ASS captions, smooth audio, thumbnail."""
from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re
import shutil
import threading
import time
from typing import Any, Callable

from core.caption_generator import generate_ass_subtitles
from core.config import Config, get_config
from core.media import duration, has_audio, is_valid_media, run_ffmpeg, video_size
from core.paths import CLIPS_DIR, TEMP_DIR
from core.transcription import Transcript
from core.video_analyzer import estimate_subject_center_x

log = logging.getLogger(__name__)


@dataclass
class GeneratedClipResult:
    path: Path
    thumbnail: Path
    title: str
    start: float
    end: float
    duration: float
    score: float
    transcript_text: str


def _safe_filename(title: str, max_len: int = 55) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "", title).strip()
    cleaned = re.sub(r"\s+", "_", cleaned).strip("._")
    return (cleaned[:max_len] or "clip").rstrip("._")


def _escape_filter_path(p: Path) -> str:
    """Escape a Windows path for FFmpeg's `ass=filename=...` filter."""
    s = str(p.resolve()).replace("\\", "/")
    s = s.replace(":", r"\:").replace("'", r"\'").replace(",", r"\,")
    return s


class ClipGenerator:
    """Renders 9:16 vertical clips with subject-aware cropping, animated subtitles, and smooth audio."""

    def __init__(self, cfg: Config | None = None, output_dir: Path = CLIPS_DIR):
        self.cfg = cfg or get_config()
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def render_clip(
        self,
        source_video: str | Path,
        start: float,
        end: float,
        title: str,
        transcript: Transcript | None = None,
        score: float = 0.0,
        clip_index: int = 1,
        pre_trimmed: bool = False,
        stop: threading.Event | None = None,
        progress: Callable[[float], None] | None = None,
    ) -> GeneratedClipResult:
        """Render a vertical clip from `source_video`.

        If `pre_trimmed` is True (e.g. extracted from RollingBuffer), `source_video` already covers [start, end].
        """
        src = Path(source_video)
        out_w = int(self.cfg.get("output.width", 1080))
        out_h = int(self.cfg.get("output.height", 1920))
        crf = int(self.cfg.get("output.video_crf", 20))
        preset = str(self.cfg.get("output.preset", "veryfast"))
        audio_br = str(self.cfg.get("output.audio_bitrate", "192k"))
        smart_crop = bool(self.cfg.get("output.smart_crop", True))
        bg_music_on = bool(self.cfg.get("output.background_music", False))
        bg_music_path = str(self.cfg.get("output.music_path", "")).strip()
        bg_music_vol = float(self.cfg.get("output.music_volume", 0.12))
        captions_cfg = dict(self.cfg.get("captions") or {})

        clip_dur = max(1.0, float(end - start)) if not pre_trimmed else max(1.0, duration(src))
        src_w, src_h, _ = video_size(src)
        src_has_audio = has_audio(src)

        # Subject-aware horizontal center in source video coordinates (0..1)
        if smart_crop and (src_w / max(src_h, 1)) > (out_w / max(out_h, 1)):
            cx = estimate_subject_center_x(
                src,
                start=0.0 if pre_trimmed else start,
                end=clip_dur if pre_trimmed else end,
            )
        else:
            cx = 0.5

        # Scale so frame fills out_w x out_h, then crop around subject center cx
        target_aspect = out_w / float(out_h)
        src_aspect = src_w / float(max(src_h, 1))
        if src_aspect >= target_aspect:
            # Source is wider than 9:16 -> scale height to out_h, crop width to out_w
            scaled_w = max(out_w, int(round(out_h * src_aspect / 2) * 2))
            max_offset = max(0, scaled_w - out_w)
            crop_x = int(round(max(0.0, min(1.0, cx)) * max_offset))
            crop_x = max(0, min(max_offset, crop_x))
            vf_chain = [
                f"scale={scaled_w}:{out_h}:flags=lanczos",
                f"crop={out_w}:{out_h}:{crop_x}:0",
                "setsar=1",
            ]
        else:
            # Source is already vertical or narrower -> scale width to out_w, crop height to out_h
            scaled_h = max(out_h, int(round(out_w / max(src_aspect, 0.1) / 2) * 2))
            crop_y = max(0, (scaled_h - out_h) // 2)
            vf_chain = [
                f"scale={out_w}:{scaled_h}:flags=lanczos",
                f"crop={out_w}:{out_h}:0:{crop_y}",
                "setsar=1",
            ]

        # Shift transcript to clip-local 0..clip_dur and generate ASS subtitles
        if transcript is not None:
            local_tr = (
                transcript
                if pre_trimmed
                else transcript.shifted(offset=start, clip_start=start, clip_end=end)
            )
        else:
            local_tr = Transcript([], "", "")

        ass_temp: Path | None = None
        if captions_cfg.get("enabled", True) and local_tr.words:
            # Write ASS file inside TEMP_DIR with simple ASCII name for clean FFmpeg filter escaping
            ass_temp = TEMP_DIR / f"sub_{int(time.time() * 1000)}_{clip_index}.ass"
            generate_ass_subtitles(local_tr, ass_temp, captions_cfg, width=out_w, height=out_h)
            vf_chain.append(f"ass=filename='{_escape_filter_path(ass_temp)}'")

        vf_str = ",".join(vf_chain)

        # Build output filename
        ts_tag = time.strftime("%Y%m%d_%H%M%S")
        slug = _safe_filename(title)
        final_mp4 = self.output_dir / f"{ts_tag}_{clip_index:02d}_{slug}.mp4"
        temp_mp4 = final_mp4.with_suffix(".part.mp4")
        thumb_jpg = final_mp4.with_suffix(".jpg")
        meta_txt = final_mp4.with_suffix(".txt")

        args: list[str] = []
        if not pre_trimmed:
            args += ["-ss", f"{max(0.0, start):.3f}"]
        args += ["-i", str(src)]
        if not pre_trimmed:
            args += ["-t", f"{clip_dur:.3f}"]

        fade_out_st = max(0.0, clip_dur - 0.35)
        af_base = f"afade=t=in:st=0:d=0.20,afade=t=out:st={fade_out_st:.2f}:d=0.35"

        use_music = bg_music_on and bg_music_path and Path(bg_music_path).exists() and src_has_audio
        if use_music:
            args += ["-stream_loop", "-1", "-i", bg_music_path]
            fc = (
                f"[0:v]{vf_str}[vout];"
                f"[0:a]{af_base}[a0];"
                f"[1:a]volume={bg_music_vol:.3f}[a1];"
                f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=2[aout]"
            )
            args += ["-filter_complex", fc, "-map", "[vout]", "-map", "[aout]"]
        else:
            args += ["-vf", vf_str]
            if src_has_audio:
                args += ["-af", af_base]

        args += [
            "-c:v", "libx264",
            "-preset", preset,
            "-crf", str(crf),
            "-pix_fmt", "yuv420p",
        ]
        if src_has_audio:
            args += ["-c:a", "aac", "-b:a", audio_br, "-ar", "48000"]
        args += ["-movflags", "+faststart", str(temp_mp4)]

        try:
            run_ffmpeg(args, stop=stop, progress=progress, total=clip_dur)
            if not is_valid_media(temp_mp4, min_duration=0.5):
                raise RuntimeError("Rendered clip failed validation check.")
            shutil.move(str(temp_mp4), str(final_mp4))
        finally:
            if ass_temp and ass_temp.exists():
                try:
                    ass_temp.unlink()
                except OSError:
                    pass
            if temp_mp4.exists():
                try:
                    temp_mp4.unlink()
                except OSError:
                    pass

        # Extract vertical thumbnail near the middle/climax of the rendered clip
        actual_dur = duration(final_mp4) or clip_dur
        thumb_t = min(max(actual_dur * 0.55, 0.2), max(actual_dur - 0.2, 0.2))
        try:
            run_ffmpeg(
                [
                    "-ss", f"{thumb_t:.2f}",
                    "-i", str(final_mp4),
                    "-frames:v", "1",
                    "-q:v", "3",
                    str(thumb_jpg),
                ],
                stop=stop,
            )
        except Exception as e:  # noqa: BLE001
            log.debug("Thumbnail extraction warning: %s", e)

        transcript_text = " ".join(s.text.strip() for s in local_tr.segments if s.text.strip())
        meta_txt.write_text(
            f"Title: {title}\n"
            f"Score: {score:.2f}\n"
            f"Source range: {start:.1f}s - {end:.1f}s ({actual_dur:.1f}s)\n\n"
            f"Transcript:\n{transcript_text}\n",
            encoding="utf-8",
        )

        return GeneratedClipResult(
            path=final_mp4,
            thumbnail=thumb_jpg,
            title=title,
            start=start,
            end=end,
            duration=actual_dur,
            score=score,
            transcript_text=transcript_text,
        )
