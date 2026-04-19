from __future__ import annotations

import json
import threading
import time
from datetime import datetime

from app.config import settings
from app.db import get_db
from app.services.bilibili import fetch_live_status
from app.services.commands import (
    build_recording_path,
    is_retryable_upload_error,
    remux_recording_to_mp4,
    start_recording,
    stop_process,
    upload_recording,
)
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
        self._sync_recording_processes()
        with get_db() as db:
            streamers = [dict(row) for row in db.execute("SELECT * FROM streamers WHERE enabled = 1")]

        for streamer in streamers:
            self._check_streamer(streamer)
        self._check_finished_remux()
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
                    db.execute(
                        "UPDATE recordings SET status_check_error = ? WHERE id = ?",
                        (str(exc), active["id"]),
                    )
            return

        if active and active["status_check_error"]:
            with get_db() as db:
                db.execute("UPDATE recordings SET status_check_error = NULL WHERE id = ?", (active["id"],))

        if live.is_live and not active:
            output = build_recording_path(streamer["name"], 1)
            process, log_path = start_recording(streamer, output)
            now = local_time_text()
            with get_db() as db:
                cursor = db.execute(
                    """
                    INSERT INTO recordings
                        (
                            streamer_id, status, live_title, started_at,
                            file_path, log_path, current_file_path, current_log_path,
                            current_segment_started_at, current_segment_index, segment_hours,
                            segment_paths, segment_log_paths, remux_status, upload_title,
                            upload_status, status_check_error, process_id
                        )
                    VALUES (?, 'recording', ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 'not_started', ?, 'waiting', NULL, ?)
                    """,
                    (
                        streamer["id"],
                        live.title,
                        now,
                        str(output),
                        str(log_path),
                        str(output),
                        str(log_path),
                        now,
                        int(streamer.get("segment_hours") or 0),
                        json.dumps([str(output)], ensure_ascii=False),
                        json.dumps([str(log_path)], ensure_ascii=False),
                        streamer["title_template"],
                        process.pid,
                    ),
                )
                self._processes[cursor.lastrowid] = process
            return

        if active and live.is_live and self._should_rotate_segment(dict(active)):
            self._rotate_segment(streamer, dict(active))
            return

        if active and not live.is_live:
            process = self._processes.pop(active["id"], None)
            if process:
                stop_process(process)
            next_upload_status = "pending" if streamer["auto_upload"] else "skipped"
            next_remux_status = "pending"
            with get_db() as db:
                db.execute(
                    """
                    UPDATE recordings
                    SET status = 'finished', ended_at = ?, upload_status = ?, remux_status = ?
                    WHERE id = ?
                    """,
                    (local_time_text(), next_upload_status, next_remux_status, active["id"]),
                )

    def _sync_recording_processes(self) -> None:
        with get_db() as db:
            active = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT r.*, s.auto_upload
                    FROM recordings r
                    JOIN streamers s ON s.id = r.streamer_id
                    WHERE r.status = 'recording'
                    """,
                )
            ]

        for recording in active:
            process = self._processes.get(recording["id"])
            if not process:
                with get_db() as db:
                    db.execute(
                        """
                        UPDATE recordings
                        SET status = 'interrupted', ended_at = ?, upload_status = 'skipped',
                            error = ?
                        WHERE id = ?
                        """,
                        (
                            local_time_text(),
                            "Recording process is not managed by this service. The service may have restarted or the process exited unexpectedly.",
                            recording["id"],
                        ),
                    )
                continue

            return_code = process.poll()
            if return_code is not None:
                self._processes.pop(recording["id"], None)
                log_file = getattr(process, "_qiepian_log_file", None)
                if log_file:
                    try:
                        log_file.close()
                    except Exception:
                        pass
                with get_db() as db:
                    if return_code == 0:
                        db.execute(
                            """
                            UPDATE recordings
                            SET status = 'finished', ended_at = ?, upload_status = ?,
                                remux_status = 'pending', error = NULL
                            WHERE id = ?
                            """,
                            (
                                local_time_text(),
                                "pending" if recording["auto_upload"] else "skipped",
                                recording["id"],
                            ),
                        )
                    else:
                        db.execute(
                            """
                            UPDATE recordings
                            SET status = 'recording_failed', ended_at = ?, upload_status = 'skipped',
                                error = ?
                            WHERE id = ?
                            """,
                            (
                                local_time_text(),
                                f"streamlink exited with code {return_code}. Please check the recording log.",
                                recording["id"],
                            ),
                        )

    def _should_rotate_segment(self, recording: dict) -> bool:
        segment_hours = int(recording.get("segment_hours") or 0)
        if segment_hours <= 0:
            return False
        started_at = recording.get("current_segment_started_at") or recording.get("started_at")
        if not started_at:
            return False
        try:
            started = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                started = datetime.fromisoformat(started_at)
            except ValueError:
                return False
        elapsed_seconds = (datetime.now() - started).total_seconds()
        return elapsed_seconds >= segment_hours * 3600

    def _rotate_segment(self, streamer: dict, recording: dict) -> None:
        process = self._processes.pop(recording["id"], None)
        if process:
            stop_process(process)

        next_index = int(recording.get("current_segment_index") or 1) + 1
        output = build_recording_path(streamer["name"], next_index)
        process, log_path = start_recording(streamer, output)
        segment_paths = self._json_list(recording.get("segment_paths"), recording.get("file_path"))
        segment_log_paths = self._json_list(recording.get("segment_log_paths"), recording.get("log_path"))
        segment_paths.append(str(output))
        segment_log_paths.append(str(log_path))
        now = local_time_text()

        with get_db() as db:
            db.execute(
                """
                UPDATE recordings
                SET current_file_path = ?, current_log_path = ?,
                    current_segment_started_at = ?, current_segment_index = ?,
                    segment_paths = ?, segment_log_paths = ?, process_id = ?
                WHERE id = ?
                """,
                (
                    str(output),
                    str(log_path),
                    now,
                    next_index,
                    json.dumps(segment_paths, ensure_ascii=False),
                    json.dumps(segment_log_paths, ensure_ascii=False),
                    process.pid,
                    recording["id"],
                ),
            )
        self._processes[recording["id"]] = process

    def _json_list(self, value: str | None, fallback: str | None) -> list[str]:
        if value:
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(item) for item in parsed if item]
            except json.JSONDecodeError:
                pass
        return [fallback] if fallback else []

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
                    WHERE r.status = 'finished'
                      AND r.upload_status = 'pending'
                      AND (r.next_upload_at IS NULL OR r.next_upload_at <= ?)
                    ORDER BY r.id ASC
                    LIMIT 1
                    """,
                    (int(time.time()),),
                )
            ]

        for row in pending:
            if row.get("remux_status") != "remuxed":
                ok, output = self.remux_recording(row["id"])
                if not ok:
                    with get_db() as db:
                        db.execute(
                            "UPDATE recordings SET upload_status = 'failed', upload_error = ? WHERE id = ?",
                            (f"MP4 remux failed: {output[-1800:]}", row["id"]),
                        )
                    continue
                with get_db() as db:
                    fresh = db.execute(
                        """
                        SELECT r.*, s.name, s.room_id, s.url, s.auto_upload, s.tid, s.tags,
                               s.title_template, s.description_template
                        FROM recordings r
                        JOIN streamers s ON s.id = r.streamer_id
                        WHERE r.id = ?
                        """,
                        (row["id"],),
                    ).fetchone()
                    if fresh:
                        row = dict(fresh)
            with get_db() as db:
                db.execute("UPDATE recordings SET upload_status = 'uploading' WHERE id = ?", (row["id"],))
            ok, output = upload_recording(row, row)
            with get_db() as db:
                if ok:
                    db.execute(
                        """
                        UPDATE recordings
                        SET upload_status = 'uploaded', upload_error = ?, upload_retry_count = 0, next_upload_at = NULL
                        WHERE id = ?
                        """,
                        (output[-4000:], row["id"]),
                    )
                elif self._should_defer_upload_retry(row, output):
                    retry_count = int(row.get("upload_retry_count") or 0) + 1
                    next_upload_at = int(time.time()) + settings.upload_deferred_retry_delay_seconds
                    next_retry_text = datetime.fromtimestamp(next_upload_at).strftime("%Y-%m-%d %H:%M:%S")
                    db.execute(
                        """
                        UPDATE recordings
                        SET upload_status = 'pending',
                            upload_error = ?,
                            upload_retry_count = ?,
                            next_upload_at = ?
                        WHERE id = ?
                        """,
                        (
                            f"Network upload failed. Will retry at {next_retry_text}. "
                            f"Deferred retry {retry_count}/{settings.upload_deferred_retry_attempts}.\n{output[-3500:]}",
                            retry_count,
                            next_upload_at,
                            row["id"],
                        ),
                    )
                else:
                    db.execute(
                        "UPDATE recordings SET upload_status = 'failed', upload_error = ? WHERE id = ?",
                        (output[-4000:], row["id"]),
                    )

    def _should_defer_upload_retry(self, recording: dict, output: str) -> bool:
        if not is_retryable_upload_error(output):
            return False
        retry_count = int(recording.get("upload_retry_count") or 0)
        return retry_count < settings.upload_deferred_retry_attempts

    def _check_finished_remux(self) -> None:
        with get_db() as db:
            row = db.execute(
                """
                SELECT *
                FROM recordings
                WHERE status = 'finished' AND remux_status = 'pending'
                ORDER BY id ASC
                LIMIT 1
                """
            ).fetchone()
        if row:
            self.remux_recording(row["id"])

    def remux_recording(self, recording_id: int, profile: str | None = None) -> tuple[bool, str]:
        with get_db() as db:
            row = db.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
            if not row:
                return False, "Recording not found"
            recording = dict(row)
            if recording["status"] == "recording":
                return False, "Recording is still running"
            profile_name = profile or recording.get("mp4_profile") or "default"
            db.execute(
                "UPDATE recordings SET remux_status = 'remuxing', remux_error = NULL, mp4_profile = ? WHERE id = ?",
                (profile_name, recording_id),
            )

        recording["mp4_profile"] = profile_name
        ok, mp4_paths, output = remux_recording_to_mp4(recording, profile_name)
        with get_db() as db:
            db.execute(
                "UPDATE recordings SET remux_status = ?, mp4_paths = ?, remux_error = ? WHERE id = ?",
                (
                    "remuxed" if ok else "failed",
                    json.dumps(mp4_paths, ensure_ascii=False) if mp4_paths else recording.get("mp4_paths"),
                    None if ok else output[-2000:],
                    recording_id,
                ),
            )
        return ok, output

    def stop_recording(self, recording_id: int, disable_streamer: bool = True) -> tuple[bool, str]:
        with get_db() as db:
            row = db.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
            if not row:
                return False, "Recording not found"
            recording = dict(row)
            if recording["status"] != "recording":
                return False, "Recording is not running"

        process = self._processes.pop(recording_id, None)
        if process:
            stop_process(process)

        with get_db() as db:
            db.execute(
                """
                UPDATE recordings
                SET status = 'interrupted', ended_at = ?, upload_status = 'skipped',
                    error = ?
                WHERE id = ?
                """,
                (local_time_text(), "Recording stopped by user.", recording_id),
            )
            if disable_streamer:
                db.execute("UPDATE streamers SET enabled = 0, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (recording["streamer_id"],))
        return True, "Recording stopped"


scheduler = RecorderScheduler()
