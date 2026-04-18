from __future__ import annotations

from datetime import datetime


def local_time_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
