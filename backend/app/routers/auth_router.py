from __future__ import annotations

from fastapi import APIRouter

from app.schemas.auth import TokenResponse
from app.services.dahua_auth_service import auth_service


router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/token", response_model=TokenResponse)
def get_token() -> TokenResponse:
    return auth_service.get_token()

