from __future__ import annotations

import threading
import time

from app.config import settings
from app.db import get_db
from app.services.bilibili import fetch_live_status
from app.services.commands import build_recording_path, start_recording, stop_process, upload_recording
from app.time_utils import local_time_text


class RecorderScheduler:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._processes: dict[int, object] = {}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="recorder-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for process in list(self._processes.values()):
            try:
                stop_process(process, timeout=5)
            except Exception:
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as exc:
                print(f"scheduler tick failed: {exc}")
            self._stop.wait(settings.check_interval_seconds)

    def tick(self) -> None:
        with get_db() as db:
            streamers = [dict(row) for row in db.execute("SELECT * FROM streamers WHERE enabled = 1")]

        for streamer in streamers:
            self._check_streamer(streamer)
        self._check_finished_uploads()

    def _check_streamer(self, streamer: dict) -> None:
        with get_db() as db:
            active = db.execute(
                "SELECT * FROM recordings WHERE streamer_id = ? AND status = 'recording' ORDER BY id DESC LIMIT 1",
                (streamer["id"],),
            ).fetchone()

        try:
            live = fetch_live_status(streamer["room_id"])
        except Exception as exc:
            if active:
                with get_db() as db:
                    db.execute("UPDATE recordings SET error = ? WHERE id = ?", (str(exc), active["id"]))
            return

        if live.is_live and not active:
            output = build_recording_path(streamer["name"])
            process = start_recording(streamer, output)
            with get_db() as db:
                cursor = db.execute(
                    """
                    INSERT INTO recordings
                        (streamer_id, status, live_title, started_at, file_path, upload_title, upload_status, process_id)
                    VALUES (?, 'recording', ?, ?, ?, ?, 'waiting', ?)
                    """,
                    (
                        streamer["id"],
                        live.title,
                        local_time_text(),
                        str(output),
                        streamer["title_template"],
                        process.pid,
                    ),
                )
                self._processes[cursor.lastrowid] = process
            return

        if active and not live.is_live:
            process = self._processes.pop(active["id"], None)
            if process:
                stop_process(process)
            next_upload_status = "pending" if streamer["auto_upload"] else "skipped"
            with get_db() as db:
                db.execute(
                    """
                    UPDATE recordings
                    SET status = 'finished', ended_at = ?, upload_status = ?
                    WHERE id = ?
                    """,
                    (local_time_text(), next_upload_status, active["id"]),
                )

    def _check_finished_uploads(self) -> None:
        with get_db() as db:
            pending = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT r.*, s.name, s.room_id, s.url, s.auto_upload, s.tid, s.tags,
                           s.title_template, s.description_template
                    FROM recordings r
                    JOIN streamers s ON s.id = r.streamer_id
                    WHERE r.status = 'finished' AND r.upload_status = 'pending'
                    ORDER BY r.id ASC
                    LIMIT 1
                    """
                )
            ]

        for row in pending:
            with get_db() as db:
                db.execute("UPDATE recordings SET upload_status = 'uploading', upload_error = NULL WHERE id = ?", (row["id"],))
            ok, output = upload_recording(row, row)
            with get_db() as db:
                db.execute(
                    "UPDATE recordings SET upload_status = ?, upload_error = ? WHERE id = ?",
                    ("uploaded" if ok else "failed", output[-2000:], row["id"]),
                )


scheduler = RecorderScheduler()
