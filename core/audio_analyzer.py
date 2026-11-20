"""Audio analysis: RMS loudness, peak amplitude, spectral flux/high-energy shouting, and excitement score."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading

import numpy as np

from core.media import load_audio

EPS = 1e-8


@dataclass
class AudioWindowStats:
    start: float
    end: float
    rms: float
    rms_db: float
    peak: float
    peak_db: float
    shout_ratio: float
    sudden_jump_db: float
    excitement: float


class AudioAnalyzer:
    """Computes frame-level and window-level audio loudness and excitement features."""

    def __init__(self, sample_rate: int = 16000):
        self.sr = sample_rate
        self._history_rms_db: list[float] = []

    @staticmethod
    def _to_db(val: float | np.ndarray) -> float | np.ndarray:
        return 20.0 * np.log10(np.maximum(val, EPS))

    def analyze_samples(
        self,
        samples: np.ndarray,
        start_time: float = 0.0,
        prev_rms_db: float | None = None,
    ) -> AudioWindowStats:
        """Analyze a single window of mono float32 samples (e.g., 10 seconds)."""
        n = len(samples)
        dur = n / float(self.sr) if n > 0 else 0.0
        if n == 0:
            return AudioWindowStats(start_time, start_time, 0.0, -96.0, 0.0, -96.0, 0.0, 0.0, 0.0)

        rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
        peak = float(np.max(np.abs(samples)))
        rms_db = float(self._to_db(rms))
        peak_db = float(self._to_db(peak))

        # Break into 0.25s sub-frames to detect sudden bursts, shouting, and dynamic range
        hop = max(int(self.sr * 0.25), 1)
        pad = (-n) % hop
        padded = np.pad(samples, (0, pad)) if pad else samples
        frames = padded.reshape(-1, hop)
        frame_rms = np.sqrt(np.mean(np.square(frames, dtype=np.float64), axis=1))
        frame_db = self._to_db(frame_rms)

        # High-frequency energy ratio in loud frames (shouting / laughter / screaming)
        diffs = np.abs(np.diff(frames, axis=1))
        zcr_like = np.mean(diffs, axis=1) / (frame_rms + EPS)
        loud_mask = frame_db > max(rms_db + 2.0, -32.0)
        shout_frames = loud_mask & (zcr_like > 0.32)
        shout_ratio = float(np.mean(shout_frames)) if len(frames) else 0.0

        # Sudden loudness jump relative to previous window or quiet half of current window
        if len(frame_db) >= 2:
            intra_jump = float(np.max(frame_db) - np.percentile(frame_db, 30))
        else:
            intra_jump = 0.0
        if prev_rms_db is not None:
            inter_jump = max(0.0, rms_db - prev_rms_db)
        elif self._history_rms_db:
            baseline = float(np.median(self._history_rms_db[-20:]))
            inter_jump = max(0.0, rms_db - baseline)
        else:
            inter_jump = 0.0
        sudden_jump_db = max(intra_jump * 0.65, inter_jump)

        self._history_rms_db.append(rms_db)
        if len(self._history_rms_db) > 120:
            self._history_rms_db = self._history_rms_db[-120:]

        # Normalize components to 0..1 excitement score
        # rms_db typical speech -35..-12 dB
        loud_norm = np.clip((rms_db + 38.0) / 26.0, 0.0, 1.0)
        peak_norm = np.clip((peak_db + 20.0) / 19.0, 0.0, 1.0)
        jump_norm = np.clip(sudden_jump_db / 14.0, 0.0, 1.0)
        shout_norm = np.clip(shout_ratio * 2.4, 0.0, 1.0)

        excitement = float(
            np.clip(
                0.35 * loud_norm + 0.20 * peak_norm + 0.30 * jump_norm + 0.15 * shout_norm,
                0.0,
                1.0,
            )
        )
        return AudioWindowStats(
            start=start_time,
            end=start_time + dur,
            rms=rms,
            rms_db=rms_db,
            peak=peak,
            peak_db=peak_db,
            shout_ratio=shout_ratio,
            sudden_jump_db=sudden_jump_db,
            excitement=excitement,
        )

    def analyze_timeline(
        self,
        media_path: str | Path,
        step_seconds: float = 1.0,
        stop: threading.Event | None = None,
    ) -> list[AudioWindowStats]:
        """Decode audio from a media file and return per-step AudioWindowStats normalized across the file."""
        samples = load_audio(media_path, sr=self.sr, stop=stop)
        if len(samples) == 0:
            return []
        win = max(int(self.sr * step_seconds), 1)
        total_steps = int(np.ceil(len(samples) / win))
        raw_stats: list[AudioWindowStats] = []
        self._history_rms_db.clear()
        prev_db: float | None = None
        for i in range(total_steps):
            chunk = samples[i * win : (i + 1) * win]
            st = self.analyze_samples(chunk, start_time=i * step_seconds, prev_rms_db=prev_db)
            prev_db = st.rms_db
            raw_stats.append(st)

        # Relative re-scaling across full video timeline so quiet videos still surface spikes
        if len(raw_stats) >= 4:
            dbs = np.array([s.rms_db for s in raw_stats], dtype=np.float64)
            jumps = np.array([s.sudden_jump_db for s in raw_stats], dtype=np.float64)
            p20, p95 = float(np.percentile(dbs, 20)), float(np.percentile(dbs, 95))
            j95 = max(float(np.percentile(jumps, 95)), 4.0)
            span = max(p95 - p20, 6.0)
            for s in raw_stats:
                rel_loud = float(np.clip((s.rms_db - p20) / span, 0.0, 1.0))
                rel_jump = float(np.clip(s.sudden_jump_db / j95, 0.0, 1.0))
                s.excitement = float(
                    np.clip(0.50 * s.excitement + 0.30 * rel_loud + 0.20 * rel_jump, 0.0, 1.0)
                )
        return raw_stats
