from __future__ import annotations

from fastapi import APIRouter

from app.schemas.ptz import PtzMoveRequest, PtzMoveResponse
from app.services.dahua_ptz_service import ptz_service


router = APIRouter(prefix="/api/ptz", tags=["ptz"])


@router.post("/move", response_model=PtzMoveResponse)
def move_ptz(request: PtzMoveRequest) -> PtzMoveResponse:
    return ptz_service.move(
        device_id=request.deviceId,
        channel_id=request.channelId,
        action=request.action,
        step_profile=request.stepProfile,
        duration=request.duration,
    )
