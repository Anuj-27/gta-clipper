"""AI clip detection & multi-modal excitement scoring for live streams and VODs."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import re
from typing import Any

import numpy as np

from core.audio_analyzer import AudioWindowStats
from core.config import Config, get_config
from core.transcription import Transcript

# Curated lexical signals for speech importance, surprise, emotion, and humor
_EXCITING_WORDS = {
    "insane", "crazy", "unbelievable", "impossible", "clutch", "win", "won", "lost",
    "dead", "killed", "eliminated", "headshot", "record", "world", "first", "finally",
    "legendary", "epic", "massive", "huge", "bro", "dude", "look", "watch", "wait",
    "stop", "run", "running", "busted", "wasted", "police", "cops", "heist", "explosion",
}
_SURPRISE_PATTERNS = re.compile(
    r"\b(what the|no way|oh my god|omg|wtf|are you serious|are you kidding|"
    r"you're joking|how did|did that just|wait what|hold on|bro what|holy)\b",
    re.I,
)
_FUNNY_EMOTION_PATTERNS = re.compile(
    r"(\[laughter\]|\[applause\]|\b(haha|lmao|lmfao|rofl|hilarious|funny|laugh|laughing|"
    r"crying|screaming|scared|terrified|love|hate|angry|mad|sad|heart|goosebumps)\b)",
    re.I,
)
_CHAT_HYPE_PATTERNS = re.compile(
    r"\b(clip it|clip that|omg|wtf|lmao|lol|pog|poggers|pagman|kekw|lul|insane|clutch|"
    r"no way|gg|ez|rip|f|w|l|goat|holy)\b|[!?]{2,}",
    re.I,
)


@dataclass
class ClipCandidate:
    start: float
    end: float
    peak_time: float
    score: float
    components: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


def score_text_window(text: str) -> tuple[float, float, list[str]]:
    """Return (speech_importance 0..1, emotion_funny 0..1, reasons) for spoken dialogue."""
    cleaned = (text or "").strip()
    if not cleaned:
        return 0.0, 0.0, []
    words = re.findall(r"[A-Za-z']+", cleaned.lower())
    n_words = len(words)
    if n_words == 0:
        return 0.0, 0.0, []

    reasons: list[str] = []
    exciting_hits = sum(1 for w in words if w in _EXCITING_WORDS)
    surprise_hits = len(_SURPRISE_PATTERNS.findall(cleaned))
    emo_hits = len(_FUNNY_EMOTION_PATTERNS.findall(cleaned))
    exclam = cleaned.count("!") + cleaned.count("?")

    # Speech density & punchy delivery
    density = min(n_words / 28.0, 1.0)
    kw_score = min((exciting_hits * 0.22) + (surprise_hits * 0.40) + (exclam * 0.12), 1.0)
    speech_score = float(np.clip(0.45 * density + 0.55 * kw_score, 0.0, 1.0))

    emo_score = float(np.clip(emo_hits * 0.42 + surprise_hits * 0.30 + min(exclam * 0.15, 0.4), 0.0, 1.0))

    if surprise_hits:
        reasons.append("surprising dialogue")
    elif exciting_hits >= 2:
        reasons.append("high-energy dialogue")
    if emo_hits:
        reasons.append("funny/emotional reaction")
    return speech_score, emo_score, reasons


def score_chat_messages(messages: list[dict[str, Any]], baseline_rate: float) -> tuple[float, float, list[str]]:
    """Return (chat_score 0..1, spike_ratio, reasons) for a 10-second window of chat messages."""
    count = len(messages)
    if count == 0:
        return 0.0, 0.0, []
    base = max(baseline_rate, 2.0)
    ratio = count / base
    hype_hits = sum(1 for m in messages if _CHAT_HYPE_PATTERNS.search(str(m.get("message", ""))))
    hype_ratio = hype_hits / max(count, 1)

    rate_norm = float(np.clip((ratio - 0.8) / 2.7, 0.0, 1.0))
    abs_norm = float(np.clip(count / 25.0, 0.0, 1.0))
    chat_score = float(np.clip(0.55 * rate_norm + 0.25 * abs_norm + 0.20 * min(hype_ratio * 2.0, 1.0), 0.0, 1.0))
    reasons: list[str] = []
    if ratio >= 1.8 and count >= 3:
        reasons.append(f"chat spike x{ratio:.1f} ({count} msgs)")
    elif hype_hits >= 2:
        reasons.append(f"chat hype ({hype_hits} reactions)")
    return chat_score, ratio, reasons


class LiveExcitementDetector:
    """Evaluates 10-second live windows and triggers when chat activity AND audio excitement spike."""

    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or get_config()
        self._chat_counts: deque[int] = deque(maxlen=36)  # 6 minutes of 10s windows
        self._audio_scores: deque[float] = deque(maxlen=36)
        self._last_trigger_ts: float = -1e9

    def evaluate_window(
        self,
        stream_time: float,
        audio_stats: AudioWindowStats,
        chat_messages: list[dict[str, Any]],
        scene_change_score: float = 0.0,
        transcript_text: str = "",
    ) -> dict[str, Any]:
        weights = self.cfg.normalized_weights("live")
        sensitivity = float(self.cfg.get("detection.sensitivity", 0.6))
        cooldown = float(self.cfg.get("live.cooldown_seconds", 60))

        baseline_chat = float(np.median(self._chat_counts)) if len(self._chat_counts) >= 3 else 3.0
        chat_score, chat_ratio, chat_reasons = score_chat_messages(chat_messages, baseline_chat)
        self._chat_counts.append(len(chat_messages))

        baseline_audio = float(np.median(self._audio_scores)) if len(self._audio_scores) >= 3 else 0.25
        audio_score = audio_stats.excitement
        self._audio_scores.append(audio_score)

        speech_score, emo_score, text_reasons = score_text_window(transcript_text)
        # When live window hasn't been transcribed yet, infer vocal presence from audio dynamics
        if not transcript_text:
            speech_score = float(np.clip(audio_stats.shout_ratio * 1.6 + audio_score * 0.5, 0.0, 1.0))
            emo_score = float(np.clip(audio_stats.shout_ratio * 2.0, 0.0, 1.0))

        total_score = float(
            weights["chat"] * chat_score
            + weights["audio"] * audio_score
            + weights["speech"] * speech_score
            + weights["scene"] * float(np.clip(scene_change_score, 0.0, 1.0))
            + weights["emotion"] * emo_score
        )

        # Threshold adapts with user sensitivity (0..1 -> threshold 0.72..0.34)
        audio_thr = float(np.clip(max(baseline_audio + 0.14, 0.65 - 0.32 * sensitivity), 0.28, 0.82))
        chat_thr = float(np.clip(0.58 - 0.32 * sensitivity, 0.20, 0.75))
        combined_thr = float(np.clip(0.66 - 0.32 * sensitivity, 0.30, 0.78))

        reasons: list[str] = list(chat_reasons)
        if audio_score >= audio_thr or audio_stats.sudden_jump_db >= 7.5:
            reasons.append(f"audio spike ({audio_stats.rms_db:.0f} dB, +{audio_stats.sudden_jump_db:.1f} dB)")
        if audio_stats.shout_ratio >= 0.22:
            reasons.append("shouting / excitement")
        reasons.extend(text_reasons)

        in_cooldown = (stream_time - self._last_trigger_ts) < cooldown
        both_spike = (chat_score >= chat_thr and audio_score >= audio_thr) or (
            total_score >= combined_thr and audio_score >= audio_thr and (chat_score >= chat_thr * 0.8 or len(chat_messages) == 0 and audio_score >= min(audio_thr + 0.15, 0.85))
        )
        triggered = bool(both_spike and not in_cooldown)
        if triggered:
            self._last_trigger_ts = stream_time
            if not reasons:
                reasons.append("combined chat + audio excitement spike")

        return {
            "t": stream_time,
            "score": total_score,
            "triggered": triggered,
            "chat_count": len(chat_messages),
            "chat_score": chat_score,
            "chat_ratio": chat_ratio,
            "audio_score": audio_score,
            "audio_threshold": audio_thr,
            "speech_score": speech_score,
            "scene_score": scene_change_score,
            "emotion_score": emo_score,
            "reasons": reasons,
        }


class VideoExcitementDetector:
    """Analyzes a full YouTube video timeline and ranks non-overlapping clip candidates."""

    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or get_config()

    def detect_candidates(
        self,
        video_duration: float,
        audio_timeline: list[AudioWindowStats],
        transcript: Transcript,
        scene_cuts: list[float],
        clip_duration: float | None = None,
        max_clips: int | None = None,
    ) -> tuple[list[ClipCandidate], dict[str, list[float]]]:
        """Return (ranked_non_overlapping_candidates, timeline_series_for_ui_graphs)."""
        clip_dur = float(clip_duration or self.cfg.get("clip.duration", 30))
        max_c = int(max_clips or self.cfg.get("clip.max_clips", 5))
        min_gap = float(self.cfg.get("clip.min_gap", 45))
        sensitivity = float(self.cfg.get("detection.sensitivity", 0.6))
        weights = self.cfg.normalized_weights("video")

        if video_duration <= 0 and audio_timeline:
            video_duration = audio_timeline[-1].end
        if video_duration <= 0:
            return [], {"t": [], "audio": [], "engagement": [], "total": []}

        # Build 1-second grid across the video
        n_sec = max(int(np.ceil(video_duration)), 1)
        ts = np.arange(n_sec, dtype=np.float64)
        audio_arr = np.zeros(n_sec, dtype=np.float64)
        jump_arr = np.zeros(n_sec, dtype=np.float64)
        shout_arr = np.zeros(n_sec, dtype=np.float64)

        for st in audio_timeline:
            idx = min(max(int(st.start), 0), n_sec - 1)
            audio_arr[idx] = st.excitement
            jump_arr[idx] = st.sudden_jump_db
            shout_arr[idx] = st.shout_ratio

        speech_arr = np.zeros(n_sec, dtype=np.float64)
        emo_arr = np.zeros(n_sec, dtype=np.float64)
        scene_arr = np.zeros(n_sec, dtype=np.float64)
        reasons_map: dict[int, list[str]] = {}

        # Score transcript in 6-second sliding windows
        win_t = 6.0
        for i in range(n_sec):
            txt = transcript.text_between(max(0.0, i - win_t / 2), min(video_duration, i + win_t / 2))
            sp, em, r = score_text_window(txt)
            speech_arr[i] = sp
            emo_arr[i] = max(em, min(shout_arr[i] * 1.8, 1.0))
            if r:
                reasons_map[i] = r

        # Scene change density in 6-second neighborhood
        cuts = np.asarray(scene_cuts, dtype=np.float64)
        if len(cuts):
            for i in range(n_sec):
                c = int(np.sum((cuts >= i - 3.0) & (cuts <= i + 3.0)))
                scene_arr[i] = float(np.clip(c / 4.0, 0.0, 1.0))

        total_arr = (
            weights["audio"] * audio_arr
            + weights["speech"] * speech_arr
            + weights["scene"] * scene_arr
            + weights["emotion"] * emo_arr
        )

        # Smooth with a 5-second kernel to find sustained exciting moments while preserving peak time
        kernel = np.ones(min(5, n_sec), dtype=np.float64)
        kernel /= kernel.sum()
        smooth = np.convolve(total_arr, kernel, mode="same")

        # Short video fallback: if video <= clip_dur * 1.2, return single clip covering the video
        if video_duration <= clip_dur * 1.15:
            peak_idx = int(np.argmax(total_arr))
            cand = ClipCandidate(
                start=0.0,
                end=video_duration,
                peak_time=float(peak_idx),
                score=float(np.max(smooth)),
                components={
                    "audio": float(np.mean(audio_arr)),
                    "speech": float(np.mean(speech_arr)),
                    "scene": float(np.mean(scene_arr)),
                    "emotion": float(np.mean(emo_arr)),
                },
                reasons=reasons_map.get(peak_idx, ["best moment in video"]),
            )
            return [cand], {
                "t": ts.tolist(),
                "audio": audio_arr.tolist(),
                "engagement": (0.6 * speech_arr + 0.4 * emo_arr).tolist(),
                "total": smooth.tolist(),
            }

        # Rank candidate peaks and greedily select non-overlapping windows
        order = np.argsort(smooth)[::-1]
        min_score = float(np.percentile(smooth, max(10.0, 75.0 - 55.0 * sensitivity)))
        selected: list[ClipCandidate] = []
        effective_gap = max(min(min_gap, video_duration / max(max_c + 1, 2)), clip_dur * 0.85)

        for idx in order:
            peak_t = float(idx)
            sc = float(smooth[idx])
            if sc < min_score and len(selected) >= 1:
                break

            # Align clip start so peak lands ~60% into the clip (builds context before climax)
            start = max(0.0, min(peak_t - clip_dur * 0.60, video_duration - clip_dur))
            end = min(video_duration, start + clip_dur)

            # Snap start slightly to nearby sentence boundary if one exists within ±2.5s
            for seg in transcript.segments:
                if abs(seg.start - start) <= 2.5 and seg.start + clip_dur <= video_duration:
                    start = max(0.0, seg.start)
                    end = min(video_duration, start + clip_dur)
                    break

            # Reject duplicates / overlaps
            overlaps = False
            for existing in selected:
                if abs(existing.peak_time - peak_t) < effective_gap:
                    overlaps = True
                    break
                overlap_dur = max(0.0, min(existing.end, end) - max(existing.start, start))
                if overlap_dur > 0.15 * clip_dur:
                    overlaps = True
                    break
            if overlaps:
                continue

            i0, i1 = max(int(start), 0), min(int(np.ceil(end)), n_sec)
            comp = {
                "audio": float(np.max(audio_arr[i0:i1])) if i1 > i0 else float(audio_arr[idx]),
                "speech": float(np.max(speech_arr[i0:i1])) if i1 > i0 else float(speech_arr[idx]),
                "scene": float(np.max(scene_arr[i0:i1])) if i1 > i0 else float(scene_arr[idx]),
                "emotion": float(np.max(emo_arr[i0:i1])) if i1 > i0 else float(emo_arr[idx]),
            }
            reasons: list[str] = []
            for r in reasons_map.get(int(idx), []):
                if r not in reasons:
                    reasons.append(r)
            if comp["audio"] >= 0.55 or float(np.max(jump_arr[i0:i1] if i1 > i0 else [0.0])) >= 7.0:
                reasons.append("loudness spike")
            if comp["scene"] >= 0.5:
                reasons.append("fast action / scene cuts")
            if not reasons:
                reasons.append("high engagement moment")

            selected.append(
                ClipCandidate(
                    start=round(start, 2),
                    end=round(end, 2),
                    peak_time=round(peak_t, 2),
                    score=round(sc, 3),
                    components=comp,
                    reasons=reasons,
                )
            )
            if len(selected) >= max_c:
                break

        selected.sort(key=lambda c: c.score, reverse=True)
        series = {
            "t": ts.tolist(),
            "audio": audio_arr.tolist(),
            "engagement": np.clip(0.6 * speech_arr + 0.4 * emo_arr, 0.0, 1.0).tolist(),
            "total": smooth.tolist(),
        }
        return selected, series
