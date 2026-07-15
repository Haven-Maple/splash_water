from __future__ import annotations

from fastapi import APIRouter

from app.schemas.preset import (
    PresetOperationResponse,
    PresetQueryRequest,
    PresetQueryResponse,
    PresetSaveRequest,
    PresetTurnRequest,
)
from app.services.dahua_preset_service import preset_service


router = APIRouter(prefix="/api/preset", tags=["preset"])


@router.post("/query", response_model=PresetQueryResponse)
def query_presets(request: PresetQueryRequest) -> PresetQueryResponse:
    return preset_service.query_presets(request.deviceId, request.channelId)


@router.post("/save", response_model=PresetOperationResponse)
def save_preset(request: PresetSaveRequest) -> PresetOperationResponse:
    return preset_service.save_preset(request.deviceId, request.channelId, request.presetIndex, request.presetName)


@router.post("/turn", response_model=PresetOperationResponse)
def turn_preset(request: PresetTurnRequest) -> PresetOperationResponse:
    return preset_service.turn_preset(request.deviceId, request.channelId, request.presetIndex)
