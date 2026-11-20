"""URL parsing / validation for YouTube and Twitch links."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

_YT_ID = r"[A-Za-z0-9_-]{11}"
PLATFORM_NAMES = {"youtube": "YouTube", "twitch": "Twitch"}


@dataclass
class ParsedURL:
    url: str            # normalized URL
    platform: str       # 'youtube' | 'twitch'
    kind: str           # 'video' | 'live' | 'channel_live' | 'vod' | 'clip'
    ident: str          # video id / channel name / vod id
    valid: bool = True
    message: str = ""


def _invalid(url: str, msg: str) -> ParsedURL:
    return ParsedURL(url=url, platform="", kind="", ident="", valid=False, message=msg)


def parse_url(raw: str) -> ParsedURL:
    url = (raw or "").strip()
    if not url:
        return _invalid(url, "Paste a YouTube or Twitch URL.")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    try:
        p = urlparse(url)
    except ValueError:
        return _invalid(raw, "That does not look like a valid URL.")
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    path = p.path.rstrip("/")
    qs = parse_qs(p.query)

    # ---------------- YouTube ----------------
    if host in ("youtube.com", "music.youtube.com", "youtube-nocookie.com"):
        if path == "/watch" and "v" in qs and re.fullmatch(_YT_ID, qs["v"][0]):
            vid = qs["v"][0]
            return ParsedURL(f"https://www.youtube.com/watch?v={vid}", "youtube", "video", vid)
        m = re.fullmatch(rf"/(?:live|shorts|embed|v)/({_YT_ID})", path)
        if m:
            vid = m.group(1)
            kind = "live" if path.startswith("/live/") else "video"
            return ParsedURL(f"https://www.youtube.com/watch?v={vid}", "youtube", kind, vid)
        m = re.fullmatch(r"/(@[\w.\-]+|channel/[\w-]+|c/[\w.\-]+|user/[\w.\-]+)(?:/live)?", path)
        if m:
            chan = m.group(1)
            return ParsedURL(f"https://www.youtube.com/{chan}/live", "youtube", "channel_live", chan)
        return _invalid(raw, "Unsupported YouTube link. Use a video, /live/ or channel URL.")
    if host == "youtu.be":
        m = re.fullmatch(rf"/({_YT_ID})", path)
        if m:
            vid = m.group(1)
            return ParsedURL(f"https://www.youtube.com/watch?v={vid}", "youtube", "video", vid)
        return _invalid(raw, "Invalid youtu.be link.")

    # ---------------- Twitch ----------------
    if host in ("twitch.tv", "go.twitch.tv"):
        m = re.fullmatch(r"/videos/(\d+)", path)
        if m:
            return ParsedURL(f"https://www.twitch.tv/videos/{m.group(1)}", "twitch", "vod", m.group(1))
        m = re.fullmatch(r"/(\w+)/clip/([\w-]+)", path)
        if m:
            return ParsedURL(url, "twitch", "clip", m.group(2))
        m = re.fullmatch(r"/(\w{3,25})", path)
        reserved = {"directory", "videos", "settings", "subscriptions", "inventory", "wallet", "search", "downloads"}
        if m and m.group(1).lower() not in reserved:
            ch = m.group(1).lower()
            return ParsedURL(f"https://www.twitch.tv/{ch}", "twitch", "channel_live", ch)
        return _invalid(raw, "Unsupported Twitch link. Use https://twitch.tv/<channel>.")
    if host == "clips.twitch.tv":
        return ParsedURL(url, "twitch", "clip", path.strip("/"))

    return _invalid(raw, "Only YouTube and Twitch URLs are supported.")


def validate_for_mode(raw: str, mode: str) -> ParsedURL:
    """mode: 'live' or 'video'. Returns ParsedURL with valid/message set for that mode."""
    p = parse_url(raw)
    if not p.valid:
        return p
    if mode == "live":
        if p.platform == "twitch" and p.kind != "channel_live":
            p.valid, p.message = False, "Live mode needs a Twitch channel URL (twitch.tv/<channel>)."
        elif p.platform == "youtube" and p.kind not in ("video", "live", "channel_live"):
            p.valid, p.message = False, "Live mode needs a YouTube live URL."
        else:
            p.message = f"{PLATFORM_NAMES[p.platform]} live stream: {p.ident}"
    else:
        if p.platform != "youtube" or p.kind not in ("video", "live"):
            p.valid, p.message = False, "Video mode supports YouTube video URLs."
        else:
            p.message = f"YouTube video: {p.ident}"
    return p
