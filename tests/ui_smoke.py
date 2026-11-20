"""UI smoke test: builds the main window offscreen, simulates events, saves screenshots."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.logger import setup_logging  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "screens"
OUT.mkdir(parents=True, exist_ok=True)

setup_logging()
app = QApplication(sys.argv)
w = MainWindow()
w.resize(1400, 920)
w.show()
d = w.dashboard

# URL validation
d.url_input.setText("https://twitch.tv/directory")
assert not d.btn_start.isEnabled(), "invalid URL must disable start"
d.url_input.setText("https://www.twitch.tv/shroud")
assert d.btn_start.isEnabled(), "valid live URL must enable start"
d.set_mode("video")
assert not d.btn_start.isEnabled(), "twitch channel invalid in video mode"
d.url_input.setText("https://youtu.be/dQw4w9WgXcQ")
assert d.btn_start.isEnabled()
assert d.quality.isVisible() and d.duration.isVisible()
d.set_mode("live")
assert not d.quality.isVisible()

# Simulated job events
d.on_event("status", {"text": "Recording", "color": "#22C55E"})
d.on_event("info", {"title": "Shroud - Ranked grind with the boys"})
import math  # noqa: E402

for i in range(60):
    t = i * 10
    d.on_event("metrics", {"t": t, "chat": 20 + 15 * math.sin(i / 4) + (80 if i == 45 else 0),
                           "audio": 0.3 + 0.2 * math.sin(i / 3) + (0.45 if i == 45 else 0), "audio_threshold": 0.7})
d.on_event("moment", {"t": 450, "start": 430, "end": 460, "score": 0.91, "reasons": ["chat spike x4.1", "loud audio +9 dB"]})
d.on_event("progress", {"stage": "Rendering clip 1/1 (captions)", "percent": 64})
app.processEvents()
w.grab().save(str(OUT / "dashboard.png"))

w._go(2)
app.processEvents()
w.grab().save(str(OUT / "settings.png"))
w.settings.save()
w._go(1)
app.processEvents()
w.grab().save(str(OUT / "gallery.png"))
print("UI smoke test OK ->", OUT)
