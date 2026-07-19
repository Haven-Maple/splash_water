from __future__ import annotations

from typing import Any

from app.schemas.preset import PresetItem, PresetOperationResponse, PresetQueryResponse
from app.utils.request_sign_adapter import client


class DahuaPresetService:
    query_endpoint = "/open-api/api-aiot/device/queryPreset"
    save_endpoint = "/open-api/api-aiot/device/configPreset"
    turn_endpoint = "/open-api/api-aiot/device/turnPreset"

    def query_presets(self, device_id: str, channel_id: str) -> PresetQueryResponse:
        payload = client.post_open_api(
            path=self.query_endpoint,
            body={"deviceId": device_id, "channelId": channel_id},
            local_endpoint="/api/preset/query",
        )
        data = payload.get("data", payload)
        presets = [self._normalize_preset(item) for item in self._coerce_to_list(data)]
        return PresetQueryResponse(deviceId=device_id, channelId=channel_id, presets=presets, raw=data)

    def save_preset(self, device_id: str, channel_id: str, preset_index: int, preset_name: str) -> PresetOperationResponse:
        payload = client.post_open_api(
            path=self.save_endpoint,
            body={
                "deviceId": device_id,
                "channelId": channel_id,
                "index": preset_index,
                "name": preset_name,
            },
            local_endpoint="/api/preset/save",
        )
        return PresetOperationResponse(accepted=True, presetIndex=preset_index, raw=payload.get("data", payload))

    def turn_preset(self, device_id: str, channel_id: str, preset_index: int) -> PresetOperationResponse:
        payload, diagnostics = client.post_open_api_control_with_retry(
            path=self.turn_endpoint,
            body={"deviceId": device_id, "channelId": channel_id, "index": preset_index},
            local_endpoint="/api/preset/turn",
        )
        return PresetOperationResponse(
            accepted=True,
            presetIndex=preset_index,
            raw=payload.get("data", payload),
            attemptCount=int(diagnostics["attemptCount"]),
            attempts=list(diagnostics["attempts"]),
            unknownStateRetrySucceeded=bool(diagnostics["unknownStateRetrySucceeded"]),
        )

    @staticmethod
    def _normalize_preset(item: Any) -> PresetItem:
        if isinstance(item, dict):
            preset_index = item.get("index")
            if preset_index is None:
                preset_index = item.get("presetId") or item.get("id") or item.get("code") or -1
            preset_name = item.get("presetName") or item.get("name")
            return PresetItem(presetIndex=int(preset_index), presetName=preset_name, raw=item)
        if isinstance(item, str):
            return PresetItem(presetIndex=-1, presetName=item, raw=item)
        return PresetItem(presetIndex=-1, presetName=None, raw=item)

    def _coerce_to_list(self, data: Any) -> list[Any]:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("list", "presets", "items", "records", "data"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return []


preset_service = DahuaPresetService()
