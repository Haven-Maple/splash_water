from __future__ import annotations

from fastapi import APIRouter

from app.schemas.device import DeviceOnlineRequest, DeviceOnlineResponse
from app.services.dahua_device_service import device_service


router = APIRouter(prefix="/api/device", tags=["device"])


@router.post("/online", response_model=DeviceOnlineResponse)
def check_online(request: DeviceOnlineRequest) -> DeviceOnlineResponse:
    return device_service.check_online(request.deviceId)

