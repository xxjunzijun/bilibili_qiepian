from __future__ import annotations

from pydantic import BaseModel, Field


class StreamerIn(BaseModel):
    name: str = Field(min_length=1)
    room_id: str = Field(min_length=1)
    url: str | None = None
    enabled: bool = True
    auto_upload: bool = True
    tid: int = 171
    tags: str = "直播录像,B站录播"
    title_template: str = "{streamer} 直播录像 {date}"
    description_template: str = "自动录制的直播录像\n主播：{streamer}\n直播间：{url}"


class StreamerPatch(BaseModel):
    name: str | None = None
    room_id: str | None = None
    url: str | None = None
    enabled: bool | None = None
    auto_upload: bool | None = None
    tid: int | None = None
    tags: str | None = None
    title_template: str | None = None
    description_template: str | None = None
