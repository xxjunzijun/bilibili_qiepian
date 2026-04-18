from __future__ import annotations

import re
from dataclasses import dataclass

import requests


ROOM_RE = re.compile(r"live\.bilibili\.com/(?:blanc/)?(?P<room>\d+)")


@dataclass
class LiveStatus:
    is_live: bool
    title: str = ""
    raw_status: int = 0


def normalize_room_id(room_id_or_url: str) -> str:
    match = ROOM_RE.search(room_id_or_url)
    if match:
        return match.group("room")
    return room_id_or_url.strip()


def room_url(room_id: str) -> str:
    return f"https://live.bilibili.com/{room_id}"


def fetch_live_status(room_id: str, timeout: float = 10.0) -> LiveStatus:
    url = "https://api.live.bilibili.com/room/v1/Room/get_info"
    response = requests.get(
        url,
        params={"room_id": room_id},
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Referer": f"https://live.bilibili.com/{room_id}",
            "Accept": "application/json, text/plain, */*",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(payload.get("message") or f"Bilibili API error: {payload}")
    data = payload.get("data") or {}
    live_status = int(data.get("live_status") or 0)
    return LiveStatus(
        is_live=live_status == 1,
        title=data.get("title") or "",
        raw_status=live_status,
    )
