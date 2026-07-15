from __future__ import annotations

from pydantic import BaseModel


class TokenResponse(BaseModel):
    appAccessToken: str
    expiresAt: str
    cached: bool

