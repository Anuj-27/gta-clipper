"""Settings page bound to the persistent Config."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.config import get_config

RESOLUTIONS = {"1080 x 1920 (Full HD)": (1080, 1920), "720 x 1280 (HD, faster)": (720, 1280)}
CAPTION_STYLES = {
    "Bold Pop (word highlight + pop)": "bold_pop",
    "Karaoke (word-by-word fill)": "karaoke",
    "Clean (white, no animation)": "clean",
    "Minimal (small lower text)": "minimal",
}


def _spin(lo: int, hi: int, suffix: str = "") -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    if suffix:
        s.setSuffix(suffix)
    return s


def _dspin(lo: float, hi: float, step: float = 0.05) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(2)
    return s


class SettingsPage(QWidget):
    saved = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.cfg = get_config()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        h1 = QLabel("Settings")
        h1.setObjectName("H1")
        outer.addWidget(h1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("ScrollBody")
        grid = QGridLayout(body)
        grid.setSpacing(16)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # ---- clips ------------------------------------------------------
        g = QGroupBox("Clips")
        f = QFormLayout(g)
        self.duration = _spin(5, 180, " s")
        self.pre = _spin(0, 120, " s")
        self.post = _spin(0, 120, " s")
        self.max_clips = _spin(1, 50)
        self.min_gap = _spin(5, 600, " s")
        f.addRow("Clip duration (video mode)", self.duration)
        f.addRow("Pre-event duration (live)", self.pre)
        f.addRow("Post-event duration (live)", self.post)
        f.addRow("Maximum number of clips", self.max_clips)
        f.addRow("Minimum gap between clips", self.min_gap)
        grid.addWidget(g, 0, 0)

        # ---- output -----------------------------------------------------
        g = QGroupBox("Output video")
        f = QFormLayout(g)
        self.resolution = QComboBox()
        self.resolution.addItems(list(RESOLUTIONS))
        self.quality = QComboBox()
        self.quality.addItems(["best", "1080p", "720p", "480p", "360p"])
        self.smart_crop = QCheckBox("Subject-aware cropping (faces / motion)")
        self.music = QCheckBox("Add background music")
        music_row = QHBoxLayout()
        self.music_path = QLineEdit()
        self.music_path.setPlaceholderText("Choose an audio file you have rights to use…")
        browse = QPushButton("Browse")
        browse.clicked.connect(self._pick_music)
        music_row.addWidget(self.music_path, 1)
        music_row.addWidget(browse)
        self.music_vol = _dspin(0.0, 1.0, 0.02)
        f.addRow("Output resolution", self.resolution)
        f.addRow("Download quality", self.quality)
        f.addRow(self.smart_crop)
        f.addRow(self.music)
        f.addRow("Music file", music_row)
        f.addRow("Music volume", self.music_vol)
        grid.addWidget(g, 0, 1)

        # ---- AI / whisper -----------------------------------------------
        g = QGroupBox("Transcription (faster-whisper, local)")
        f = QFormLayout(g)
        self.whisper_model = QComboBox()
        self.whisper_model.addItems(["tiny", "base", "small", "medium", "large-v3"])
        self.whisper_device = QComboBox()
        self.whisper_device.addItems(["auto", "cpu", "cuda"])
        self.language = QLineEdit()
        self.language.setPlaceholderText("auto-detect (or e.g. en, hi, es)")
        hint = QLabel("tiny/base are fast on CPU; small is a good balance; medium/large need a strong PC or GPU.")
        hint.setObjectName("Dim")
        hint.setWordWrap(True)
        f.addRow("Whisper model", self.whisper_model)
        f.addRow("Device", self.whisper_device)
        f.addRow("Language", self.language)
        f.addRow(hint)
        grid.addWidget(g, 1, 0)

        # ---- captions ---------------------------------------------------
        g = QGroupBox("Captions")
        f = QFormLayout(g)
        self.captions_on = QCheckBox("Burn animated captions into clips")
        self.caption_style = QComboBox()
        self.caption_style.addItems(list(CAPTION_STYLES))
        self.font = QComboBox()
        self.font.setEditable(True)
        self.font.addItems(["Arial Black", "Impact", "Segoe UI Black", "Verdana", "Arial"])
        self.font_size = _spin(30, 160)
        self.words_per_line = _spin(1, 8)
        self.highlight = QComboBox()
        self.highlight.setEditable(True)
        self.highlight.addItems(["#FFE400", "#00FF85", "#FF2D75", "#22D3EE", "#FFFFFF"])
        self.position = QComboBox()
        self.position.addItems(["lower", "middle"])
        f.addRow(self.captions_on)
        f.addRow("Caption style", self.caption_style)
        f.addRow("Font", self.font)
        f.addRow("Font size", self.font_size)
        f.addRow("Words per caption", self.words_per_line)
        f.addRow("Highlight colour", self.highlight)
        f.addRow("Position", self.position)
        grid.addWidget(g, 1, 1)

        # ---- detection --------------------------------------------------
        g = QGroupBox("AI clip detection")
        f = QFormLayout(g)
        sens_row = QHBoxLayout()
        self.sensitivity = QSlider(Qt.Horizontal)
        self.sensitivity.setRange(0, 100)
        self.sens_label = QLabel()
        self.sensitivity.valueChanged.connect(lambda v: self.sens_label.setText(f"{v}%"))
        sens_row.addWidget(self.sensitivity, 1)
        sens_row.addWidget(self.sens_label)
        f.addRow("Detection sensitivity", sens_row)
        self.weights: dict[str, QDoubleSpinBox] = {}
        for key, label in (
            ("chat", "Chat activity weight"),
            ("audio", "Audio excitement weight"),
            ("speech", "Speech / dialogue weight"),
            ("scene", "Scene / action change weight"),
            ("emotion", "Emotion / funny weight"),
        ):
            self.weights[key] = _dspin(0.0, 1.0, 0.05)
            f.addRow(label, self.weights[key])
        note = QLabel("Weights are normalised automatically. In YouTube video mode the chat weight is "
                      "redistributed to transcript and scene signals.")
        note.setObjectName("Dim")
        note.setWordWrap(True)
        f.addRow(note)
        grid.addWidget(g, 2, 0)

        # ---- live -------------------------------------------------------
        g = QGroupBox("Live recording")
        f = QFormLayout(g)
        self.segment = _spin(2, 30, " s")
        self.buffer = _spin(2, 120, " min")
        self.cooldown = _spin(10, 900, " s")
        self.reconnects = _spin(0, 200)
        f.addRow("Segment length", self.segment)
        f.addRow("Rolling buffer length", self.buffer)
        f.addRow("Cooldown between clips", self.cooldown)
        f.addRow("Reconnect attempts", self.reconnects)
        grid.addWidget(g, 2, 1)

        # ---- buttons ----------------------------------------------------
        btns = QHBoxLayout()
        btns.addStretch(1)
        reset = QPushButton("Reset to defaults")
        reset.clicked.connect(self._reset)
        save = QPushButton("Save settings")
        save.setObjectName("Primary")
        save.clicked.connect(self.save)
        btns.addWidget(reset)
        btns.addWidget(save)
        outer.addLayout(btns)
        self.load()

    def _pick_music(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Background music", "", "Audio (*.mp3 *.wav *.m4a *.ogg *.flac)")
        if path:
            self.music_path.setText(path)

    def load(self) -> None:
        c = self.cfg
        self.duration.setValue(c.get("clip.duration"))
        self.pre.setValue(c.get("clip.pre_event"))
        self.post.setValue(c.get("clip.post_event"))
        self.max_clips.setValue(c.get("clip.max_clips"))
        self.min_gap.setValue(c.get("clip.min_gap"))
        res = (c.get("output.width"), c.get("output.height"))
        self.resolution.setCurrentText(next((k for k, v in RESOLUTIONS.items() if v == res), list(RESOLUTIONS)[0]))
        self.quality.setCurrentText(c.get("download.quality"))
        self.smart_crop.setChecked(c.get("output.smart_crop"))
        self.music.setChecked(c.get("output.background_music"))
        self.music_path.setText(c.get("output.music_path"))
        self.music_vol.setValue(c.get("output.music_volume"))
        self.whisper_model.setCurrentText(c.get("whisper.model"))
        self.whisper_device.setCurrentText(c.get("whisper.device"))
        self.language.setText(c.get("whisper.language"))
        self.captions_on.setChecked(c.get("captions.enabled"))
        style = c.get("captions.style")
        self.caption_style.setCurrentText(next((k for k, v in CAPTION_STYLES.items() if v == style), list(CAPTION_STYLES)[0]))
        self.font.setCurrentText(c.get("captions.font"))
        self.font_size.setValue(c.get("captions.font_size"))
        self.words_per_line.setValue(c.get("captions.words_per_line"))
        self.highlight.setCurrentText(c.get("captions.highlight_color"))
        self.position.setCurrentText(c.get("captions.position"))
        self.sensitivity.setValue(int(c.get("detection.sensitivity") * 100))
        self.sens_label.setText(f"{self.sensitivity.value()}%")
        for k, w in self.weights.items():
            w.setValue(c.get(f"detection.weights.{k}"))
        self.segment.setValue(c.get("live.segment_seconds"))
        self.buffer.setValue(c.get("live.buffer_minutes"))
        self.cooldown.setValue(c.get("live.cooldown_seconds"))
        self.reconnects.setValue(c.get("live.reconnect_attempts"))

    def save(self) -> None:
        c = self.cfg
        if self.music.isChecked() and not self.music_path.text().strip():
            QMessageBox.warning(self, "Background music", "Choose a music file or disable background music.")
            return
        s = lambda k, v: c.set(k, v, save=False)  # noqa: E731
        s("clip.duration", self.duration.value())
        s("clip.pre_event", self.pre.value())
        s("clip.post_event", self.post.value())
        s("clip.max_clips", self.max_clips.value())
        s("clip.min_gap", self.min_gap.value())
        w, h = RESOLUTIONS[self.resolution.currentText()]
        s("output.width", w)
        s("output.height", h)
        s("download.quality", self.quality.currentText())
        s("output.smart_crop", self.smart_crop.isChecked())
        s("output.background_music", self.music.isChecked())
        s("output.music_path", self.music_path.text().strip())
        s("output.music_volume", self.music_vol.value())
        s("whisper.model", self.whisper_model.currentText())
        s("whisper.device", self.whisper_device.currentText())
        s("whisper.language", self.language.text().strip().lower())
        s("captions.enabled", self.captions_on.isChecked())
        s("captions.style", CAPTION_STYLES[self.caption_style.currentText()])
        s("captions.font", self.font.currentText().strip() or "Arial Black")
        s("captions.font_size", self.font_size.value())
        s("captions.words_per_line", self.words_per_line.value())
        s("captions.highlight_color", self.highlight.currentText().strip())
        s("captions.position", self.position.currentText())
        s("detection.sensitivity", self.sensitivity.value() / 100)
        for k, wdg in self.weights.items():
            s(f"detection.weights.{k}", wdg.value())
        s("live.segment_seconds", self.segment.value())
        s("live.buffer_minutes", self.buffer.value())
        s("live.cooldown_seconds", self.cooldown.value())
        s("live.reconnect_attempts", self.reconnects.value())
        c.validate()
        c.save()
        self.load()
        self.saved.emit()

    def _reset(self) -> None:
        if QMessageBox.question(self, "Reset", "Restore all default settings?") == QMessageBox.Yes:
            self.cfg.reset()
            self.load()
            self.saved.emit()
