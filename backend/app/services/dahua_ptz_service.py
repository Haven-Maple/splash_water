from __future__ import annotations

from typing import Any

from app.config import settings
from app.schemas.ptz import PtzMoveResponse
from app.utils.request_sign_adapter import DahuaApiError, client


STEP_PROFILES = {
    "small": {"duration": 150, "speed": 0.25},
    "medium": {"duration": 300, "speed": 0.45},
    "large": {"duration": 500, "speed": 0.7},
}

ACTION_PROFILE = {
    "up": {"operation": "1", "horizontal": 0.0, "vertical": 1.0},
    "down": {"operation": "2", "horizontal": 0.0, "vertical": -1.0},
    "left": {"operation": "3", "horizontal": -1.0, "vertical": 0.0},
    "right": {"operation": "4", "horizontal": 1.0, "vertical": 0.0},
    "upLeft": {"operation": "5", "horizontal": -1.0, "vertical": 1.0},
    "upRight": {"operation": "6", "horizontal": 1.0, "vertical": 1.0},
    "downLeft": {"operation": "7", "horizontal": -1.0, "vertical": -1.0},
    "downRight": {"operation": "8", "horizontal": 1.0, "vertical": -1.0},
    "zoomIn": {"operation": "8"},
    "zoomOut": {"operation": "9"},
}


class DahuaPtzService:
    endpoint = "/api-aiot/device/controlMovePTZ"

    def __init__(self) -> None:
        self._verified_map = dict(settings.ptz_verified_map)

    def move(
        self,
        *,
        device_id: str,
        channel_id: str,
        action: str,
        step_profile: str,
        duration: int | None = None,
    ) -> PtzMoveResponse:
        profile = STEP_PROFILES[step_profile]
        action_profile = ACTION_PROFILE.get(action)
        if action_profile is None:
            raise DahuaApiError(f"Unsupported PTZ action: {action}")

        operation_code = self._operation_code(action)
        command = {
            "deviceId": device_id,
            "channelId": channel_id,
            "operation": operation_code,
            "duration": duration or profile["duration"],
        }
        if "horizontal" in action_profile and "vertical" in action_profile:
            command["horizontalSpeed"] = self._speed_value(action_profile["horizontal"], profile["speed"])
            command["verticalSpeed"] = self._speed_value(action_profile["vertical"], profile["speed"])
        payload = client.post_open_api(
            path=self.endpoint,
            body=command,
            local_endpoint="/api/ptz/move",
        )
        verified_operation = self._verified_map.get(action)
        return PtzMoveResponse(
            accepted=True,
            operationVerified=verified_operation == operation_code,
            verifiedMap=dict(sorted(self._verified_map.items())),
            verifiedOperation=verified_operation,
            command=command,
            raw=payload.get("data", payload),
        )

    @staticmethod
    def _clamp(value: float) -> float:
        return max(-1.0, min(1.0, round(value, 4)))

    def _speed_value(self, direction: float, base_speed: float) -> float:
        if direction == 0:
            return 0.0
        return self._clamp(direction * base_speed)

    @staticmethod
    def _operation_code(action: str) -> str:
        configured_code = settings.ptz_operation_map.get(action)
        if configured_code:
            return configured_code

        fallback = ACTION_PROFILE.get(action, {}).get("operation")
        if fallback:
            return str(fallback)

        raise DahuaApiError(
            f"PTZ operation code not configured for action '{action}'. Set backend/local_config.json -> ptz_operation_map."
        )


ptz_service = DahuaPtzService()
