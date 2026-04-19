from __future__ import annotations

import json
import os
import signal
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path

from app.config import settings

RETRYABLE_UPLOAD_MARKERS = (
    "Temporary failure in name resolution",
    "failed to lookup address information",
    "dns error",
    "client error (Connect)",
    "connection reset",
    "connection refused",
    "connection timed out",
    "operation timed out",
    "timed out",
)


def _format(template: str, values: dict[str, str | int]) -> str:
    return template.format(**{key: str(value) for key, value in values.items()})


def build_recording_path(streamer_name: str, segment_index: int = 1) -> Path:
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in streamer_name)
    directory = settings.recordings_dir / safe_name
    directory.mkdir(parents=True, exist_ok=True)
    suffix = f"_p{segment_index:02d}" if segment_index > 1 else "_p01"
    return directory / f"{datetime.now():%Y%m%d_%H%M%S}{suffix}.ts"


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
    files = _recording_files(recording)
    if not files:
        return False, "No recording files to upload"

    command = _format(
        settings.upload_command,
        {
            "file": files[0],
            "files": " ".join(shlex.quote(file) for file in files),
            "title": title,
            "description": description,
            "tags": streamer["tags"],
            "tid": streamer["tid"],
            "source": streamer["url"],
        },
    )
    if "{files}" not in settings.upload_command and len(files) > 1:
        command = f"{command} {' '.join(shlex.quote(file) for file in files[1:])}"
    attempts = max(1, settings.upload_retry_attempts)
    delay_seconds = max(0, settings.upload_retry_delay_seconds)
    outputs: list[str] = []
    for attempt in range(1, attempts + 1):
        completed = subprocess.run(command, shell=True, capture_output=True, text=True)
        output = ((completed.stdout or "") + (completed.stderr or "")).strip()
        outputs.append(f"[qiepian] upload attempt {attempt}/{attempts}, exit code {completed.returncode}\n{output}")
        if completed.returncode == 0:
            return True, "\n\n".join(outputs)[-4000:]
        if attempt < attempts and delay_seconds:
            time.sleep(delay_seconds)
    return False, "\n\n".join(outputs)[-4000:]


def is_retryable_upload_error(output: str) -> bool:
    lowered = output.lower()
    return any(marker.lower() in lowered for marker in RETRYABLE_UPLOAD_MARKERS)


def _recording_files(recording: dict) -> list[str]:
    if recording.get("mp4_paths"):
        try:
            files = json.loads(recording["mp4_paths"])
            files = [str(file) for file in files if file]
            if files:
                return files
        except json.JSONDecodeError:
            pass
    if recording.get("segment_paths"):
        try:
            files = json.loads(recording["segment_paths"])
            return [str(file) for file in files if file]
        except json.JSONDecodeError:
            pass
    return [recording["file_path"]] if recording.get("file_path") else []


def source_recording_files(recording: dict) -> list[str]:
    if recording.get("segment_paths"):
        try:
            files = json.loads(recording["segment_paths"])
            return [str(file) for file in files if file]
        except json.JSONDecodeError:
            pass
    return [recording["file_path"]] if recording.get("file_path") else []


TRANSCODE_PROFILES = {
    "copy": {"mode": "copy"},
    "small": {"mode": "h264", "crf": 30, "preset": "veryfast", "audio_bitrate": "96k"},
    "balanced": {"mode": "h264", "crf": 28, "preset": "veryfast", "audio_bitrate": "128k"},
    "high": {"mode": "h264", "crf": 24, "preset": "fast", "audio_bitrate": "160k"},
}


def remux_recording_to_mp4(recording: dict, profile: str | None = None) -> tuple[bool, list[str], str]:
    sources = source_recording_files(recording)
    if not sources:
        return False, [], "No recording files to remux"

    profile_name = _normalize_transcode_profile(profile or recording.get("mp4_profile") or "default")
    outputs: list[str] = []
    logs: list[str] = []
    for source in sources:
        source_path = Path(source)
        output_path = source_path.with_suffix(".mp4")
        command = _build_mp4_command(source_path, output_path, profile_name)
        completed = subprocess.run(command, capture_output=True, text=True)
        output = (completed.stdout or "") + (completed.stderr or "")
        logs.append(output.strip())
        if completed.returncode != 0:
            return False, outputs, "\n".join(logs)[-4000:]
        outputs.append(str(output_path))

    return True, outputs, "\n".join(logs)[-4000:]


def _normalize_transcode_profile(profile: str | None) -> str:
    profile_name = (profile or "default").strip().lower()
    if profile_name == "default":
        return settings.video_transcode_mode if settings.video_transcode_mode in ("copy", "h264") else "balanced"
    if profile_name == "h264":
        return "h264"
    return profile_name if profile_name in TRANSCODE_PROFILES else "balanced"


def _build_mp4_command(source_path: Path, output_path: Path, profile: str | None = None) -> list[str]:
    command = shlex.split(settings.ffmpeg_command) + ["-y", "-i", str(source_path)]
    profile_name = _normalize_transcode_profile(profile)
    options = (
        {"mode": "h264"}
        if profile_name == "h264"
        else TRANSCODE_PROFILES.get(profile_name, TRANSCODE_PROFILES["balanced"])
    )
    if options["mode"] == "h264":
        command.extend(
            [
                "-map",
                "0:v:0",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-preset",
                str(options.get("preset") or settings.video_transcode_preset or "veryfast"),
                "-crf",
                str(options.get("crf") or settings.video_transcode_crf),
                "-c:a",
                "aac",
                "-b:a",
                str(options.get("audio_bitrate") or settings.video_audio_bitrate or "128k"),
            ]
        )
    else:
        command.extend(["-c", "copy"])
    command.extend(["-movflags", "+faststart", str(output_path)])
    return command
