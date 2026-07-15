from __future__ import annotations

import base64
import binascii
import json
import re
from pathlib import Path
from typing import Any

from app.config import settings
from app.schemas.calibration import CalibrationListItem, CalibrationListResponse, CalibrationRecord, CalibrationSaveRequest
from app.utils.time_utils import iso_utc_now


SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


class CalibrationStorageService:
    def save(self, payload: CalibrationSaveRequest) -> CalibrationRecord:
        snapshot_path = self._save_snapshot(payload.deviceId, payload.presetIndex, payload.snapshotBase64)
        record = CalibrationRecord(
            deviceId=payload.deviceId,
            channelId=payload.channelId,
            targetId=payload.targetId,
            targetName=payload.targetName,
            presetIndex=payload.presetIndex,
            presetName=payload.presetName,
            roi=payload.roi,
            focusAnchorRoi=payload.focusAnchorRoi,
            notes=payload.notes,
            snapshotPath=snapshot_path,
            snapshotUrl=self._artifact_url(snapshot_path),
            updatedAt=iso_utc_now(),
        )

        file_path = self._config_path(payload.deviceId, payload.presetIndex)
        file_path.write_text(json.dumps(record.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
        return record

    def get(self, device_id: str, preset_index: int) -> CalibrationRecord:
        file_path = self._config_path(device_id, preset_index)
        if not file_path.exists():
            raise FileNotFoundError(f"Calibration not found for {device_id}/{preset_index}")
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        return CalibrationRecord(**self._normalize_record_payload(payload))

    def load_path(self, path: Path) -> CalibrationRecord:
        if not path.exists():
            raise FileNotFoundError(f"Calibration file not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return CalibrationRecord(**self._normalize_record_payload(payload))

    def list(self) -> CalibrationListResponse:
        items: list[CalibrationListItem] = []
        for path in sorted(settings.calibration_dir.glob("*.json")):
            payload = self._normalize_record_payload(json.loads(path.read_text(encoding="utf-8")))
            items.append(
                CalibrationListItem(
                    deviceId=payload["deviceId"],
                    presetIndex=payload["presetIndex"],
                    targetName=payload["targetName"],
                    updatedAt=payload["updatedAt"],
                    path=self._display_path(path),
                )
            )
        return CalibrationListResponse(items=items)

    def _config_path(self, device_id: str, preset_index: int) -> Path:
        file_name = f"{self._safe_name(device_id)}_{self._safe_name(str(preset_index))}.json"
        return settings.calibration_dir / file_name

    def _save_snapshot(self, device_id: str, preset_index: int, snapshot_base64: str | None) -> str | None:
        if not snapshot_base64:
            return None

        raw_data = snapshot_base64
        if "," in snapshot_base64:
            raw_data = snapshot_base64.split(",", 1)[1]

        try:
            binary = base64.b64decode(raw_data)
        except (binascii.Error, ValueError) as error:
            raise ValueError(f"Invalid snapshotBase64 payload: {error}") from error

        timestamp = iso_utc_now().replace(":", "-")
        file_name = f"{self._safe_name(device_id)}_{self._safe_name(str(preset_index))}_{timestamp}.png"
        output_path = settings.snapshot_dir / file_name
        output_path.write_bytes(binary)
        return self._artifact_path(output_path)

    @staticmethod
    def _safe_name(value: str) -> str:
        return SAFE_NAME_PATTERN.sub("_", value.strip()) or "unknown"

    def _normalize_record_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        normalized.pop("stabilityRoi", None)
        normalized.pop("version", None)
        normalized.pop("streamPreference", None)
        normalized.pop("captureMode", None)
        normalized.pop("captureDurationMs", None)
        normalized.pop("ptzSettleMs", None)
        normalized.pop("ptzExtraSettleMs", None)
        normalized.pop("presetTurnSettleMs", None)
        normalized.pop("streamCatchupMs", None)
        normalized.pop("streamUnreadyDebounceMs", None)
        normalized.pop("visualStableWindowMs", None)
        normalized.pop("visualStableSampleMs", None)
        normalized.pop("visualStableThreshold", None)
        normalized.pop("visualStableGraceThreshold", None)
        normalized.pop("snapshotBase64", None)
        normalized.setdefault("notes", "")
        normalized.setdefault("focusAnchorRoi", None)
        if "presetIndex" not in normalized and "presetId" in normalized:
            try:
                normalized["presetIndex"] = int(normalized["presetId"])
            except (TypeError, ValueError):
                normalized["presetIndex"] = -1

        snapshot_path = self._normalize_artifact_path(normalized.get("snapshotPath"))
        if snapshot_path is not None:
            normalized["snapshotPath"] = snapshot_path
            normalized["snapshotUrl"] = self._artifact_url(snapshot_path)
        elif normalized.get("snapshotPath") is None:
            normalized["snapshotUrl"] = None

        return normalized

    @staticmethod
    def _artifact_url(snapshot_path: str | None) -> str | None:
        if not snapshot_path:
            return None
        return f"/artifacts/{snapshot_path.replace('\\', '/')}"

    @staticmethod
    def _display_path(path: Path) -> str:
        try:
            return path.relative_to(settings.data_root).as_posix()
        except ValueError:
            return str(path)

    @staticmethod
    def _artifact_path(path: Path) -> str:
        try:
            return path.relative_to(settings.data_root).as_posix()
        except ValueError:
            return path.name

    @staticmethod
    def _normalize_artifact_path(snapshot_path: Any) -> str | None:
        if not isinstance(snapshot_path, str):
            return None

        cleaned = snapshot_path.replace("\\", "/").strip().lstrip("/")
        if not cleaned:
            return None

        data_root_name = settings.data_root.name.strip("/\\")
        prefixes = ["snapshots/"]
        if data_root_name:
            prefixes.append(f"{data_root_name}/snapshots/")
        prefixes.append("data/snapshots/")

        for prefix in prefixes:
            if cleaned.startswith(prefix):
                suffix = cleaned[len(prefix) :].lstrip("/")
                return f"snapshots/{suffix}" if suffix else "snapshots"

        if cleaned.startswith("artifacts/"):
            suffix = cleaned[len("artifacts/") :].lstrip("/")
            return suffix or None

        return cleaned

storage_service = CalibrationStorageService()
