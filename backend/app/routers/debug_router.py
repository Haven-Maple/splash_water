from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.utils.logging_utils import get_available_log_filenames, read_recent_vendor_logs


router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.get("/recent-logs")
def get_recent_logs(
    limit: int = Query(default=20, ge=1, le=100),
    file: str | None = Query(default=None),
) -> dict[str, Any]:
    try:
        items = read_recent_vendor_logs(limit, log_file=file)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {
        "file": file,
        "availableFiles": get_available_log_filenames(),
        "items": items,
    }
