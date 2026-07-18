from __future__ import annotations

import base64
import binascii
import io
import json
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from app.config import settings
from app.schemas.calibration import (
    CalibrationHistoryItem,
    CalibrationHistoryResponse,
    CalibrationListItem,
    CalibrationListResponse,
    CalibrationRecord,
    CalibrationSaveRequest,
)
from app.utils.time_utils import iso_utc_now


SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")
VERSION_PATTERN = re.compile(r"^v(\d{4,})$")
DEPLOYMENT_FIELD_NAMES = (
    "deviceId",
    "channelId",
    "targetId",
    "targetName",
    "presetIndex",
    "presetName",
    "roi",
    "focusAnchorRoi",
    # Current recognition loaders still require these compatibility fields.
    "notes",
    "updatedAt",
)


class CalibrationStorageService:
    """Keeps an inspector-compatible current config beside immutable calibration history."""

    def save(self, payload: CalibrationSaveRequest) -> CalibrationRecord:
        history_dir = self._history_dir(payload.deviceId, payload.presetIndex)
        self._archive_legacy_current_if_needed(payload.deviceId, payload.presetIndex, history_dir)

        version = self._next_version(history_dir)
        version_dir = history_dir / self._version_name(version)
        version_dir.mkdir(parents=True, exist_ok=False)
        original_bytes = self._decode_snapshot(payload.snapshotOriginalBase64 or payload.snapshotBase64)
        annotated_bytes = self._decode_snapshot(payload.snapshotAnnotatedBase64)
        if original_bytes is None or annotated_bytes is None:
            raise ValueError("Calibration save requires both original and annotated snapshots.")
        original_path = self._write_snapshot(version_dir, "snapshot-original.png", original_bytes)
        annotated_path = self._write_snapshot(version_dir, "snapshot-annotated.png", annotated_bytes)
        updated_at = iso_utc_now()
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
            snapshotPath=original_path,
            snapshotUrl=self._artifact_url(original_path),
            snapshotOriginalPath=original_path,
            snapshotOriginalUrl=self._artifact_url(original_path),
            snapshotAnnotatedPath=annotated_path,
            snapshotAnnotatedUrl=self._artifact_url(annotated_path),
            version=version,
            updatedAt=updated_at,
        )
        self._write_record(version_dir / "calibration.json", record)
        self._write_record(self._config_path(payload.deviceId, payload.presetIndex), record)
        return record

    def get(self, device_id: str, preset_index: int) -> CalibrationRecord:
        return self.load_path(self._config_path(device_id, preset_index))

    def load_path(self, path: Path) -> CalibrationRecord:
        if not path.exists():
            raise FileNotFoundError(f"Calibration file not found: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        return CalibrationRecord(**self._normalize_record_payload(payload))

    def list(self) -> CalibrationListResponse:
        items: list[CalibrationListItem] = []
        for path in sorted(settings.calibration_dir.glob("*.json")):
            record = self.load_path(path)
            items.append(
                CalibrationListItem(
                    deviceId=record.deviceId,
                    presetIndex=record.presetIndex,
                    targetName=record.targetName,
                    updatedAt=record.updatedAt,
                    path=self._display_path(path),
                    version=record.version,
                    legacy=record.legacy,
                )
            )
        return CalibrationListResponse(items=items)

    def history(self, device_id: str, preset_index: int) -> CalibrationHistoryResponse:
        history_dir = self._history_dir(device_id, preset_index)
        items: list[CalibrationHistoryItem] = []
        if not history_dir.exists():
            return CalibrationHistoryResponse(items=items)
        for version_dir in sorted(history_dir.iterdir(), key=lambda path: self._parse_version(path.name)):
            record_path = version_dir / "calibration.json"
            if not record_path.exists():
                continue
            record = self.load_path(record_path)
            if record.version is None:
                continue
            items.append(
                CalibrationHistoryItem(
                    version=record.version,
                    updatedAt=record.updatedAt,
                    targetName=record.targetName,
                    legacy=record.legacy,
                    restoredFromVersion=record.restoredFromVersion,
                    snapshotOriginalUrl=record.snapshotOriginalUrl,
                    snapshotAnnotatedUrl=record.snapshotAnnotatedUrl,
                )
            )
        return CalibrationHistoryResponse(items=items)

    def restore(self, device_id: str, preset_index: int, version: int) -> CalibrationRecord:
        source_path = self._history_dir(device_id, preset_index) / self._version_name(version) / "calibration.json"
        source = self.load_path(source_path)
        if source.focusAnchorRoi is None:
            raise ValueError("Legacy calibration has no focusAnchorRoi and must be updated before it can be restored.")
        original_snapshot = self._snapshot_as_data_url(source.snapshotOriginalPath or source.snapshotPath)
        annotated_snapshot = self._snapshot_as_data_url(source.snapshotAnnotatedPath)
        if original_snapshot is None:
            raise ValueError("Cannot restore calibration without an original snapshot.")
        if annotated_snapshot is None:
            raise ValueError("Cannot restore calibration without an annotated snapshot.")
        payload = CalibrationSaveRequest(
            deviceId=source.deviceId,
            channelId=source.channelId,
            targetId=source.targetId,
            targetName=source.targetName,
            presetIndex=source.presetIndex,
            presetName=source.presetName,
            roi=source.roi,
            focusAnchorRoi=source.focusAnchorRoi,
            notes=source.notes,
            snapshotOriginalBase64=original_snapshot,
            snapshotAnnotatedBase64=annotated_snapshot,
        )
        restored = self.save(payload)
        restored.restoredFromVersion = version
        version_path = self._history_dir(device_id, preset_index) / self._version_name(restored.version or 0)
        self._write_record(version_path / "calibration.json", restored)
        self._write_record(self._config_path(device_id, preset_index), restored)
        return restored

    def current_deployment_bytes(self, device_id: str, preset_index: int) -> bytes:
        record = self.get(device_id, preset_index)
        return self._deployment_json(record)

    def build_all_current_archive(self) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(settings.calibration_dir.glob("*.json")):
                record = self.load_path(path)
                archive.writestr(f"calibrations/{path.name}", self._deployment_json(record))
        return output.getvalue()

    def build_history_archive(self) -> bytes:
        files: dict[str, Path] = {}
        for path in sorted(settings.calibration_history_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(settings.data_root).as_posix()
            files[relative] = path
        for path in sorted(settings.calibration_dir.glob("*.json")):
            files[f"current/{path.name}"] = path
            record = self.load_path(path)
            for artifact_path in (record.snapshotOriginalPath, record.snapshotAnnotatedPath, record.snapshotPath):
                source = self._resolve_artifact_path(artifact_path)
                if source is not None and source.exists():
                    files[source.relative_to(settings.data_root).as_posix()] = source
        manifest = [{"path": archive_name} for archive_name in sorted(files)]
        content = json.dumps({"files": manifest}, ensure_ascii=False, indent=2).encode("utf-8")
        return self._build_zip(((path, archive_name) for archive_name, path in files.items()), extra_files=(("manifest.json", content),))

    def _archive_legacy_current_if_needed(self, device_id: str, preset_index: int, history_dir: Path) -> None:
        current_path = self._config_path(device_id, preset_index)
        if not current_path.exists() or any(history_dir.glob("v*/calibration.json")):
            return
        legacy = self.load_path(current_path)
        legacy.version = 1
        legacy.legacy = True
        version_dir = history_dir / self._version_name(1)
        version_dir.mkdir(parents=True, exist_ok=False)
        original_source = self._resolve_artifact_path(legacy.snapshotOriginalPath or legacy.snapshotPath)
        if original_source and original_source.exists():
            copied = version_dir / "snapshot-original.png"
            shutil.copyfile(original_source, copied)
            legacy.snapshotPath = self._artifact_path(copied)
            legacy.snapshotUrl = self._artifact_url(legacy.snapshotPath)
            legacy.snapshotOriginalPath = legacy.snapshotPath
            legacy.snapshotOriginalUrl = legacy.snapshotUrl
        self._write_record(version_dir / "calibration.json", legacy)

    def _config_path(self, device_id: str, preset_index: int) -> Path:
        return settings.calibration_dir / f"{self._target_key(device_id, preset_index)}.json"

    def _history_dir(self, device_id: str, preset_index: int) -> Path:
        return settings.calibration_history_dir / self._target_key(device_id, preset_index)

    def _target_key(self, device_id: str, preset_index: int) -> str:
        return f"{self._safe_name(device_id)}_{self._safe_name(str(preset_index))}"

    def _next_version(self, history_dir: Path) -> int:
        versions = [self._parse_version(path.name) for path in history_dir.glob("v*") if path.is_dir()]
        return max(versions, default=0) + 1

    @staticmethod
    def _version_name(version: int) -> str:
        return f"v{version:04d}"

    @staticmethod
    def _parse_version(name: str) -> int:
        matched = VERSION_PATTERN.match(name)
        return int(matched.group(1)) if matched else -1

    def _write_snapshot(self, directory: Path, filename: str, content: bytes | None) -> str | None:
        if content is None:
            return None
        path = directory / filename
        path.write_bytes(content)
        return self._artifact_path(path)

    @staticmethod
    def _decode_snapshot(value: str | None) -> bytes | None:
        if not value:
            return None
        raw_data = value.split(",", 1)[1] if "," in value else value
        try:
            return base64.b64decode(raw_data)
        except (binascii.Error, ValueError) as error:
            raise ValueError(f"Invalid snapshot payload: {error}") from error

    def _snapshot_as_data_url(self, artifact_path: str | None) -> str | None:
        source = self._resolve_artifact_path(artifact_path)
        if source is None or not source.exists():
            return None
        return f"data:image/png;base64,{base64.b64encode(source.read_bytes()).decode('ascii')}"

    def _resolve_artifact_path(self, artifact_path: str | None) -> Path | None:
        if not artifact_path:
            return None
        relative = PurePosixPath(artifact_path.replace("\\", "/").strip().lstrip("/"))
        if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
            return None
        candidate = settings.data_root.joinpath(*relative.parts)
        try:
            candidate.resolve().relative_to(settings.data_root.resolve())
        except ValueError:
            return None
        return candidate

    def _write_record(self, path: Path, record: CalibrationRecord) -> None:
        path.write_text(json.dumps(record.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _deployment_json(record: CalibrationRecord) -> bytes:
        payload = record.model_dump(include=set(DEPLOYMENT_FIELD_NAMES))
        return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

    @staticmethod
    def _safe_name(value: str) -> str:
        return SAFE_NAME_PATTERN.sub("_", value.strip()) or "unknown"

    def _normalize_record_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        for obsolete_key in (
            "stabilityRoi", "streamPreference", "captureMode", "captureDurationMs", "ptzSettleMs", "ptzExtraSettleMs",
            "presetTurnSettleMs", "streamCatchupMs", "streamUnreadyDebounceMs", "visualStableWindowMs",
            "visualStableSampleMs", "visualStableThreshold", "visualStableGraceThreshold", "snapshotBase64",
        ):
            normalized.pop(obsolete_key, None)
        normalized.setdefault("notes", "")
        normalized.setdefault("focusAnchorRoi", None)
        normalized.setdefault("legacy", normalized.get("version") is None)
        normalized.setdefault("restoredFromVersion", None)
        if "presetIndex" not in normalized and "presetId" in normalized:
            try:
                normalized["presetIndex"] = int(normalized["presetId"])
            except (TypeError, ValueError):
                normalized["presetIndex"] = -1

        original_path = self._normalize_artifact_path(normalized.get("snapshotOriginalPath") or normalized.get("snapshotPath"))
        annotated_path = self._normalize_artifact_path(normalized.get("snapshotAnnotatedPath"))
        normalized["snapshotPath"] = original_path
        normalized["snapshotUrl"] = self._artifact_url(original_path)
        normalized["snapshotOriginalPath"] = original_path
        normalized["snapshotOriginalUrl"] = self._artifact_url(original_path)
        normalized["snapshotAnnotatedPath"] = annotated_path
        normalized["snapshotAnnotatedUrl"] = self._artifact_url(annotated_path)
        return normalized

    def _build_zip(self, files: Any, extra_files: tuple[tuple[str, bytes], ...] = ()) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source, archive_name in files:
                archive.write(source, archive_name)
            for archive_name, content in extra_files:
                archive.writestr(archive_name, content)
        return output.getvalue()

    @staticmethod
    def _artifact_url(snapshot_path: str | None) -> str | None:
        return f"/artifacts/{snapshot_path.replace('\\', '/')}" if snapshot_path else None

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
        for prefix in ("snapshots/", "calibration_history/", "data/snapshots/", "data/calibration_history/"):
            if cleaned.startswith(prefix):
                suffix = cleaned[len(prefix):].lstrip("/")
                return f"{prefix.removeprefix('data/')}{suffix}" if suffix else None
        return cleaned


storage_service = CalibrationStorageService()
