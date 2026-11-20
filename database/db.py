"""SQLite storage for jobs, detected moments and generated clips."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from core.paths import DATABASE_DIR

DB_FILE = DATABASE_DIR / "history.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    mode        TEXT NOT NULL,              -- 'live' or 'video'
    url         TEXT NOT NULL,
    platform    TEXT,
    title       TEXT,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending/running/stopped/done/error
    state_json  TEXT,                       -- resumable progress information
    error       TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS moments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    start       REAL NOT NULL,
    end         REAL NOT NULL,
    score       REAL NOT NULL,
    reasons     TEXT,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    path        TEXT NOT NULL,
    thumbnail   TEXT,
    title       TEXT,
    score       REAL,
    start       REAL,
    end         REAL,
    duration    REAL,
    source_url  TEXT,
    transcript  TEXT,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clips_created ON clips(created_at DESC);
"""


class Database:
    def __init__(self, path: Path = DB_FILE):
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    # ---- jobs -----------------------------------------------------------
    def create_job(self, mode: str, url: str, platform: str = "", title: str = "") -> int:
        now = time.time()
        cur = self._exec(
            "INSERT INTO jobs(mode,url,platform,title,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (mode, url, platform, title, "running", now, now),
        )
        return int(cur.lastrowid)

    def update_job(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        if "state" in fields:
            fields["state_json"] = json.dumps(fields.pop("state"))
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self._exec(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not rows:
            return None
        job = rows[0]
        job["state"] = json.loads(job["state_json"]) if job.get("state_json") else {}
        return job

    def find_resumable_job(self, mode: str, url: str) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT id FROM jobs WHERE mode=? AND url=? AND status IN ('stopped','error','running') "
            "ORDER BY id DESC LIMIT 1",
            (mode, url),
        )
        return self.get_job(rows[0]["id"]) if rows else None

    def recent_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))

    # ---- moments --------------------------------------------------------
    def add_moment(self, job_id: int, start: float, end: float, score: float, reasons: list[str]) -> int:
        cur = self._exec(
            "INSERT INTO moments(job_id,start,end,score,reasons,created_at) VALUES(?,?,?,?,?,?)",
            (job_id, start, end, score, json.dumps(reasons), time.time()),
        )
        return int(cur.lastrowid)

    def moments_for_job(self, job_id: int) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM moments WHERE job_id=? ORDER BY start", (job_id,))

    # ---- clips ----------------------------------------------------------
    def add_clip(self, **c: Any) -> int:
        c.setdefault("created_at", time.time())
        cols = ", ".join(c)
        qs = ", ".join("?" for _ in c)
        cur = self._exec(f"INSERT INTO clips({cols}) VALUES({qs})", tuple(c.values()))
        return int(cur.lastrowid)

    def list_clips(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self._query("SELECT * FROM clips ORDER BY created_at DESC LIMIT ?", (limit,))
        return [r for r in rows if Path(r["path"]).exists()]

    def clips_for_job(self, job_id: int) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM clips WHERE job_id=? ORDER BY start", (job_id,))

    def delete_clip(self, clip_id: int) -> None:
        self._exec("DELETE FROM clips WHERE id=?", (clip_id,))

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
    return _db
