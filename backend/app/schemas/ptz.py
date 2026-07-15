from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PtzMoveRequest(BaseModel):
    deviceId: str = Field(min_length=1)
    channelId: str = Field(default="0", min_length=1)
    action: Literal[
        "up",
        "down",
        "left",
        "right",
        "upLeft",
        "upRight",
        "downLeft",
        "downRight",
        "zoomIn",
        "zoomOut",
    ]
    stepProfile: Literal["small", "medium", "large"] = "small"
    duration: int | None = Field(default=None, ge=50, le=3000)


class PtzMoveResponse(BaseModel):
    accepted: bool
    operationVerified: bool
    verifiedMap: dict[str, str] = Field(default_factory=dict)
    verifiedOperation: str | None = None
    command: dict[str, Any]
    raw: Any
