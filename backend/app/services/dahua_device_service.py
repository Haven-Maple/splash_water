from __future__ import annotations

from typing import Any

from app.schemas.device import DeviceOnlineResponse
from app.utils.request_sign_adapter import client


class DahuaDeviceService:
    endpoint = "/open-api/api-iot/device/deviceOnline"

    def check_online(self, device_id: str) -> DeviceOnlineResponse:
        payload = client.post_open_api(
            path=self.endpoint,
            body={"deviceId": device_id},
            local_endpoint="/api/device/online",
        )
        data = payload.get("data", payload)
        online = self._extract_online(data)
        status = "online" if online else "offline"
        return DeviceOnlineResponse(deviceId=device_id, online=online, status=status, raw=data)

    @staticmethod
    def _extract_online(data: Any) -> bool:
        if isinstance(data, bool):
            return data
        if isinstance(data, (int, float)):
            return bool(data)
        if isinstance(data, str):
            return data.lower() in {"true", "1", "online", "yes"}
        if isinstance(data, dict):
            for key in ("online", "isOnline", "deviceOnline"):
                value = data.get(key)
                if value is not None:
                    return DahuaDeviceService._extract_online(value)
            for key in ("status", "deviceStatus", "onlineStatus"):
                value = data.get(key)
                if isinstance(value, str):
                    return value.lower() in {"online", "1", "true", "yes"}
                if isinstance(value, (int, float)):
                    return bool(value)
        return False


device_service = DahuaDeviceService()

