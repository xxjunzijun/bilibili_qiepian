from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings:
    host: str = os.getenv("APP_HOST", "0.0.0.0")
    port: int = int(os.getenv("APP_PORT", "8787"))
    data_dir: Path = Path(os.getenv("APP_DATA_DIR", str(BASE_DIR / "data"))).resolve()
    recordings_dir: Path = Path(os.getenv("APP_RECORDINGS_DIR", str(BASE_DIR / "recordings"))).resolve()
    check_interval_seconds: int = int(os.getenv("APP_CHECK_INTERVAL_SECONDS", "60"))
    network_interface: str = os.getenv("APP_NETWORK_INTERFACE", "").strip()
    record_command: str = os.getenv(
        "RECORD_COMMAND",
        'streamlink --retry-streams 60 --retry-max 3 "{url}" "{quality}" -o "{output}"',
    )
    ffmpeg_command: str = os.getenv("FFMPEG_COMMAND", "ffmpeg")
    video_transcode_mode: str = os.getenv("VIDEO_TRANSCODE_MODE", "copy").strip().lower()
    video_transcode_crf: int = int(os.getenv("VIDEO_TRANSCODE_CRF", "28"))
    video_transcode_preset: str = os.getenv("VIDEO_TRANSCODE_PRESET", "veryfast").strip()
    video_audio_bitrate: str = os.getenv("VIDEO_AUDIO_BITRATE", "128k").strip()
    upload_command: str = os.getenv(
        "UPLOAD_COMMAND",
        'biliup --user-cookie ./data/cookies.json upload --copyright 2 --tid {tid} --tag "{tags}" --title "{title}" --desc "{description}" {files}',
    )

    @property
    def database_path(self) -> Path:
        return self.data_dir / "qiepian.db"


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.recordings_dir.mkdir(parents=True, exist_ok=True)
