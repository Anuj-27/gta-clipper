# GTA Clipper — Automatic AI Video Clipping Desktop Application

**GTA Clipper** is a 100% free, local-first Windows desktop application that automatically identifies exciting moments from **YouTube** and **Twitch** livestreams and uploaded YouTube videos, turning them into vertical **9:16 (1080×1920)** YouTube Shorts and Instagram Reels with animated captions and context-grounded titles.

---

## Features

### Mode A: Live Stream Clipper (`YouTube` & `Twitch`)
- Paste any active YouTube or Twitch livestream URL.
- Automatically detects platform, stream title, and channel information via `yt-dlp`.
- Continuously records the stream into a timestamped **MPEG-TS rolling buffer** (`core/rolling_buffer.py` & `core/stream_recorder.py`) using `FFmpeg` with automatic network reconnection.
- Reads live chat in real time without paid API keys or OAuth tokens:
  - **Twitch**: Anonymous TLS IRC (`justinfanXXXXX` on `irc.chat.twitch.tv:6697`).
  - **YouTube**: `chat-downloader` with an automatic Innertube `get_live_chat` continuation fallback.
- Every 10 seconds:
  - Counts chat messages and detects sudden spikes and hype reactions.
  - Decodes the buffered audio window to measure RMS loudness, peak amplitude, sudden dB jumps, and high-frequency shouting/excitement.
  - Computes a weighted excitement score and plots live **Chat Activity** and **Audio Excitement** graphs.
- When both chat activity and audio excitement spike:
  - Extracts **20 seconds before** the event and **10 seconds after** it (configurable) across buffered segments into a seamless 30-second clip while the stream continues recording.
  - Transcribes speech with local **`faster-whisper`**.
  - Generates a truthful, catchy title from spoken content and relevant chat messages.
  - Burns animated vertical captions and saves the completed clip immediately into `clips/`.

### Mode B: YouTube Video Clipper (Uploaded VODs)
- Paste any regular YouTube video URL.
- Fetches video metadata and downloads the video at user-selectable quality (`best`, `1080p`, `720p`, `480p`, `360p`).
- Retrieves available YouTube subtitles (`.json3` / `.vtt`) first, automatically falling back to local **`faster-whisper`** speech recognition when subtitles are unavailable.
- Scans the full video timeline for exciting dialogue, funny/emotional reactions, surprising statements, loudness jumps, and scene/action cuts.
- Ranks candidate moments by engagement potential while preventing duplicate or overlapping clips.
- Exports vertical 9:16 clips with subject-aware cropping, animated captions, and thumbnails.
- Supports safe **Stop & Resume**: stopping a job preserves downloaded media and already-rendered clips in SQLite so starting the same URL again resumes where it left off.

### Configurable AI Scoring System
Default weights (customizable in **Settings**):
- **Chat activity**: 30% (automatically redistributed to transcript and scene signals in YouTube Video Mode)
- **Audio excitement**: 25%
- **Speech & dialogue importance**: 20%
- **Sudden scene / action changes**: 15%
- **Emotional or funny context**: 10%

### Automatic Vertical Video Editing
- **9:16 vertical output** (`1080x1920` Full HD or `720x1280` HD).
- **Subject-aware cropping**: uses OpenCV face detection + motion saliency to center the subject horizontally before cropping 16:9 footage to 9:16.
- **Animated ASS subtitles**: supports `Bold Pop` (word-by-word pop + active word highlight), `Karaoke`, `Clean`, and `Minimal` styles.
- **Audio preservation**: original audio preserved with smooth edge fades and optional background music mixing (disabled by default).

---

## Project Structure

```text
GTA Clipper/
├── main.py                     # Application entry point (--check for diagnostics)
├── run_gta_clipper.bat         # Double-click Windows launcher
├── requirements.txt            # Python package dependencies
├── README.md                   # Documentation
├── bin/                        # Portable FFmpeg, FFprobe, and Deno binaries
├── config/
│   └── settings.json           # Persistent user configuration
├── core/
│   ├── paths.py                # Path definitions & binary discovery
│   ├── config.py               # Thread-safe settings manager & weight normalization
│   ├── logger.py               # Rotating file + UI callback logger
│   ├── url_utils.py            # YouTube & Twitch URL parser and validator
│   ├── media.py                # FFmpeg/FFprobe wrappers & audio decoding
│   ├── stream_recorder.py      # Continuous livestream recorder with auto-reconnect
│   ├── rolling_buffer.py       # Timestamped MPEG-TS rolling segment buffer
│   ├── youtube_chat.py         # Keyless YouTube live chat reader
│   ├── twitch_chat.py          # Anonymous Twitch IRC live chat reader
│   ├── audio_analyzer.py       # RMS, peak dB, shouting & loudness jump analyzer
│   ├── excitement_detector.py  # Multi-modal excitement detector & candidate ranker
│   ├── video_analyzer.py       # YouTube VOD downloader, scene cut & smart-crop tracker
│   ├── transcription.py        # Subtitle parser & faster-whisper local transcriber
│   ├── caption_generator.py    # Animated ASS subtitle generator
│   ├── title_generator.py      # Context-grounded Shorts title generator
│   ├── clip_generator.py       # 9:16 FFmpeg vertical renderer & thumbnail generator
│   └── pipeline.py             # LiveClipJob and VideoClipJob orchestrators
├── ui/
│   ├── theme.py                # Dark theme stylesheet
│   ├── widgets.py              # Custom StatCard and live LineGraph widgets
│   ├── job_controller.py       # Background thread to Qt signal bridge
│   ├── main_window.py          # Main window & sidebar navigation
│   ├── dashboard.py            # Live/Video mode dashboard, graphs & log view
│   ├── settings.py             # Full settings editor
│   └── clip_gallery.py         # Thumbnail gallery, player launcher & transcript viewer
├── database/
│   └── db.py                   # SQLite persistence for jobs, moments, and clips
├── assets/                     # Application icon & static assets
├── models/                     # Local faster-whisper CTranslate2 model cache
├── temp/                       # Working directory for downloads & rolling buffers
├── clips/                      # Output folder for rendered 9:16 MP4 clips
└── tests/                      # Automated phase & end-to-end verification tests
```

---

## Quick Start (Windows)

1. **Launch the desktop interface**:
   - Double-click **`run_gta_clipper.bat`**, or run:
     ```powershell
     .\.venv\Scripts\python.exe main.py
     ```
2. **Verify dependencies from the command line**:
   ```powershell
   .\.venv\Scripts\python.exe main.py --check
   ```
3. **Run the automated test suite**:
   ```powershell
   .\.venv\Scripts\python.exe tests\test_phase1.py
   .\.venv\Scripts\python.exe tests\test_phase2.py
   .\.venv\Scripts\python.exe tests\test_phase3.py
   .\.venv\Scripts\python.exe tests\test_phase4.py
   .\.venv\Scripts\python.exe tests\test_phase5_6.py
   .\.venv\Scripts\python.exe tests\test_phase7_8.py
   .\.venv\Scripts\python.exe tests\test_end_to_end.py
   ```

---

## Responsible Use
- GTA Clipper runs 100% locally on your machine and never uploads content automatically.
- It does not bypass DRM, paywalls, or access restrictions.
- Only record or clip content that you own or have permission to use.
