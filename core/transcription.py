"""Transcripts: data model, YouTube subtitle parsing, and local faster-whisper transcription."""
from __future__ import annotations

import html
import json
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from core.media import Cancelled, load_audio
from core.paths import MODELS_DIR

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

log = logging.getLogger(__name__)


@dataclass
class Word:
    start: float
    end: float
    text: str
    prob: float = 1.0


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass
class Transcript:
    segments: list[Segment]
    language: str = ""
    source: str = ""  # 'subtitles' | 'auto-captions' | 'whisper'

    # ---- helpers --------------------------------------------------------
    @property
    def words(self) -> list[Word]:
        return [w for s in self.segments for w in s.words]

    def text_between(self, start: float, end: float) -> str:
        parts = [s.text for s in self.segments if s.end > start and s.start < end]
        return " ".join(p.strip() for p in parts if p.strip())

    def words_between(self, start: float, end: float) -> list[Word]:
        return [w for w in self.words if w.end > start and w.start < end]

    def segments_between(self, start: float, end: float) -> list[Segment]:
        return [s for s in self.segments if s.end > start and s.start < end]

    def shifted(self, offset: float, clip_start: float = 0.0, clip_end: float | None = None) -> "Transcript":
        """Return a transcript restricted to [clip_start, clip_end] and shifted by -offset."""
        out: list[Segment] = []
        for s in self.segments:
            if s.end <= clip_start or (clip_end is not None and s.start >= clip_end):
                continue
            ws = [
                Word(max(w.start, clip_start) - offset, min(w.end, clip_end or w.end) - offset, w.text, w.prob)
                for w in s.words
                if w.end > clip_start and (clip_end is None or w.start < clip_end)
            ]
            if not ws:
                continue
            out.append(Segment(ws[0].start, ws[-1].end, " ".join(w.text for w in ws), ws))
        return Transcript(out, self.language, self.source)

    def to_json(self) -> str:
        return json.dumps({"language": self.language, "source": self.source,
                           "segments": [asdict(s) for s in self.segments]})

    @classmethod
    def from_json(cls, data: str) -> "Transcript":
        d = json.loads(data)
        segs = [Segment(s["start"], s["end"], s["text"], [Word(**w) for w in s["words"]]) for s in d["segments"]]
        return cls(segs, d.get("language", ""), d.get("source", ""))

    def save(self, path: Path) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(self.to_json(), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "Transcript":
        return cls.from_json(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Subtitle parsing
# ---------------------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>")
_NOISE_RE = re.compile(r"^\[(?:music|applause|laughter|__)\]$", re.I)


def _split_even(start: float, end: float, text: str) -> list[Word]:
    toks = text.split()
    if not toks:
        return []
    step = max(end - start, 0.05) / len(toks)
    return [Word(start + i * step, start + (i + 1) * step, t) for i, t in enumerate(toks)]


def parse_json3(path: Path) -> list[Segment]:
    """Parse YouTube json3 subtitles. Auto-captions include per-word offsets."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    events = [e for e in data.get("events", []) if e.get("segs")]
    segments: list[Segment] = []
    for i, ev in enumerate(events):
        t0 = ev.get("tStartMs", 0) / 1000.0
        dur = ev.get("dDurationMs")
        nxt = events[i + 1].get("tStartMs", 0) / 1000.0 if i + 1 < len(events) else None
        t1 = t0 + dur / 1000.0 if dur else (nxt or t0 + 2.0)
        segs = [s for s in ev["segs"] if s.get("utf8", "").strip()]
        if not segs:
            continue
        text = "".join(s["utf8"] for s in ev["segs"]).replace("\n", " ").strip()
        if not text or _NOISE_RE.match(text):
            # keep laughter/applause markers as context words: useful for emotion detection
            if text.lower() in ("[laughter]", "[applause]"):
                segments.append(Segment(t0, t1, text, [Word(t0, t1, text)]))
            continue
        if any("tOffsetMs" in s for s in segs) or len(segs) > 1:
            words: list[Word] = []
            for j, s in enumerate(segs):
                ws = t0 + s.get("tOffsetMs", 0) / 1000.0
                we = t0 + segs[j + 1].get("tOffsetMs", 0) / 1000.0 if j + 1 < len(segs) else min(t1, ws + 1.0)
                for k, tok in enumerate(s["utf8"].split()):
                    words.append(Word(ws, max(we, ws + 0.05), tok))
            # tokens sharing one seg timing: spread them evenly
            fixed: list[Word] = []
            k = 0
            while k < len(words):
                grp = [w for w in words[k:] if w.start == words[k].start]
                fixed.extend(_split_even(grp[0].start, grp[0].end, " ".join(w.text for w in grp)))
                k += len(grp)
            words = fixed
        else:
            words = _split_even(t0, t1, text)
        if words:
            segments.append(Segment(words[0].start, max(words[-1].end, words[0].start + 0.1), text, words))
    return _merge_short(segments)


def _vtt_time(s: str) -> float:
    parts = s.replace(",", ".").split(":")
    parts = [float(p) for p in parts]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def parse_vtt(path: Path) -> list[Segment]:
    """Parse WebVTT / SRT, de-duplicating YouTube's rolling auto-caption lines."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"\n\s*\n", text.replace("\r", ""))
    segs: list[Segment] = []
    last_line = ""
    for b in blocks:
        m = re.search(r"([\d:.,]+)\s+-->\s+([\d:.,]+)", b)
        if not m:
            continue
        t0, t1 = _vtt_time(m.group(1)), _vtt_time(m.group(2))
        lines = [html.unescape(_TAG_RE.sub("", ln)).strip() for ln in b[m.end():].split("\n")]
        lines = [ln for ln in lines if ln]
        new = [ln for ln in lines if ln != last_line]
        if lines:
            last_line = lines[-1]
        content = " ".join(new).strip()
        if not content or t1 - t0 < 0.02:
            continue
        segs.append(Segment(t0, t1, content, _split_even(t0, t1, content)))
    return _merge_short(segs)


def _merge_short(segs: list[Segment], max_len: float = 8.0) -> list[Segment]:
    """Merge tiny caption fragments into sentence-ish segments."""
    out: list[Segment] = []
    for s in sorted(segs, key=lambda x: x.start):
        if out:
            p = out[-1]
            ends_sentence = p.text.rstrip().endswith((".", "!", "?"))
            if not ends_sentence and s.start - p.end < 0.6 and s.end - p.start < max_len:
                p.end = s.end
                p.text = f"{p.text} {s.text}".strip()
                p.words.extend(s.words)
                continue
        out.append(Segment(s.start, s.end, s.text, list(s.words)))
    return out


def load_subtitle_file(path: Path) -> list[Segment]:
    return parse_json3(path) if path.suffix == ".json3" else parse_vtt(path)


# ---------------------------------------------------------------------------
# faster-whisper
# ---------------------------------------------------------------------------
class WhisperTranscriber:
    """Lazily loads a faster-whisper model once and reuses it (thread-safe)."""

    _lock = threading.Lock()
    _model = None
    _model_key: tuple | None = None

    def __init__(self, model: str = "small", device: str = "auto", compute_type: str = "auto", language: str = ""):
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.language = language or None

    def _resolve(self) -> tuple[str, str]:
        device = self.device
        if device == "auto":
            try:
                import ctranslate2

                device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
            except Exception:  # noqa: BLE001
                device = "cpu"
        ct = self.compute_type
        if ct == "auto":
            ct = "float16" if device == "cuda" else "int8"
        return device, ct

    def load(self, status: Callable[[str], None] | None = None):
        device, ct = self._resolve()
        key = (self.model_name, device, ct)
        with WhisperTranscriber._lock:
            if WhisperTranscriber._model is not None and WhisperTranscriber._model_key == key:
                return WhisperTranscriber._model
            from faster_whisper import WhisperModel

            if status:
                status(f"Loading Whisper '{self.model_name}' on {device} (first run downloads the model)…")
            log.info("Loading faster-whisper model=%s device=%s compute=%s", *key)
            threads = max(1, (os.cpu_count() or 4) - 1)
            try:
                model = WhisperModel(self.model_name, device=device, compute_type=ct,
                                     download_root=str(MODELS_DIR), cpu_threads=threads)
            except Exception as e:  # noqa: BLE001
                if device == "cuda":
                    log.warning("CUDA load failed (%s); falling back to CPU.", e)
                    model = WhisperModel(self.model_name, device="cpu", compute_type="int8",
                                         download_root=str(MODELS_DIR), cpu_threads=threads)
                    key = (self.model_name, "cpu", "int8")
                else:
                    raise RuntimeError(
                        f"Could not load Whisper model '{self.model_name}'. The first run needs an internet "
                        f"connection to download it. Details: {e}"
                    ) from e
            WhisperTranscriber._model, WhisperTranscriber._model_key = model, key
            return model

    def transcribe(
        self,
        audio,  # path or float32 numpy array @16 kHz
        stop: threading.Event | None = None,
        progress: Callable[[float], None] | None = None,
        total_duration: float | None = None,
        status: Callable[[str], None] | None = None,
    ) -> Transcript:
        model = self.load(status)
        if isinstance(audio, (str, Path)):
            if status:
                status("Decoding audio track for Whisper transcription…")
            audio_samples = load_audio(audio, sr=16000, stop=stop)
            if total_duration is None and len(audio_samples):
                total_duration = len(audio_samples) / 16000.0
        else:
            audio_samples = audio
        if len(audio_samples) == 0:
            return Transcript([], self.language or "", "whisper")
        segments_iter, info = model.transcribe(
            audio_samples,
            language=self.language,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            beam_size=5,
            condition_on_previous_text=False,
        )
        total = total_duration or getattr(info, "duration", None) or 0
        segs: list[Segment] = []
        for s in segments_iter:
            if stop is not None and stop.is_set():
                raise Cancelled()
            words = [Word(float(w.start), float(w.end), w.word.strip(), float(w.probability))
                     for w in (s.words or []) if w.word.strip()]
            if not words:
                words = _split_even(float(s.start), float(s.end), s.text.strip())
            segs.append(Segment(float(s.start), float(s.end), s.text.strip(), words))
            if progress and total:
                progress(min(float(s.end) / total, 1.0))
        return Transcript(segs, getattr(info, "language", "") or "", "whisper")
