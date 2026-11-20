"""YouTube live chat reader using chat-downloader with direct Innertube live_chat fallback."""
from __future__ import annotations

from collections import deque
import json
import logging
import re
import threading
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

_API_KEY_RE = re.compile(r'"INNERTUBE_API_KEY"\s*:\s*"([^"]+)"')
_CONT_RE = re.compile(r'"continuation"\s*:\s*"([^"]{20,})"')


class YouTubeChatReader:
    """Continuously reads live chat messages from a YouTube livestream without requiring an API key."""

    def __init__(self, url: str, max_messages: int = 5000):
        self.url = url
        self._messages: deque[dict[str, Any]] = deque(maxlen=max_messages)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.connected = False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="youtube-chat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=4.0)
        self.connected = False

    def add_message(self, author: str, message: str, timestamp: float | None = None) -> None:
        with self._lock:
            self._messages.append(
                {
                    "timestamp": timestamp if timestamp is not None else time.time(),
                    "author": author,
                    "message": message,
                    "platform": "youtube",
                }
            )

    def messages_since(self, since_ts: float, until_ts: float | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if until_ts is None:
                return [m for m in self._messages if m["timestamp"] >= since_ts]
            return [m for m in self._messages if since_ts <= m["timestamp"] <= until_ts]

    def recent_messages(self, seconds: float = 30.0) -> list[dict[str, Any]]:
        return self.messages_since(time.time() - seconds)

    def _run(self) -> None:
        while not self._stop.is_set():
            # Primary: chat-downloader
            try:
                from chat_downloader import ChatDownloader

                downloader = ChatDownloader()
                chat = downloader.get_chat(self.url, message_groups=["messages", "superchat"])
                self.connected = True
                for item in chat:
                    if self._stop.is_set():
                        return
                    msg = (item.get("message") or "").strip()
                    author = (item.get("author", {}) or {}).get("name") or "Viewer"
                    if msg:
                        self.add_message(str(author), str(msg))
            except Exception as e:  # noqa: BLE001
                log.debug("chat-downloader fallback triggered: %s", e)

            if self._stop.is_set():
                return

            # Fallback: direct YouTube live_chat polling
            try:
                self._poll_innertube()
            except Exception as e:  # noqa: BLE001
                log.debug("Innertube live chat poll ended: %s", e)
            finally:
                self.connected = False

            if not self._stop.is_set():
                time.sleep(5.0)

    def _poll_innertube(self) -> None:
        sess = requests.Session()
        sess.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        r = sess.get(self.url, timeout=15)
        r.raise_for_status()
        html = r.text
        key_m = _API_KEY_RE.search(html)
        cont_m = _CONT_RE.search(html)
        if not (key_m and cont_m):
            return
        api_key = key_m.group(1)
        continuation = cont_m.group(1)
        seen_ids: deque[str] = deque(maxlen=1000)
        self.connected = True

        while not self._stop.is_set() and continuation:
            endpoint = f"https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?key={api_key}"
            payload = {
                "context": {"client": {"clientName": "WEB", "clientVersion": "2.20240901.00.00"}},
                "continuation": continuation,
            }
            resp = sess.post(endpoint, data=json.dumps(payload), timeout=15)
            if resp.status_code != 200:
                break
            data = resp.json()
            cont_contents = data.get("continuationContents", {}).get("liveChatContinuation", {})
            actions = cont_contents.get("actions") or []
            for act in actions:
                renderer = (
                    act.get("addChatItemAction", {})
                    .get("item", {})
                    .get("liveChatTextMessageRenderer")
                )
                if not renderer:
                    continue
                mid = renderer.get("id") or ""
                if mid and mid in seen_ids:
                    continue
                if mid:
                    seen_ids.append(mid)
                runs = renderer.get("message", {}).get("runs") or []
                text = "".join(str(x.get("text", "")) for x in runs).strip()
                author = renderer.get("authorName", {}).get("simpleText") or "Viewer"
                if text:
                    self.add_message(author, text)

            conts = cont_contents.get("continuations") or []
            next_c = None
            for c in conts:
                for k in ("invalidationContinuationData", "timedContinuationData", "reloadContinuationData"):
                    if k in c and c[k].get("continuation"):
                        next_c = c[k]["continuation"]
                        break
                if next_c:
                    break
            continuation = next_c
            time.sleep(2.5)
