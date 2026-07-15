from __future__ import annotations

import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.config import settings
from app.utils.time_utils import iso_utc_now


LOGGER_NAME = "calibration_tool"
DEFAULT_LOG_ROLE = "default"
LOG_ROLE_FILENAMES = {
    "backend": "api-backend.log",
    "inspector": "api-inspector.log",
    DEFAULT_LOG_ROLE: "api-default.log",
}
REPLAY_WORKER_LOG_PREFIX = "api-replay-worker"
LEGACY_LOG_FILENAMES = ("api.log",)

_current_log_role = DEFAULT_LOG_ROLE


class ConsoleSceneModeFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        message = record.getMessage()
        return (
            "requestedSceneMode=" in message
            or "effectiveSceneMode=" in message
            or "sceneMode=" in message
            or "Temporal vote resolved" in message
        )


class LogRoleFilter(logging.Filter):
    def __init__(self, log_role: str) -> None:
        super().__init__()
        self.log_role = log_role

    def filter(self, record: logging.LogRecord) -> bool:
        record.logRole = self.log_role
        return True


def normalize_log_role(log_role: str | None) -> str:
    if not log_role:
        return DEFAULT_LOG_ROLE
    normalized = str(log_role).strip().lower()
    return normalized or DEFAULT_LOG_ROLE


def _build_replay_worker_log_filename(process_id: int | None = None) -> str:
    resolved_process_id = process_id or os.getpid()
    return f"{REPLAY_WORKER_LOG_PREFIX}-p{resolved_process_id}.log"


def get_log_filename(log_role: str | None, *, process_id: int | None = None) -> str:
    normalized = normalize_log_role(log_role)
    if normalized == "replay-worker":
        return _build_replay_worker_log_filename(process_id)
    return LOG_ROLE_FILENAMES.get(normalized, LOG_ROLE_FILENAMES[DEFAULT_LOG_ROLE])


def get_log_path(log_role: str | None, *, process_id: int | None = None) -> Path:
    return settings.log_dir / get_log_filename(log_role, process_id=process_id)


def get_available_log_filenames() -> list[str]:
    candidates = {filename for filename in LOG_ROLE_FILENAMES.values()}
    candidates.update(LEGACY_LOG_FILENAMES)
    existing = {
        path.name
        for path in settings.log_dir.glob("api*.log")
        if path.is_file()
    }
    candidates.update(existing)
    return sorted(candidates)


def _resolve_recent_log_path(log_file: str | None) -> Path:
    if log_file:
        candidate = Path(log_file)
        if candidate.name != log_file:
            raise ValueError("log file must be a plain file name")
        return settings.log_dir / candidate.name

    for filename in (
        LOG_ROLE_FILENAMES["backend"],
        LOG_ROLE_FILENAMES[DEFAULT_LOG_ROLE],
        *LEGACY_LOG_FILENAMES,
    ):
        path = settings.log_dir / filename
        if path.exists():
            return path
    return settings.log_dir / LOG_ROLE_FILENAMES["backend"]


def _build_formatter() -> logging.Formatter:
    return logging.Formatter(
        "%(asctime)s %(levelname)s [pid=%(process)d process=%(processName)s role=%(logRole)s] %(message)s"
    )


def setup_logging(log_role: str | None = None, *, force: bool = False) -> logging.Logger:
    global _current_log_role

    normalized_role = normalize_log_role(log_role)
    logger_instance = logging.getLogger(LOGGER_NAME)
    if logger_instance.handlers and not force and _current_log_role == normalized_role:
        return logger_instance

    if logger_instance.handlers:
        for handler in list(logger_instance.handlers):
            logger_instance.removeHandler(handler)
            handler.close()

    logger_instance.setLevel(logging.INFO)
    formatter = _build_formatter()
    log_role_filter = LogRoleFilter(normalized_role)

    file_handler = RotatingFileHandler(
        get_log_path(normalized_role),
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(log_role_filter)
    logger_instance.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(log_role_filter)
    console_handler.addFilter(ConsoleSceneModeFilter())
    logger_instance.addHandler(console_handler)

    logger_instance.propagate = False
    _current_log_role = normalized_role
    return logger_instance


def get_current_log_role() -> str:
    return _current_log_role


logger = setup_logging()


SENSITIVE_KEYS = {
    "appAccessToken",
    "AppAccessToken",
    "accessToken",
    "token",
    "secret",
    "secretKey",
    "dahuaSecretKey",
}


def mask_value(value: str) -> str:
    if len(value) <= 10:
        return "***"
    return f"{value[:4]}***{value[-4:]}"


def mask_sensitive(data: Any) -> Any:
    if isinstance(data, dict):
        masked: dict[str, Any] = {}
        for key, value in data.items():
            if key in SENSITIVE_KEYS and isinstance(value, str):
                masked[key] = mask_value(value)
            else:
                masked[key] = mask_sensitive(value)
        return masked
    if isinstance(data, list):
        return [mask_sensitive(item) for item in data]
    return data


def append_json_line(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")


def log_vendor_call(
    *,
    local_endpoint: str,
    vendor_endpoint: str,
    request_summary: dict[str, Any],
    response_status: int | None,
    response_payload: Any,
    success: bool,
    trace_id: str,
    error: str | None = None,
) -> None:
    payload = {
        "timestamp": iso_utc_now(),
        "localEndpoint": local_endpoint,
        "vendorEndpoint": vendor_endpoint,
        "traceId": trace_id,
        "success": success,
        "responseStatus": response_status,
        "requestSummary": mask_sensitive(request_summary),
        "responsePayload": mask_sensitive(response_payload),
        "error": error,
    }
    logger.info(json.dumps(payload, ensure_ascii=False))


def read_recent_vendor_logs(limit: int = 20, log_file: str | None = None) -> list[dict[str, Any]]:
    log_path = _resolve_recent_log_path(log_file)
    if not log_path.exists():
        return []

    entries: list[dict[str, Any]] = []
    for line in reversed(log_path.read_text(encoding="utf-8").splitlines()):
        _, _, json_part = line.partition(" INFO ")
        if not json_part:
            continue
        json_part = json_part.rsplit("] ", 1)[-1]
        try:
            entries.append(json.loads(json_part))
        except json.JSONDecodeError:
            continue
        if len(entries) >= limit:
            break
    return entries
