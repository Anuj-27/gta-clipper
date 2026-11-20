"""Context-grounded title generator for YouTube Shorts and Instagram Reels.

Generates concise, engaging titles strictly from actual transcript lines, live chat reactions,
and stream/video metadata without inventing quotes or events.
"""
from __future__ import annotations

from collections import Counter
import re
from typing import Any

from core.transcription import Transcript

_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "you", "your", "are", "was", "were",
    "have", "has", "had", "not", "but", "what", "when", "where", "who", "why", "how",
    "from", "they", "them", "their", "there", "here", "just", "like", "know", "think",
    "going", "gonna", "want", "really", "about", "into", "over", "then", "than", "some",
    "more", "very", "can", "will", "would", "could", "should", "did", "does", "done",
    "get", "got", "getting", "let", "lets", "one", "two", "out", "all", "too", "yes",
    "yeah", "okay", "right", "now", "back", "down", "see", "look", "come", "came",
}
_NOISE_CHAT = {
    "lol", "lmao", "lmfao", "rofl", "kekw", "lul", "lulw", "pog", "poggers", "pagman",
    "omg", "wtf", "clip", "it", "that", "gg", "ez", "w", "l", "f", "rip", "haha", "hahaha",
}


def _clean_text(s: str) -> str:
    s = re.sub(r"\[(?:music|applause|laughter)\]", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _best_spoken_phrase(transcript: Transcript) -> str:
    """Pick the most expressive actual phrase spoken in the clip without altering its meaning."""
    best = ""
    best_score = -1.0
    for seg in transcript.segments:
        txt = _clean_text(seg.text)
        if not txt:
            continue
        # Split on sentence boundaries
        sentences = [p.strip() for p in re.split(r"(?<=[.!?])\s+", txt) if p.strip()]
        for sent in sentences:
            words = sent.split()
            if len(words) < 3:
                continue
            if len(words) > 11:
                sent = " ".join(words[:10]) + "…"
            score = 0.0
            if "!" in sent or "?" in sent:
                score += 2.0
            score += sum(1.2 for w in words if len(w) >= 5 and w.lower() not in _STOPWORDS)
            if 4 <= len(words) <= 9:
                score += 1.5
            if score > best_score:
                best_score = score
                best = sent
    return best


def _top_chat_topic(chat_messages: list[dict[str, Any]]) -> str:
    """Extract a recurring substantive topic word or short phrase from live chat if present."""
    if not chat_messages:
        return ""
    counts: Counter[str] = Counter()
    for m in chat_messages:
        msg = str(m.get("message", "")).strip()
        for tok in re.findall(r"[A-Za-z0-9']{3,}", msg):
            low = tok.lower()
            if low not in _STOPWORDS and low not in _NOISE_CHAT:
                counts[tok] += 1
    if not counts:
        return ""
    word, freq = counts.most_common(1)[0]
    return word if freq >= 2 else ""


def _clean_source_title(source_title: str) -> str:
    if not source_title:
        return ""
    # Strip trailing stream tags or hashtags
    cleaned = re.split(r"\s+[|\-–—]\s+|\s+#\w+", source_title)[0].strip()
    return cleaned[:48].strip()


def generate_clip_title(
    transcript: Transcript | None = None,
    chat_messages: list[dict[str, Any]] | None = None,
    source_title: str = "",
    reasons: list[str] | None = None,
    clip_index: int = 1,
) -> str:
    """Generate a truthful, punchy Shorts/Reels title (< 75 chars) from transcript, chat, and context."""
    chat_messages = chat_messages or []
    reasons = reasons or []
    phrase = _best_spoken_phrase(transcript) if transcript else ""
    chat_topic = _top_chat_topic(chat_messages)
    base_title = _clean_source_title(source_title)

    if phrase:
        # Remove surrounding quotes; we do not invent quotes—this is verbatim from the transcript
        title = phrase.strip(" \"'")
        if base_title and len(title) + len(base_title) + 3 <= 68 and base_title.lower() not in title.lower():
            title = f"{title} | {base_title}"
        return title[:75]

    if chat_topic and base_title:
        return f"{base_title} — {chat_topic}"[:75]

    if base_title:
        reason_tag = ""
        for r in reasons:
            if "chat spike" in r:
                reason_tag = "Live Chat Highlight"
                break
            if "loud" in r or "audio" in r:
                reason_tag = "High-Energy Moment"
                break
            if "scene" in r:
                reason_tag = "Action Highlight"
                break
        if reason_tag:
            return f"{base_title} — {reason_tag}"[:75]
        return f"{base_title} (Highlight #{clip_index})"[:75]

    return f"Stream Highlight #{clip_index}"
