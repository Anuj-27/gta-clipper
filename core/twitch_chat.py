"""Anonymous Twitch IRC live chat reader (no OAuth token required)."""
from __future__ import annotations

from collections import deque
import logging
import random
import re
import socket
import ssl
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

_IRC_HOST = "irc.chat.twitch.tv"
_IRC_PORT = 6697
_PRIVMSG_RE = re.compile(r"^(?:@([^ ]+) )?:([^!]+)![^ ]+ PRIVMSG #\w+ :(.*)$")


def parse_irc_line(line: str, now: float | None = None) -> dict[str, Any] | None:
    """Parse a single raw Twitch IRC PRIVMSG line into {timestamp, author, message}."""
    line = line.rstrip("\r\n")
    m = _PRIVMSG_RE.match(line)
    if not m:
        return None
    tags_raw, nick, msg = m.group(1) or "", m.group(2), m.group(3)
    display = nick
    if tags_raw:
        for kv in tags_raw.split(";"):
            if kv.startswith("display-name="):
                val = kv.split("=", 1)[1].strip()
                if val:
                    display = val
                break
    return {
        "timestamp": now if now is not None else time.time(),
        "author": display,
        "message": msg.strip(),
        "platform": "twitch",
    }


class TwitchChatReader:
    """Connects anonymously to Twitch IRC as `justinfanXXXXX` and buffers live chat messages."""

    def __init__(self, channel: str, max_messages: int = 5000):
        self.channel = channel.lstrip("#").strip().lower()
        self._messages: deque[dict[str, Any]] = deque(maxlen=max_messages)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self.connected = False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"twitch-irc-{self.channel}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        s = self._sock
        if s is not None:
            try:
                s.close()
            except OSError:
                pass
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
                    "platform": "twitch",
                }
            )

    def messages_since(self, since_ts: float, until_ts: float | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if until_ts is None:
                return [m for m in self._messages if m["timestamp"] >= since_ts]
            return [m for m in self._messages if since_ts <= m["timestamp"] <= until_ts]

    def recent_messages(self, seconds: float = 30.0) -> list[dict[str, Any]]:
        cutoff = time.time() - seconds
        return self.messages_since(cutoff)

    def _run(self) -> None:
        backoff = 2.0
        ctx = ssl.create_default_context()
        while not self._stop.is_set():
            try:
                raw = socket.create_connection((_IRC_HOST, _IRC_PORT), timeout=15)
                sock = ctx.wrap_socket(raw, server_hostname=_IRC_HOST)
                sock.settimeout(30.0)
                self._sock = sock
                nick = f"justinfan{random.randint(10000, 99999)}"
                sock.sendall(b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n")
                sock.sendall(b"PASS SCHMOOPIIE\r\n")
                sock.sendall(f"NICK {nick}\r\n".encode("utf-8"))
                sock.sendall(f"JOIN #{self.channel}\r\n".encode("utf-8"))
                self.connected = True
                backoff = 2.0
                log.info("Connected anonymously to Twitch IRC #%s as %s", self.channel, nick)

                buf = ""
                while not self._stop.is_set():
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        sock.sendall(b"PING :keepalive\r\n")
                        continue
                    if not chunk:
                        break
                    buf += chunk.decode("utf-8", errors="replace")
                    while "\r\n" in buf:
                        line, buf = buf.split("\r\n", 1)
                        if line.startswith("PING"):
                            payload = line.split(":", 1)[1] if ":" in line else "tmi.twitch.tv"
                            sock.sendall(f"PONG :{payload}\r\n".encode("utf-8"))
                            continue
                        parsed = parse_irc_line(line)
                        if parsed:
                            with self._lock:
                                self._messages.append(parsed)
            except Exception as e:  # noqa: BLE001
                if not self._stop.is_set():
                    log.debug("Twitch IRC reconnect (#%s): %s", self.channel, e)
            finally:
                self.connected = False
                if self._sock is not None:
                    try:
                        self._sock.close()
                    except OSError:
                        pass
                    self._sock = None
            if not self._stop.is_set():
                time.sleep(backoff)
                backoff = min(backoff * 1.7, 25.0)
