from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, settings
from app.db import get_db, init_db
from app.schemas import RemuxIn, StreamerIn, StreamerPatch, UploadIn
from app.services.bilibili import fetch_live_status, normalize_room_id, room_url
from app.services.network import read_network_rx_bytes
from app.services.scheduler import scheduler

app = FastAPI(title="Qiepian Live Recorder")


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    scheduler.start()


@app.on_event("shutdown")
def on_shutdown() -> None:
    scheduler.stop()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/metrics/network")
def network_metrics() -> dict:
    return read_network_rx_bytes()


@app.get("/api/streamers")
def list_streamers() -> list[dict]:
    with get_db() as db:
        return [dict(row) for row in db.execute("SELECT * FROM streamers ORDER BY id DESC")]


@app.post("/api/streamers")
def create_streamer(payload: StreamerIn) -> dict:
    room_id = normalize_room_id(payload.room_id)
    url = payload.url or room_url(room_id)
    with get_db() as db:
        cursor = db.execute(
            """
            INSERT INTO streamers
                (name, room_id, url, quality, segment_hours, enabled, auto_upload, tid, tags, title_template, description_template)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.name,
                room_id,
                url,
                payload.quality,
                payload.segment_hours,
                int(payload.enabled),
                int(payload.auto_upload),
                payload.tid,
                payload.tags,
                payload.title_template,
                payload.description_template,
            ),
        )
        row = db.execute("SELECT * FROM streamers WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)


@app.patch("/api/streamers/{streamer_id}")
def update_streamer(streamer_id: int, payload: StreamerPatch) -> dict:
    data = payload.model_dump(exclude_unset=True)
    if "room_id" in data and data["room_id"]:
        data["room_id"] = normalize_room_id(data["room_id"])
        data.setdefault("url", room_url(data["room_id"]))
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update")
    data["updated_at"] = "CURRENT_TIMESTAMP"

    columns = []
    values = []
    for key, value in data.items():
        if key == "updated_at":
            columns.append("updated_at = CURRENT_TIMESTAMP")
        else:
            columns.append(f"{key} = ?")
            values.append(int(value) if isinstance(value, bool) else value)
    values.append(streamer_id)

    with get_db() as db:
        db.execute(f"UPDATE streamers SET {', '.join(columns)} WHERE id = ?", values)
        row = db.execute("SELECT * FROM streamers WHERE id = ?", (streamer_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Streamer not found")
        return dict(row)


@app.delete("/api/streamers/{streamer_id}")
def delete_streamer(streamer_id: int) -> dict:
    with get_db() as db:
        db.execute("DELETE FROM streamers WHERE id = ?", (streamer_id,))
    return {"ok": True}


@app.delete("/api/recordings/{recording_id}")
def delete_recording(recording_id: int, delete_file: bool = True) -> dict:
    with get_db() as db:
        row = db.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Recording not found")
        recording = dict(row)
        if recording["status"] == "recording":
            raise HTTPException(status_code=400, detail="Recording is still running")

        deleted_file = False
        if delete_file:
            path_candidates = [recording.get("file_path"), recording.get("log_path")]
            for json_field in ("segment_paths", "segment_log_paths", "mp4_paths"):
                if recording.get(json_field):
                    try:
                        path_candidates.extend(json.loads(recording[json_field]))
                    except json.JSONDecodeError:
                        pass
            for path_text in path_candidates:
                if not path_text:
                    continue
                path = Path(path_text).resolve()
                recordings_root = settings.recordings_dir.resolve()
                try:
                    path.relative_to(recordings_root)
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail="Refuse to delete file outside recordings directory") from exc
                if path.exists() and path.is_file():
                    path.unlink()
                    deleted_file = True

        db.execute("DELETE FROM recordings WHERE id = ?", (recording_id,))
    return {"ok": True, "deleted_file": deleted_file}


@app.post("/api/recordings/{recording_id}/stop")
def stop_recording(recording_id: int, disable_streamer: bool = True) -> dict:
    ok, message = scheduler.stop_recording(recording_id, disable_streamer=disable_streamer)
    if not ok:
        raise HTTPException(status_code=400, detail=message)
    return {"ok": True, "message": message, "disable_streamer": disable_streamer}


@app.get("/api/streamers/{streamer_id}/status")
def streamer_status(streamer_id: int) -> dict:
    with get_db() as db:
        row = db.execute("SELECT * FROM streamers WHERE id = ?", (streamer_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Streamer not found")
    streamer = dict(row)
    try:
        status = fetch_live_status(streamer["room_id"])
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"B站状态检查失败：{exc}") from exc
    return {"is_live": status.is_live, "title": status.title, "raw_status": status.raw_status}


@app.get("/api/recordings")
def list_recordings() -> list[dict]:
    with get_db() as db:
        return [
            dict(row)
            for row in db.execute(
                """
                SELECT r.*, s.name AS streamer_name
                FROM recordings r
                JOIN streamers s ON s.id = r.streamer_id
                ORDER BY r.id DESC
                LIMIT 100
                """
            )
        ]


@app.get("/api/recordings/{recording_id}/file")
def recording_file_metrics(recording_id: int) -> dict:
    with get_db() as db:
        row = db.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Recording not found")
    recording = dict(row)
    file_path = recording.get("current_file_path") or recording.get("file_path")
    if not file_path:
        return {"exists": False, "size_bytes": 0, "path": None}

    path = Path(file_path).resolve()
    recordings_root = settings.recordings_dir.resolve()
    try:
        path.relative_to(recordings_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Refuse to stat file outside recordings directory") from exc

    if not path.exists() or not path.is_file():
        return {"exists": False, "size_bytes": 0, "path": str(path)}

    stat = path.stat()
    return {
        "exists": True,
        "size_bytes": stat.st_size,
        "mtime": stat.st_mtime,
        "path": str(path),
    }


@app.post("/api/recordings/{recording_id}/remux")
def remux_recording(recording_id: int, payload: RemuxIn = RemuxIn()) -> dict:
    ok, output = scheduler.remux_recording(recording_id, payload.quality)
    if not ok:
        raise HTTPException(status_code=400, detail=output)
    with get_db() as db:
        row = db.execute("SELECT mp4_paths, remux_status FROM recordings WHERE id = ?", (recording_id,)).fetchone()
    return {"ok": True, "output": output, "mp4_paths": row["mp4_paths"] if row else None}


@app.get("/api/recordings/{recording_id}/media/{segment_index}")
def recording_media(recording_id: int, segment_index: int) -> FileResponse:
    if segment_index < 1:
        raise HTTPException(status_code=400, detail="segment_index starts from 1")
    with get_db() as db:
        row = db.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Recording not found")
    recording = dict(row)
    if not recording.get("mp4_paths"):
        raise HTTPException(status_code=404, detail="MP4 preview is not ready")
    try:
        paths = json.loads(recording["mp4_paths"])
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="Invalid MP4 path list") from exc
    if segment_index > len(paths):
        raise HTTPException(status_code=404, detail="Segment not found")

    path = Path(paths[segment_index - 1]).resolve()
    recordings_root = settings.recordings_dir.resolve()
    try:
        path.relative_to(recordings_root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Refuse to serve file outside recordings directory") from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Media file not found")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.post("/api/recordings/{recording_id}/upload")
def queue_upload(recording_id: int, payload: UploadIn = UploadIn()) -> dict:
    with get_db() as db:
        row = db.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Recording not found")
        recording = dict(row)
        if recording["status"] == "recording":
            raise HTTPException(status_code=400, detail="Recording is still running")
        has_source = bool(recording.get("file_path") or recording.get("segment_paths") or recording.get("mp4_paths"))
        if not has_source:
            raise HTTPException(status_code=400, detail="Recording has no media file")
        remux_status = recording.get("remux_status") or "not_started"
        next_remux_status = remux_status if remux_status == "remuxed" else "pending"
        profile_name = (payload.quality or recording.get("mp4_profile") or "default").strip().lower()
        db.execute(
            """
            UPDATE recordings
            SET status = 'finished',
                upload_status = 'pending',
                upload_error = NULL,
                upload_retry_count = 0,
                next_upload_at = NULL,
                remux_status = ?,
                mp4_profile = ?,
                remux_error = NULL
            WHERE id = ?
            """,
            (next_remux_status, profile_name, recording_id),
        )
    return {"ok": True}


app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")
