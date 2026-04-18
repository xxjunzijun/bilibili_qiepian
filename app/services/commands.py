from __future__ import annotations

import os
import signal
import subprocess
from datetime import datetime
from pathlib import Path

from app.config import settings


def _format(template: str, values: dict[str, str | int]) -> str:
    return template.format(**{key: str(value) for key, value in values.items()})


def build_recording_path(streamer_name: str) -> Path:
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in streamer_name)
    directory = settings.recordings_dir / safe_name
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{datetime.now():%Y%m%d_%H%M%S}.ts"


def start_recording(streamer: dict, output: Path) -> tuple[subprocess.Popen, Path]:
    log_path = output.with_suffix(".log")
    command = _format(
        settings.record_command,
        {
            "url": streamer["url"],
            "output": str(output),
            "streamer_name": streamer["name"],
            "room_id": streamer["room_id"],
            "quality": streamer.get("quality") or "best",
        },
    )
    log_file = log_path.open("a", encoding="utf-8")
    log_file.write(f"[qiepian] command: {command}\n")
    log_file.flush()
    kwargs = {
        "shell": True,
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "text": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["preexec_fn"] = os.setsid
    process = subprocess.Popen(command, **kwargs)
    process._qiepian_log_file = log_file  # type: ignore[attr-defined]
    return process, log_path


def stop_process(process: subprocess.Popen, timeout: int = 30) -> None:
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        process.wait(timeout=timeout)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
    log_file = getattr(process, "_qiepian_log_file", None)
    if log_file:
        try:
            log_file.close()
        except Exception:
            pass


def upload_recording(streamer: dict, recording: dict) -> tuple[bool, str]:
    if not settings.upload_command.strip():
        return False, "UPLOAD_COMMAND is empty"

    now = datetime.now()
    title = (recording.get("upload_title") or streamer["title_template"]).format(
        streamer=streamer["name"],
        date=f"{now:%Y-%m-%d}",
        title=recording.get("live_title") or "",
        url=streamer["url"],
    )
    description = streamer["description_template"].format(
        streamer=streamer["name"],
        date=f"{now:%Y-%m-%d}",
        title=recording.get("live_title") or "",
        url=streamer["url"],
    )
    command = _format(
        settings.upload_command,
        {
            "file": recording["file_path"],
            "title": title,
            "description": description,
            "tags": streamer["tags"],
            "tid": streamer["tid"],
            "source": streamer["url"],
        },
    )
    completed = subprocess.run(command, shell=True, capture_output=True, text=True)
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode == 0, output.strip()
