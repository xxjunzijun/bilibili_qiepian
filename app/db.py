from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app.config import settings


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(settings.database_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS streamers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                room_id TEXT NOT NULL,
                url TEXT NOT NULL,
                quality TEXT NOT NULL DEFAULT 'best',
                segment_hours INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                auto_upload INTEGER NOT NULL DEFAULT 1,
                tid INTEGER NOT NULL DEFAULT 171,
                tags TEXT NOT NULL DEFAULT '直播录像,B站录播',
                title_template TEXT NOT NULL DEFAULT '{streamer} 直播录像 {date}',
                description_template TEXT NOT NULL DEFAULT '自动录制的直播录像\n主播：{streamer}\n直播间：{url}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS recordings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                streamer_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                live_title TEXT,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                ended_at TEXT,
                file_path TEXT,
                log_path TEXT,
                current_file_path TEXT,
                current_log_path TEXT,
                current_segment_started_at TEXT,
                current_segment_index INTEGER NOT NULL DEFAULT 1,
                segment_hours INTEGER NOT NULL DEFAULT 0,
                segment_paths TEXT,
                segment_log_paths TEXT,
                mp4_paths TEXT,
                mp4_profile TEXT,
                remux_status TEXT NOT NULL DEFAULT 'not_started',
                remux_error TEXT,
                upload_title TEXT,
                upload_status TEXT NOT NULL DEFAULT 'not_started',
                upload_error TEXT,
                upload_retry_count INTEGER NOT NULL DEFAULT 0,
                next_upload_at INTEGER,
                process_id INTEGER,
                status_check_error TEXT,
                error TEXT,
                FOREIGN KEY(streamer_id) REFERENCES streamers(id) ON DELETE CASCADE
            );
            """
        )
        _ensure_column(db, "streamers", "quality", "TEXT NOT NULL DEFAULT 'best'")
        _ensure_column(db, "streamers", "segment_hours", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(db, "recordings", "log_path", "TEXT")
        _ensure_column(db, "recordings", "status_check_error", "TEXT")
        _ensure_column(db, "recordings", "current_file_path", "TEXT")
        _ensure_column(db, "recordings", "current_log_path", "TEXT")
        _ensure_column(db, "recordings", "current_segment_started_at", "TEXT")
        _ensure_column(db, "recordings", "current_segment_index", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(db, "recordings", "segment_hours", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(db, "recordings", "segment_paths", "TEXT")
        _ensure_column(db, "recordings", "segment_log_paths", "TEXT")
        _ensure_column(db, "recordings", "mp4_paths", "TEXT")
        _ensure_column(db, "recordings", "mp4_profile", "TEXT")
        _ensure_column(db, "recordings", "remux_status", "TEXT NOT NULL DEFAULT 'not_started'")
        _ensure_column(db, "recordings", "remux_error", "TEXT")
        _ensure_column(db, "recordings", "upload_retry_count", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(db, "recordings", "next_upload_at", "INTEGER")


def _ensure_column(db: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
