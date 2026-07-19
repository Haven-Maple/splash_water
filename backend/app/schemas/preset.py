from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PresetQueryRequest(BaseModel):
    deviceId: str = Field(min_length=1)
    channelId: str = Field(default="0", min_length=1)


class PresetSaveRequest(PresetQueryRequest):
    presetIndex: int = Field(ge=0)
    presetName: str = Field(min_length=1)


class PresetTurnRequest(PresetQueryRequest):
    presetIndex: int = Field(ge=0)


class PresetItem(BaseModel):
    presetIndex: int
    presetName: str | None = None
    raw: Any = None


class PresetQueryResponse(BaseModel):
    deviceId: str
    channelId: str
    presets: list[PresetItem]
    raw: Any


class PresetOperationResponse(BaseModel):
    accepted: bool
    presetIndex: int
    raw: Any
    attemptCount: int = 1
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    unknownStateRetrySucceeded: bool = False
