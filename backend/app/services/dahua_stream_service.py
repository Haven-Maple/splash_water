from __future__ import annotations

from typing import Any

from app.schemas.stream import StreamResponse
from app.utils.request_sign_adapter import DahuaApiError, client


STREAM_URL_KEYS = (
    "streamUrl",
    "url",
    "liveUrl",
    "playUrl",
    "flv",
    "flvUrl",
    "hls",
    "hlsUrl",
    "httpsUrl",
)


class DahuaStreamService:
    flv_endpoint = "/open-api/api-iot/device/queryDeviceFlvLive"
    hls_endpoint = "/open-api/api-iot/device/getHlsLiveList"

    def get_flv_stream(
        self,
        device_id: str,
        channel_id: str,
        *,
        timeout: float | tuple[float, float] | None = None,
        deadline: float | None = None,
    ) -> StreamResponse:
        payload = client.post_open_api(
            path=self.flv_endpoint,
            body={"deviceId": device_id, "channelId": channel_id},
            local_endpoint="/api/stream/flv",
            timeout=timeout,
            deadline=deadline,
        )
        url = self._extract_stream_url(payload.get("data", payload), preferred="flv")
        return StreamResponse(streamType="flv", streamUrl=url, fallbackAvailable=True, raw=payload.get("data", payload))

    def get_hls_stream(self, device_id: str, channel_id: str) -> StreamResponse:
        payload = client.post_open_api(
            path=self.hls_endpoint,
            body={"deviceId": device_id, "channelId": channel_id},
            local_endpoint="/api/stream/hls",
        )
        url = self._extract_stream_url(payload.get("data", payload), preferred="hls")
        return StreamResponse(streamType="hls", streamUrl=url, fallbackAvailable=False, raw=payload.get("data", payload))

    def get_preferred_stream(self, device_id: str, channel_id: str, prefer: str = "flv") -> StreamResponse:
        primary = self.get_flv_stream if prefer == "flv" else self.get_hls_stream
        fallback = self.get_hls_stream if prefer == "flv" else self.get_flv_stream
        try:
            stream = primary(device_id, channel_id)
            stream.fallbackAvailable = True
            return stream
        except DahuaApiError:
            return fallback(device_id, channel_id)

    def _extract_stream_url(self, payload: Any, *, preferred: str) -> str:
        urls = list(self._walk_urls(payload))
        if not urls:
            raise DahuaApiError(f"No {preferred} stream URL found", payload=payload)

        preferred_urls = [url for url in urls if preferred in url.lower()]
        return preferred_urls[0] if preferred_urls else urls[0]

    def _walk_urls(self, payload: Any) -> list[str]:
        urls: list[str] = []
        self._collect_urls(payload, urls)
        return urls

    def _collect_urls(self, payload: Any, urls: list[str]) -> None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key in STREAM_URL_KEYS and isinstance(value, str) and value.startswith(("http://", "https://")):
                    urls.append(value)
                else:
                    self._collect_urls(value, urls)
        elif isinstance(payload, list):
            for item in payload:
                self._collect_urls(item, urls)
        elif isinstance(payload, str) and payload.startswith(("http://", "https://")):
            urls.append(payload)


stream_service = DahuaStreamService()
