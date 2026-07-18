from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _read_local_config(workspace_root: Path) -> dict[str, object]:
    config_path = workspace_root / "backend" / "local_config.json"
    if not config_path.exists():
        return {}

    with config_path.open("r", encoding="utf-8") as fp:
        loaded = json.load(fp)
    return loaded if isinstance(loaded, dict) else {}


def _read_string_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, item in value.items():
        key_str = str(key).strip()
        item_str = str(item).strip()
        if key_str and item_str:
            normalized[key_str] = item_str
    return normalized


def _read_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_float(value: object, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class Settings:
    workspace_root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2])
    dahua_domain: str = ""
    dahua_access_key: str = ""
    dahua_secret_key: str = ""
    dahua_product_id: str = ""
    dahua_api_version: str = ""
    dahua_language: str = ""
    request_timeout_seconds: int = 20
    frontend_origins: list[str] = field(default_factory=list)
    data_root: Path | None = None
    ptz_operation_map: dict[str, str] = field(default_factory=dict)
    ptz_verified_map: dict[str, str] = field(default_factory=dict)
    local_config_path: Path = field(init=False)
    local_config: dict[str, object] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.local_config_path = self.workspace_root / "backend" / "local_config.json"
        self.local_config = _read_local_config(self.workspace_root)

        self.dahua_domain = str(
            os.getenv("DAHUA_DOMAIN")
            or self.local_config.get("domain_name")
            or "open.cloud-dahua.com"
        )
        self.dahua_access_key = str(
            os.getenv("DAHUA_ACCESS_KEY")
            or self.local_config.get("access_key")
            or ""
        )
        self.dahua_secret_key = str(
            os.getenv("DAHUA_SECRET_KEY")
            or self.local_config.get("secret_access_key")
            or ""
        )
        self.dahua_product_id = str(
            os.getenv("DAHUA_PRODUCT_ID")
            or self.local_config.get("product_id")
            or ""
        )
        self.dahua_api_version = str(
            os.getenv("DAHUA_API_VERSION")
            or self.local_config.get("api_version")
            or "v1"
        )
        self.dahua_language = str(
            os.getenv("DAHUA_ACCEPT_LANGUAGE")
            or self.local_config.get("accept_language")
            or "zh-CN"
        )

        timeout_value = (
            os.getenv("DAHUA_REQUEST_TIMEOUT_SECONDS")
            or self.local_config.get("request_timeout_seconds")
            or "20"
        )
        self.request_timeout_seconds = int(timeout_value)

        frontend_origins_value = (
            os.getenv("FRONTEND_ORIGINS")
            or self.local_config.get("frontend_origins")
            or "http://localhost:5173,http://127.0.0.1:5173"
        )
        if isinstance(frontend_origins_value, list):
            self.frontend_origins = [str(item).strip() for item in frontend_origins_value if str(item).strip()]
        else:
            self.frontend_origins = _split_csv(str(frontend_origins_value))

        data_root_value = os.getenv("CALIBRATION_DATA_ROOT") or self.local_config.get("data_root")
        if data_root_value:
            configured_root = Path(str(data_root_value))
            if not configured_root.is_absolute():
                configured_root = self.workspace_root / configured_root
            self.data_root = configured_root
        if self.data_root is None:
            self.data_root = self.workspace_root / "data"

        self.ptz_operation_map = _read_string_map(self.local_config.get("ptz_operation_map"))
        self.ptz_verified_map = _read_string_map(self.local_config.get("ptz_verified_map"))

        self.calibration_dir.mkdir(parents=True, exist_ok=True)
        self.calibration_history_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    @property
    def backend_root(self) -> Path:
        return self.workspace_root / "backend"

    @property
    def app_root(self) -> Path:
        return self.backend_root / "app"

    @property
    def log_dir(self) -> Path:
        return self.app_root / "logs"

    @property
    def calibration_dir(self) -> Path:
        return self.data_root / "calibrations"

    @property
    def snapshot_dir(self) -> Path:
        return self.data_root / "snapshots"

    @property
    def calibration_history_dir(self) -> Path:
        return self.data_root / "calibration_history"

    @property
    def request_sign_path(self) -> Path:
        return self.workspace_root / "API-SDK-SignPythonDemo" / "request_sign_utils.py"

    @property
    def calibration_tool_runtime_config(self) -> dict[str, int | float]:
        calibration_tool = self.local_config.get("calibration_tool")
        if not isinstance(calibration_tool, dict):
            calibration_tool = {}

        recognition_config = self.local_config.get("recognition_v1")
        if not isinstance(recognition_config, dict):
            recognition_config = {}

        threshold = max(0.0, _read_float(calibration_tool.get("visualStableThreshold"), 6.0))
        grace_threshold = max(
            threshold,
            _read_float(calibration_tool.get("visualStableGraceThreshold"), 8.0),
        )

        return {
            "ptzExtraSettleMs": max(0, _read_int(calibration_tool.get("ptzExtraSettleMs"), 800)),
            "presetTurnSettleMs": max(0, _read_int(recognition_config.get("presetTurnSettleMs"), 1800)),
            "streamCatchupMs": max(0, _read_int(recognition_config.get("streamCatchupMs"), 1000)),
            "streamUnreadyDebounceMs": max(0, _read_int(calibration_tool.get("streamUnreadyDebounceMs"), 800)),
            "visualStableWindowMs": max(200, _read_int(calibration_tool.get("visualStableWindowMs"), 800)),
            "visualStableSampleMs": max(50, _read_int(calibration_tool.get("visualStableSampleMs"), 200)),
            "visualStableThreshold": threshold,
            "visualStableGraceThreshold": grace_threshold,
        }

    @property
    def is_dahua_configured(self) -> bool:
        return all(
            [
                self.dahua_domain,
                self.dahua_access_key,
                self.dahua_secret_key,
                self.dahua_product_id,
                self.dahua_api_version,
            ]
        )


settings = Settings()
