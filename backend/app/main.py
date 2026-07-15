from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.utils.logging_utils import setup_logging
from app.routers.auth_router import router as auth_router
from app.routers.calibration_router import router as calibration_router
from app.routers.debug_router import router as debug_router
from app.routers.device_router import router as device_router
from app.routers.preset_router import router as preset_router
from app.routers.ptz_router import router as ptz_router
from app.routers.stream_router import router as stream_router
from app.utils.request_sign_adapter import DahuaApiError


setup_logging("backend", force=True)


app = FastAPI(title="Calibration Tool API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(device_router)
app.include_router(stream_router)
app.include_router(ptz_router)
app.include_router(preset_router)
app.include_router(calibration_router)
app.include_router(debug_router)
app.mount("/artifacts", StaticFiles(directory=settings.data_root), name="artifacts")


@app.get("/api/health")
def healthcheck() -> dict[str, object]:
    return {
        "ok": True,
        "dahuaConfigured": settings.is_dahua_configured,
        "dataRoot": str(settings.data_root),
    }


@app.exception_handler(DahuaApiError)
async def dahua_api_error_handler(_, exc: DahuaApiError):
    return JSONResponse(
        status_code=exc.status_code or 502,
        content={
            "message": str(exc),
            "payload": exc.payload,
        },
    )
