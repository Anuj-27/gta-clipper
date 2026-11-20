"""Persistent JSON configuration with defaults and validation."""
from __future__ import annotations

import copy
import json
import logging
import threading
from pathlib import Path
from typing import Any

from core.paths import CONFIG_DIR

log = logging.getLogger(__name__)

CONFIG_FILE = CONFIG_DIR / "settings.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "clip": {
        "duration": 30,          # seconds, used by YouTube video mode
        "pre_event": 20,         # seconds before an event (live mode)
        "post_event": 10,        # seconds after an event (live mode)
        "max_clips": 5,          # max clips per video / per live session
        "min_gap": 45,           # minimum seconds between two clip centres
    },
    "output": {
        "width": 1080,
        "height": 1920,
        "video_crf": 20,
        "preset": "veryfast",
        "audio_bitrate": "192k",
        "smart_crop": True,
        "background_music": False,
        "music_path": "",
        "music_volume": 0.12,
    },
    "download": {
        "quality": "720p",       # best / 1080p / 720p / 480p / 360p
    },
    "whisper": {
        "model": "small",        # tiny / base / small / medium / large-v3
        "device": "auto",        # auto / cpu / cuda
        "compute_type": "auto",
        "language": "",          # empty = auto-detect
    },
    "captions": {
        "enabled": True,
        "style": "bold_pop",     # bold_pop / clean / karaoke / minimal
        "font": "Arial Black",
        "font_size": 78,
        "primary_color": "#FFFFFF",
        "highlight_color": "#FFE400",
        "outline_color": "#000000",
        "words_per_line": 3,
        "position": "lower",     # lower / middle
    },
    "detection": {
        "sensitivity": 0.6,      # 0..1, higher = more clips
        "window_seconds": 10,
        "weights": {
            "chat": 0.30,
            "audio": 0.25,
            "speech": 0.20,
            "scene": 0.15,
            "emotion": 0.10,
        },
    },
    "live": {
        "segment_seconds": 5,
        "buffer_minutes": 10,    # rolling buffer length kept on disk
        "reconnect_attempts": 20,
        "cooldown_seconds": 60,  # min seconds between live clip triggers
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif k in out:
            out[k] = v
    return out


class Config:
    """Thread-safe config store. Access via dotted keys: cfg.get('clip.duration')."""

    def __init__(self, path: Path = CONFIG_FILE):
        self.path = path
        self._lock = threading.RLock()
        self.data: dict[str, Any] = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    def load(self) -> None:
        with self._lock:
            if self.path.exists():
                try:
                    user = json.loads(self.path.read_text(encoding="utf-8"))
                    self.data = _deep_merge(DEFAULT_CONFIG, user)
                except Exception as e:  # noqa: BLE001
                    log.error("Config file unreadable (%s); using defaults.", e)
                    self.data = copy.deepcopy(DEFAULT_CONFIG)
            self.validate()
            self.save()

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self.data
            for part in key.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return copy.deepcopy(node)

    def set(self, key: str, value: Any, save: bool = True) -> None:
        with self._lock:
            parts = key.split(".")
            node = self.data
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
            if save:
                self.validate()
                self.save()

    def reset(self) -> None:
        with self._lock:
            self.data = copy.deepcopy(DEFAULT_CONFIG)
            self.save()

    def validate(self) -> None:
        d = self.data
        c = d["clip"]
        c["duration"] = int(min(max(c["duration"], 5), 180))
        c["pre_event"] = int(min(max(c["pre_event"], 0), 120))
        c["post_event"] = int(min(max(c["post_event"], 0), 120))
        if c["pre_event"] + c["post_event"] < 5:
            c["pre_event"], c["post_event"] = 20, 10
        c["max_clips"] = int(min(max(c["max_clips"], 1), 50))
        c["min_gap"] = int(min(max(c["min_gap"], 5), 600))
        det = d["detection"]
        det["sensitivity"] = float(min(max(det["sensitivity"], 0.0), 1.0))
        w = det["weights"]
        for k in DEFAULT_CONFIG["detection"]["weights"]:
            w[k] = max(0.0, float(w.get(k, 0.0)))
        if sum(w.values()) <= 0:
            det["weights"] = copy.deepcopy(DEFAULT_CONFIG["detection"]["weights"])
        o = d["output"]
        o["width"], o["height"] = int(o["width"]), int(o["height"])
        if d["download"]["quality"] not in ("best", "1080p", "720p", "480p", "360p"):
            d["download"]["quality"] = "720p"
        if d["whisper"]["model"] not in ("tiny", "base", "small", "medium", "large-v3"):
            d["whisper"]["model"] = "small"
        live = d["live"]
        live["segment_seconds"] = int(min(max(live["segment_seconds"], 2), 30))
        live["buffer_minutes"] = int(min(max(live["buffer_minutes"], 2), 120))

    def normalized_weights(self, mode: str = "live") -> dict[str, float]:
        """Weights summing to 1. In 'video' mode the chat weight is redistributed."""
        w = dict(self.get("detection.weights"))
        if mode == "video":
            chat = w.pop("chat", 0.0)
            rest = sum(w.values()) or 1.0
            w = {k: v + chat * (v / rest) for k, v in w.items()}
            w["chat"] = 0.0
        total = sum(w.values()) or 1.0
        return {k: v / total for k, v in w.items()}


_instance: Config | None = None


def get_config() -> Config:
    global _instance
    if _instance is None:
        _instance = Config()
    return _instance
