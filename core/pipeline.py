"""End-to-end orchestration pipelines for Mode A (Live Stream Clipper) and Mode B (YouTube Video Clipper)."""
from __future__ import annotations

import logging
from pathlib import Path
import threading
import time
from typing import Any, Callable

from core.audio_analyzer import AudioAnalyzer
from core.clip_generator import ClipGenerator
from core.config import Config
from core.excitement_detector import LiveExcitementDetector, VideoExcitementDetector
from core.media import Cancelled
from core.paths import TEMP_DIR
from core.rolling_buffer import RollingBuffer
from core.stream_recorder import StreamRecorder
from core.title_generator import generate_clip_title
from core.transcription import Transcript, WhisperTranscriber
from core.twitch_chat import TwitchChatReader
from core.url_utils import parse_url
from core.video_analyzer import (
    detect_scene_changes,
    download_youtube_video,
    obtain_transcript,
)
from core.youtube_chat import YouTubeChatReader
from database.db import Database
from ui import theme

log = logging.getLogger(__name__)

EmitFn = Callable[[str, dict[str, Any]], None]


class VideoClipJob:
    """MODE B: Normal YouTube video downloading, multi-modal timeline analysis, and vertical clip generation."""

    def __init__(
        self,
        url: str,
        cfg: Config,
        db: Database,
        emit: EmitFn,
        stop: threading.Event,
        options: dict[str, Any] | None = None,
    ):
        self.url = url
        self.cfg = cfg
        self.db = db
        self.emit = emit
        self.stop = stop
        self.options = options or {}

    def _check_stop(self) -> None:
        if self.stop.is_set():
            raise Cancelled("Stopped by user")

    def run(self) -> None:
        parsed = parse_url(self.url)
        quality = str(self.options.get("quality") or self.cfg.get("download.quality", "720p"))
        clip_dur = int(self.options.get("duration") or self.cfg.get("clip.duration", 30))
        max_clips = int(self.options.get("max_clips") or self.cfg.get("clip.max_clips", 5))

        existing = self.db.find_resumable_job("video", self.url)
        if existing and existing.get("status") in ("stopped", "error"):
            job_id = int(existing["id"])
            self.db.update_job(job_id, status="running", error="")
            log.info("Resuming existing video job #%s for %s", job_id, self.url)
        else:
            job_id = self.db.create_job("video", self.url, parsed.platform, "")

        work_dir = TEMP_DIR / f"video_{parsed.ident or job_id}"
        work_dir.mkdir(parents=True, exist_ok=True)

        try:
            self.emit("status", {"text": "Fetching metadata…", "color": theme.YELLOW})
            self.emit("progress", {"stage": "Downloading video & subtitles…", "percent": 2})

            def _dl_prog(frac: float, msg: str) -> None:
                self.emit("progress", {"stage": msg, "percent": int(2 + frac * 28)})

            video_path, meta, sub_path = download_youtube_video(
                self.url,
                quality=quality,
                work_dir=work_dir,
                stop=self.stop,
                progress=_dl_prog,
                preferred_lang=str(self.cfg.get("whisper.language") or "en"),
            )
            self.db.update_job(
                job_id,
                title=meta.title,
                state={"stage": "downloaded", "video_path": str(video_path)},
            )
            self.emit("info", {"title": meta.title, "duration": meta.duration})
            log.info("Video ready: %s (%.1fs)", meta.title, meta.duration)
            self._check_stop()

            # Step 2: Transcript (available subtitles first, then faster-whisper)
            self.emit("status", {"text": "Transcribing…", "color": theme.CYAN})
            self.emit("progress", {"stage": "Extracting transcript…", "percent": 32})
            tr_cache = work_dir / f"{meta.video_id}_transcript.json"
            transcript = obtain_transcript(
                video_path=video_path,
                subtitle_path=sub_path,
                whisper_cfg=dict(self.cfg.get("whisper") or {}),
                cache_path=tr_cache,
                stop=self.stop,
                progress=lambda f: self.emit("progress", {"percent": int(32 + f * 20)}),
                status=lambda msg: self.emit("progress", {"stage": msg}),
            )
            log.info("Transcript ready (%d segments, source=%s)", len(transcript.segments), transcript.source)
            self._check_stop()

            # Step 3: Audio & Scene analysis
            self.emit("status", {"text": "Analyzing timeline…", "color": theme.CYAN})
            self.emit("progress", {"stage": "Analyzing audio loudness & excitement…", "percent": 54})
            audio_analyzer = AudioAnalyzer()
            audio_timeline = audio_analyzer.analyze_timeline(video_path, step_seconds=1.0, stop=self.stop)
            self._check_stop()

            self.emit("progress", {"stage": "Detecting scene & action changes…", "percent": 64})
            scene_cuts = detect_scene_changes(video_path, stop=self.stop)
            self._check_stop()

            # Step 4: Score & rank candidates
            self.emit("progress", {"stage": "Ranking clip candidates…", "percent": 70})
            detector = VideoExcitementDetector(self.cfg)
            candidates, series = detector.detect_candidates(
                video_duration=meta.duration,
                audio_timeline=audio_timeline,
                transcript=transcript,
                scene_cuts=scene_cuts,
                clip_duration=clip_dur,
                max_clips=max_clips,
            )
            self.emit("timeline", series)

            if not candidates:
                self.db.update_job(job_id, status="done")
                self.emit("finished", {"ok": True, "message": "No moments detected in this video."})
                return

            for cand in reversed(candidates):
                self.db.add_moment(job_id, cand.start, cand.end, cand.score, cand.reasons)
                self.emit(
                    "moment",
                    {
                        "t": cand.peak_time,
                        "start": cand.start,
                        "end": cand.end,
                        "score": cand.score,
                        "reasons": cand.reasons,
                    },
                )

            # Step 5: Render vertical clips (skip any already rendered on a previous run of this job)
            already_done = {
                (round(float(c["start"] or 0), 1), round(float(c["end"] or 0), 1))
                for c in self.db.clips_for_job(job_id)
                if Path(c["path"]).exists()
            }
            generator = ClipGenerator(self.cfg)
            whisper_cfg = dict(self.cfg.get("whisper") or {})
            transcriber: WhisperTranscriber | None = None

            self.emit("status", {"text": "Rendering clips…", "color": theme.ACCENT})
            total_c = len(candidates)
            for idx, cand in enumerate(candidates, start=1):
                self._check_stop()
                key = (round(cand.start, 1), round(cand.end, 1))
                if key in already_done:
                    log.info("Skipping already rendered clip %d/%d (%.1fs-%.1fs)", idx, total_c, *key)
                    continue

                base_pct = 72 + int((idx - 1) / total_c * 26)
                self.emit(
                    "progress",
                    {
                        "stage": f"Rendering vertical clip {idx}/{total_c} ({cand.start:.0f}s–{cand.end:.0f}s)…",
                        "percent": base_pct,
                    },
                )

                # If video had no subtitles and we want accurate word timestamps for just this clip
                clip_tr = transcript
                if not clip_tr.words_between(cand.start, cand.end):
                    try:
                        if transcriber is None:
                            transcriber = WhisperTranscriber(
                                model=whisper_cfg.get("model", "small"),
                                device=whisper_cfg.get("device", "auto"),
                                compute_type=whisper_cfg.get("compute_type", "auto"),
                                language=whisper_cfg.get("language", ""),
                            )
                        from core.media import load_audio

                        clip_audio = load_audio(video_path, sr=16000, start=cand.start, end=cand.end, stop=self.stop)
                        if len(clip_audio) > 800:
                            sub_tr = transcriber.transcribe(clip_audio, stop=self.stop)
                            # Offset local words to video timeline so render_clip can shift consistently
                            for s in sub_tr.segments:
                                s.start += cand.start
                                s.end += cand.start
                                for w in s.words:
                                    w.start += cand.start
                                    w.end += cand.start
                            clip_tr = sub_tr
                    except Cancelled:
                        raise
                    except Exception as e:  # noqa: BLE001
                        log.warning("Clip transcription warning: %s", e)

                local_slice = clip_tr.shifted(offset=cand.start, clip_start=cand.start, clip_end=cand.end)
                title = generate_clip_title(
                    transcript=local_slice,
                    chat_messages=[],
                    source_title=meta.title,
                    reasons=cand.reasons,
                    clip_index=idx,
                )

                res = generator.render_clip(
                    source_video=video_path,
                    start=cand.start,
                    end=cand.end,
                    title=title,
                    transcript=clip_tr,
                    score=cand.score,
                    clip_index=idx,
                    pre_trimmed=False,
                    stop=self.stop,
                    progress=lambda f, b=base_pct, tc=total_c: self.emit(
                        "progress", {"percent": min(99, b + int(f * (26 / tc)))}
                    ),
                )
                clip_id = self.db.add_clip(
                    job_id=job_id,
                    path=str(res.path),
                    thumbnail=str(res.thumbnail),
                    title=res.title,
                    score=res.score,
                    start=res.start,
                    end=res.end,
                    duration=res.duration,
                    source_url=self.url,
                    transcript=res.transcript_text,
                )
                self.emit(
                    "clip",
                    {
                        "id": clip_id,
                        "path": str(res.path),
                        "thumbnail": str(res.thumbnail),
                        "title": res.title,
                        "score": res.score,
                        "start": res.start,
                        "end": res.end,
                        "duration": res.duration,
                        "transcript": res.transcript_text,
                    },
                )
                log.info("Saved clip %d/%d: %s -> %s", idx, total_c, res.title, res.path.name)

            self.db.update_job(job_id, status="done", state={"stage": "done"})
            self.emit("finished", {"ok": True, "message": f"Completed {total_c} vertical clip(s)."})

        except Cancelled:
            self.db.update_job(job_id, status="stopped")
            self.emit("status", {"text": "Stopped (resumable)", "color": theme.YELLOW})
            self.emit("finished", {"ok": True, "message": "Job stopped safely. Start again with the same URL to resume."})
        except Exception as e:  # noqa: BLE001
            log.exception("VideoClipJob failed")
            self.db.update_job(job_id, status="error", error=str(e))
            self.emit("error", {"message": str(e)})
            self.emit("finished", {"ok": False, "message": f"Error: {e}"})


class LiveClipJob:
    """MODE A: Continuous livestream recording, live chat reading, 10s excitement loop, and instant 30s clipping."""

    def __init__(
        self,
        url: str,
        cfg: Config,
        db: Database,
        emit: EmitFn,
        stop: threading.Event,
        options: dict[str, Any] | None = None,
    ):
        self.url = url
        self.cfg = cfg
        self.db = db
        self.emit = emit
        self.stop = stop
        self.options = options or {}

    def run(self) -> None:
        parsed = parse_url(self.url)
        seg_sec = int(self.cfg.get("live.segment_seconds", 5))
        buf_min = int(self.cfg.get("live.buffer_minutes", 10))
        max_reconnects = int(self.cfg.get("live.reconnect_attempts", 20))
        window_sec = float(self.cfg.get("detection.window_seconds", 10))
        pre_sec = float(self.cfg.get("clip.pre_event", 20))
        post_sec = float(self.cfg.get("clip.post_event", 10))
        max_clips = int(self.options.get("max_clips") or self.cfg.get("clip.max_clips", 5))

        job_id = self.db.create_job("live", self.url, parsed.platform, "")
        session_dir = TEMP_DIR / f"live_{parsed.platform}_{parsed.ident}_{int(time.time())}"
        buffer = RollingBuffer(session_dir / "buffer", segment_seconds=seg_sec, max_minutes=buf_min)

        recorder = StreamRecorder(
            url=self.url,
            rolling_buffer=buffer,
            segment_seconds=seg_sec,
            max_reconnects=max_reconnects,
            on_status=lambda s: self.emit("status", {"text": s, "color": theme.GREEN}),
        )
        chat_reader: TwitchChatReader | YouTubeChatReader | None = None
        clip_threads: list[threading.Thread] = []

        try:
            self.emit("status", {"text": "Connecting to live stream…", "color": theme.YELLOW})
            self.emit("progress", {"stage": "Resolving live stream & starting FFmpeg rolling buffer…", "percent": 10})
            info = recorder.start()
            self.db.update_job(job_id, title=info.title, platform=info.platform)
            self.emit("info", {"title": f"{info.title} ({info.channel})"})
            log.info("Live stream connected: [%s] %s", info.platform, info.title)

            # Start platform live chat reader
            if info.platform == "twitch":
                chat_reader = TwitchChatReader(info.channel or parsed.ident)
            else:
                chat_reader = YouTubeChatReader(self.url)
            chat_reader.start()

            self.emit("status", {"text": "Recording & Monitoring", "color": theme.GREEN})
            self.emit(
                "progress",
                {
                    "stage": f"Rolling buffer active ({buf_min} min window). Analyzing every {window_sec:.0f}s…",
                    "percent": 25,
                },
            )

            audio_analyzer = AudioAnalyzer()
            detector = LiveExcitementDetector(self.cfg)
            generator = ClipGenerator(self.cfg)
            whisper_cfg = dict(self.cfg.get("whisper") or {})
            transcriber = WhisperTranscriber(
                model=whisper_cfg.get("model", "small"),
                device=whisper_cfg.get("device", "auto"),
                compute_type=whisper_cfg.get("compute_type", "auto"),
                language=whisper_cfg.get("language", ""),
            )

            last_analyzed_t = 0.0
            last_wall_check = time.time()
            clips_created = 0

            while not self.stop.is_set():
                buffer.scan()
                latest_t = buffer.latest_time

                if latest_t - last_analyzed_t >= window_sec:
                    win_end = latest_t
                    win_start = max(0.0, win_end - window_sec)
                    now_wall = time.time()
                    chat_msgs = chat_reader.messages_since(now_wall - window_sec, now_wall) if chat_reader else []
                    samples = buffer.load_window_audio(win_start, win_end, sr=16000, stop=self.stop)
                    audio_stats = audio_analyzer.analyze_samples(samples, start_time=win_start)

                    ev = detector.evaluate_window(
                        stream_time=win_end,
                        audio_stats=audio_stats,
                        chat_messages=chat_msgs,
                    )
                    last_analyzed_t = win_end
                    last_wall_check = now_wall

                    self.emit(
                        "metrics",
                        {
                            "t": win_end,
                            "chat": ev["chat_count"],
                            "audio": ev["audio_score"],
                            "audio_threshold": ev["audio_threshold"],
                        },
                    )

                    if ev["triggered"]:
                        event_t = win_end
                        c_start = max(buffer.earliest_time, event_t - pre_sec)
                        c_end = event_t + post_sec
                        clips_created += 1
                        clip_num = clips_created
                        self.db.add_moment(job_id, c_start, c_end, ev["score"], ev["reasons"])
                        self.emit(
                            "moment",
                            {
                                "t": event_t,
                                "start": c_start,
                                "end": c_end,
                                "score": ev["score"],
                                "reasons": ev["reasons"],
                            },
                        )
                        log.info(
                            "Excitement spike at %.1fs (score %.2f: %s) -> scheduling %ds clip",
                            event_t,
                            ev["score"],
                            ", ".join(ev["reasons"]),
                            int(pre_sec + post_sec),
                        )

                        snapshot_chat = list(chat_msgs)
                        snapshot_reasons = list(ev["reasons"])
                        th = threading.Thread(
                            target=self._build_live_clip,
                            args=(
                                job_id,
                                buffer,
                                generator,
                                transcriber,
                                info.title,
                                c_start,
                                c_end,
                                ev["score"],
                                snapshot_chat,
                                snapshot_reasons,
                                clip_num,
                                session_dir,
                            ),
                            name=f"live-clip-{clip_num}",
                            daemon=True,
                        )
                        clip_threads.append(th)
                        th.start()

                        if clips_created >= max_clips:
                            log.info("Reached configured maximum clips (%d); finishing after pending clip.", max_clips)
                            th.join()
                            break

                if not recorder.running and (time.time() - last_wall_check) > 25.0:
                    break
                time.sleep(1.0)

            for th in clip_threads:
                th.join(timeout=60.0)

            status_val = "stopped" if self.stop.is_set() else "done"
            self.db.update_job(job_id, status=status_val)
            self.emit(
                "finished",
                {
                    "ok": True,
                    "message": f"Live session ended ({clips_created} clip(s) saved).",
                },
            )
        except Exception as e:  # noqa: BLE001
            log.exception("LiveClipJob error")
            self.db.update_job(job_id, status="error", error=str(e))
            self.emit("error", {"message": str(e)})
            self.emit("finished", {"ok": False, "message": f"Live error: {e}"})
        finally:
            recorder.stop()
            if chat_reader is not None:
                chat_reader.stop()

    def _build_live_clip(
        self,
        job_id: int,
        buffer: RollingBuffer,
        generator: ClipGenerator,
        transcriber: WhisperTranscriber,
        stream_title: str,
        clip_start: float,
        clip_end: float,
        score: float,
        chat_msgs: list[dict[str, Any]],
        reasons: list[str],
        clip_num: int,
        session_dir: Path,
    ) -> None:
        """Wait for post-event segments to flush into buffer, extract 30s segment, transcribe, title, and render."""
        try:
            self.emit(
                "progress",
                {
                    "stage": f"Clip #{clip_num}: waiting for {clip_end - buffer.latest_time:.0f}s post-event footage…",
                    "percent": 40,
                },
            )
            deadline = time.time() + max(30.0, (clip_end - buffer.latest_time) + 20.0)
            while buffer.latest_time < clip_end and time.time() < deadline and not self.stop.is_set():
                buffer.scan()
                time.sleep(0.8)

            raw_clip = session_dir / f"raw_clip_{clip_num:03d}.mp4"
            self.emit("progress", {"stage": f"Clip #{clip_num}: extracting 30s from rolling buffer…", "percent": 55})
            buffer.extract_clip(clip_start, min(clip_end, buffer.latest_time), raw_clip, stop=self.stop)

            self.emit("progress", {"stage": f"Clip #{clip_num}: generating captions with faster-whisper…", "percent": 70})
            try:
                transcript = transcriber.transcribe(raw_clip, stop=self.stop)
            except Exception as e:  # noqa: BLE001
                log.warning("Live clip transcription fallback (%s)", e)
                transcript = Transcript([], "", "")

            title = generate_clip_title(
                transcript=transcript,
                chat_messages=chat_msgs,
                source_title=stream_title,
                reasons=reasons,
                clip_index=clip_num,
            )

            self.emit("progress", {"stage": f"Clip #{clip_num}: rendering 9:16 vertical Short…", "percent": 85})
            res = generator.render_clip(
                source_video=raw_clip,
                start=clip_start,
                end=clip_end,
                title=title,
                transcript=transcript,
                score=score,
                clip_index=clip_num,
                pre_trimmed=True,
                stop=self.stop,
            )
            try:
                raw_clip.unlink()
            except OSError:
                pass

            clip_id = self.db.add_clip(
                job_id=job_id,
                path=str(res.path),
                thumbnail=str(res.thumbnail),
                title=res.title,
                score=res.score,
                start=clip_start,
                end=clip_end,
                duration=res.duration,
                source_url=self.url,
                transcript=res.transcript_text,
            )
            self.emit(
                "clip",
                {
                    "id": clip_id,
                    "path": str(res.path),
                    "thumbnail": str(res.thumbnail),
                    "title": res.title,
                    "score": res.score,
                    "start": clip_start,
                    "end": clip_end,
                    "duration": res.duration,
                    "transcript": res.transcript_text,
                },
            )
            self.emit("progress", {"stage": f"Saved Clip #{clip_num}: {res.title}", "percent": 100})
            log.info("Live clip #%d saved immediately: %s", clip_num, res.path)
        except Cancelled:
            pass
        except Exception as e:  # noqa: BLE001
            log.exception("Failed to build live clip #%d: %s", clip_num, e)
            self.emit("error", {"message": f"Live clip #{clip_num} failed: {e}"})
