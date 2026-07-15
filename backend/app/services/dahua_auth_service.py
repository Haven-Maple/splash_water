from __future__ import annotations

from app.schemas.auth import TokenResponse
from app.utils.request_sign_adapter import client


class DahuaAuthService:
    def get_token(self) -> TokenResponse:
        return TokenResponse(**client.get_app_access_token())


auth_service = DahuaAuthService()

