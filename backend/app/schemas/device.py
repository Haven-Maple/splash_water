from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DeviceOnlineRequest(BaseModel):
    deviceId: str = Field(min_length=1)


class DeviceOnlineResponse(BaseModel):
    deviceId: str
    online: bool
    status: str
    raw: Any

