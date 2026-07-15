from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.schemas.calibration import (
    CalibrationListResponse,
    CalibrationOperationResponse,
    CalibrationRecord,
    CalibrationSaveRequest,
    CalibrationToolRuntimeConfig,
)
from app.services.calibration_storage_service import storage_service


router = APIRouter(prefix="/api/calibration", tags=["calibration"])


@router.post("/save", response_model=CalibrationOperationResponse)
def save_calibration(request: CalibrationSaveRequest) -> CalibrationOperationResponse:
    record = storage_service.save(request)
    return CalibrationOperationResponse(saved=True, record=record)


@router.get("/get", response_model=CalibrationRecord)
def get_calibration(
    deviceId: str = Query(..., min_length=1),
    presetIndex: int = Query(..., ge=0),
) -> CalibrationRecord:
    try:
        return storage_service.get(deviceId, presetIndex)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/list", response_model=CalibrationListResponse)
def list_calibrations() -> CalibrationListResponse:
    return storage_service.list()


@router.get("/runtime-config", response_model=CalibrationToolRuntimeConfig)
def get_runtime_config() -> CalibrationToolRuntimeConfig:
    return CalibrationToolRuntimeConfig(**settings.calibration_tool_runtime_config)
