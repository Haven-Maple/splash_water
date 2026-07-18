from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.config import settings
from app.schemas.calibration import (
    CalibrationListResponse,
    CalibrationOperationResponse,
    CalibrationRecord,
    CalibrationHistoryResponse,
    CalibrationRestoreRequest,
    CalibrationSaveRequest,
    CalibrationToolRuntimeConfig,
)
from app.services.calibration_storage_service import storage_service


router = APIRouter(prefix="/api/calibration", tags=["calibration"])


@router.post("/save", response_model=CalibrationOperationResponse)
def save_calibration(request: CalibrationSaveRequest) -> CalibrationOperationResponse:
    try:
        record = storage_service.save(request)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
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


@router.get("/history", response_model=CalibrationHistoryResponse)
def get_calibration_history(
    deviceId: str = Query(..., min_length=1),
    presetIndex: int = Query(..., ge=0),
) -> CalibrationHistoryResponse:
    return storage_service.history(deviceId, presetIndex)


@router.post("/restore", response_model=CalibrationOperationResponse)
def restore_calibration(request: CalibrationRestoreRequest) -> CalibrationOperationResponse:
    try:
        return CalibrationOperationResponse(saved=True, record=storage_service.restore(request.deviceId, request.presetIndex, request.version))
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/export/current")
def export_current_calibration(
    deviceId: str = Query(..., min_length=1),
    presetIndex: int = Query(..., ge=0),
) -> Response:
    try:
        content = storage_service.current_deployment_bytes(deviceId, presetIndex)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return Response(
        content,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=calibration-{deviceId}-{presetIndex}.json"},
    )


@router.get("/export/all")
def export_all_current_calibrations() -> Response:
    return Response(
        storage_service.build_all_current_archive(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=calibration-current-configs.zip"},
    )


@router.get("/export/archive")
def export_calibration_archive() -> Response:
    return Response(
        storage_service.build_history_archive(),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=calibration-archive.zip"},
    )


@router.get("/runtime-config", response_model=CalibrationToolRuntimeConfig)
def get_runtime_config() -> CalibrationToolRuntimeConfig:
    return CalibrationToolRuntimeConfig(**settings.calibration_tool_runtime_config)
