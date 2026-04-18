from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR, settings
from app.db import get_db, init_db
from app.schemas import StreamerIn, StreamerPatch
from app.services.bilibili import fetch_live_status, normalize_room_id, room_url
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
                (name, room_id, url, quality, enabled, auto_upload, tid, tags, title_template, description_template)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.name,
                room_id,
                url,
                payload.quality,
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
        file_path = recording.get("file_path")
        if delete_file and file_path:
            path = Path(file_path).resolve()
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


@app.post("/api/recordings/{recording_id}/upload")
def queue_upload(recording_id: int) -> dict:
    with get_db() as db:
        row = db.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Recording not found")
        if row["status"] != "finished":
            raise HTTPException(status_code=400, detail="Recording is not finished")
        db.execute("UPDATE recordings SET upload_status = 'pending', upload_error = NULL WHERE id = ?", (recording_id,))
    return {"ok": True}


app.mount("/", StaticFiles(directory=BASE_DIR / "static", html=True), name="static")
