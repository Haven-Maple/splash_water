from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class StreamRequest(BaseModel):
    deviceId: str = Field(min_length=1)
    channelId: str = Field(default="0", min_length=1)


class PreferredStreamRequest(StreamRequest):
    prefer: Literal["flv", "hls"] = "flv"


class StreamResponse(BaseModel):
    streamType: Literal["flv", "hls"]
    streamUrl: str
    fallbackAvailable: bool
    raw: Any

