from __future__ import annotations

from fastapi import APIRouter

from app.schemas.stream import PreferredStreamRequest, StreamRequest, StreamResponse
from app.services.dahua_stream_service import stream_service


router = APIRouter(prefix="/api/stream", tags=["stream"])


@router.post("/flv", response_model=StreamResponse)
def get_flv_stream(request: StreamRequest) -> StreamResponse:
    return stream_service.get_flv_stream(request.deviceId, request.channelId)


@router.post("/hls", response_model=StreamResponse)
def get_hls_stream(request: StreamRequest) -> StreamResponse:
    return stream_service.get_hls_stream(request.deviceId, request.channelId)


@router.post("/preferred", response_model=StreamResponse)
def get_preferred_stream(request: PreferredStreamRequest) -> StreamResponse:
    return stream_service.get_preferred_stream(request.deviceId, request.channelId, request.prefer)

