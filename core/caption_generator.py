"""Animated ASS subtitle generator for 9:16 vertical Shorts and Reels."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from core.transcription import Transcript, Word

_IMPORTANT_WORDS = {
    "insane", "crazy", "unbelievable", "impossible", "clutch", "win", "won", "lost",
    "dead", "killed", "headshot", "record", "first", "finally", "legendary", "epic",
    "massive", "huge", "never", "always", "best", "worst", "secret", "police", "cops",
    "heist", "explosion", "busted", "wasted", "million", "thousand", "omg", "wtf",
}


def hex_to_ass_color(hex_color: str, alpha: str = "00") -> str:
    """Convert #RRGGBB to ASS &HAABBGGRR& format."""
    c = (hex_color or "#FFFFFF").strip().lstrip("#")
    if len(c) != 6 or not re.fullmatch(r"[0-9A-Fa-f]{6}", c):
        c = "FFFFFF"
    r, g, b = c[0:2], c[2:4], c[4:6]
    return f"&H{alpha}{b}{g}{r}&".upper()


def _ass_time(seconds: float) -> str:
    s = max(0.0, float(seconds))
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h}:{m:02d}:{sec:05.2f}"


def _escape_ass(text: str) -> str:
    return (
        text.replace("\\", "")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\n", " ")
        .strip()
    )


def is_important_word(word_text: str) -> bool:
    clean = re.sub(r"[^A-Za-z0-9]", "", word_text).lower()
    if not clean:
        return False
    if clean in _IMPORTANT_WORDS:
        return True
    if any(ch.isdigit() for ch in clean):
        return True
    if "!" in word_text or "?" in word_text:
        return True
    return len(clean) >= 8 and word_text.isupper()


def _chunk_words(words: list[Word], max_words: int = 3, max_gap: float = 0.75) -> list[list[Word]]:
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        tok = _escape_ass(w.text)
        if not tok or tok.lower() in ("[music]", "[applause]", "[laughter]"):
            continue
        if cur and (len(cur) >= max_words or (w.start - cur[-1].end) > max_gap):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def generate_ass_subtitles(
    transcript: Transcript,
    output_path: str | Path,
    cfg_captions: dict[str, Any] | None = None,
    width: int = 1080,
    height: int = 1920,
) -> Path:
    """Create an ASS subtitle file with word-level highlighting and pop animations."""
    c = dict(cfg_captions or {})
    style_name = c.get("style", "bold_pop")
    font = c.get("font", "Arial Black")
    font_size = int(c.get("font_size", 78))
    if style_name == "minimal":
        font_size = max(46, int(font_size * 0.72))
    primary = hex_to_ass_color(c.get("primary_color", "#FFFFFF"))
    highlight = hex_to_ass_color(c.get("highlight_color", "#FFE400"))
    outline = hex_to_ass_color(c.get("outline_color", "#000000"))
    shadow = hex_to_ass_color("#000000", alpha="60")
    words_per_line = max(1, int(c.get("words_per_line", 3)))
    position = c.get("position", "lower")

    # Alignment 2 = bottom-center, 5 = middle-center (ASS numpad convention)
    alignment = 5 if position == "middle" else 2
    margin_v = int(height * (0.08 if position == "middle" else 0.18))
    outline_w = 5 if style_name != "minimal" else 3

    header = f"""[Script Info]
Title: GTA Clipper Captions
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{font_size},{primary},{highlight},{outline},{shadow},-1,0,0,0,100,100,1,0,1,{outline_w},2,{alignment},70,70,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    chunks = _chunk_words(transcript.words, max_words=words_per_line)
    events: list[str] = []

    for group in chunks:
        if not group:
            continue
        g_start = max(0.0, group[0].start)
        g_end = max(g_start + 0.15, group[-1].end)

        if style_name == "bold_pop":
            # Emit one event per active word in the group so the spoken word pops & highlights
            for idx, active in enumerate(group):
                w_start = max(0.0, active.start)
                w_end = group[idx + 1].start if idx + 1 < len(group) else max(active.end, w_start + 0.15)
                if w_end <= w_start:
                    w_end = w_start + 0.12
                parts: list[str] = []
                for j, w in enumerate(group):
                    txt = _escape_ass(w.text).upper()
                    if j == idx:
                        parts.append(f"{{\\c{highlight}\\fscx114\\fscy114}}{txt}{{\\c{primary}\\fscx100\\fscy100}}")
                    elif is_important_word(w.text):
                        parts.append(f"{{\\c{highlight}}}{txt}{{\\c{primary}}}")
                    else:
                        parts.append(txt)
                pop = r"{\fad(35,35)\fscx108\fscy108\t(0,95,\fscx100\fscy100)}" if idx == 0 else ""
                line_text = pop + " ".join(parts)
                events.append(f"Dialogue: 0,{_ass_time(w_start)},{_ass_time(w_end)},Default,,0,0,0,,{line_text}")

        elif style_name == "karaoke":
            parts = []
            for w in group:
                dur_cs = max(10, int(round((w.end - w.start) * 100)))
                txt = _escape_ass(w.text).upper()
                parts.append(f"{{\\kf{dur_cs}}}{txt}")
            line_text = f"{{\\c{highlight}\\2c{primary}}}" + " ".join(parts)
            events.append(f"Dialogue: 0,{_ass_time(g_start)},{_ass_time(g_end)},Default,,0,0,0,,{line_text}")

        else:  # 'clean' or 'minimal'
            parts = []
            for w in group:
                txt = _escape_ass(w.text)
                if style_name == "clean":
                    txt = txt.upper()
                if is_important_word(w.text):
                    parts.append(f"{{\\c{highlight}}}{txt}{{\\c{primary}}}")
                else:
                    parts.append(txt)
            line_text = r"{\fad(45,45)}" + " ".join(parts)
            events.append(f"Dialogue: 0,{_ass_time(g_start)},{_ass_time(g_end)},Default,,0,0,0,,{line_text}")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return out
