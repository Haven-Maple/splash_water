from __future__ import annotations

import importlib.util
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from app.config import settings
from app.utils.logging_utils import log_vendor_call
from app.utils.time_utils import utc_after, utc_now


def _load_request_sign_utils() -> Any:
    sign_module_path = settings.request_sign_path
    if not sign_module_path.exists():
        raise FileNotFoundError(f"request_sign_utils.py not found: {sign_module_path}")

    spec = importlib.util.spec_from_file_location("dahua_request_sign_utils", sign_module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load sign utility from {sign_module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.RequestSignUtils


RequestSignUtils = _load_request_sign_utils()


class DahuaApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


@dataclass(slots=True)
class TokenCache:
    token: str
    expires_at: datetime

    @property
    def is_valid(self) -> bool:
        return utc_now() < self.expires_at


class DahuaOpenApiClient:
    def __init__(self) -> None:
        self._sign_utils = RequestSignUtils()
        self._session = requests.Session()
        self._token_cache: TokenCache | None = None

    def ensure_configured(self) -> None:
        if settings.is_dahua_configured:
            return
        raise DahuaApiError(
            "Dahua credentials are not configured. Set DAHUA_ACCESS_KEY, DAHUA_SECRET_KEY, DAHUA_PRODUCT_ID, and DAHUA_DOMAIN."
        )

    def get_app_access_token(self, *, force_refresh: bool = False) -> dict[str, Any]:
        self.ensure_configured()
        if not force_refresh and self._token_cache and self._token_cache.is_valid:
            return {
                "appAccessToken": self._token_cache.token,
                "expiresAt": self._token_cache.expires_at.isoformat(),
                "cached": True,
            }

        timestamp = self._timestamp()
        nonce = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())
        sign = self._sign_utils.open_token_sign(
            {
                "access_key": settings.dahua_access_key,
                "timestamp": timestamp,
                "nonce": nonce,
                "method": "POST",
                "secret_access_key": settings.dahua_secret_key,
            }
        )
        headers = {
            "Content-Type": "application/json",
            "Accept-Language": settings.dahua_language,
            "Version": settings.dahua_api_version,
            "Timestamp": timestamp,
            "Nonce": nonce,
            "AccessKey": settings.dahua_access_key,
            "ProductId": settings.dahua_product_id,
            "X-TraceId-Header": trace_id,
            "Sign": sign,
        }

        vendor_path = "/open-api/api-base/auth/getAppAccessToken"
        response = self._post_json(vendor_path, headers=headers, body={}, local_endpoint="/api/auth/token")
        token = self._extract_required(response, ("data", "appAccessToken"))
        expires_at = self._parse_expiry(response.get("data", {}))
        self._token_cache = TokenCache(token=token, expires_at=expires_at)
        return {
            "appAccessToken": token,
            "expiresAt": expires_at.isoformat(),
            "cached": False,
        }

    def post_open_api(
        self,
        *,
        path: str,
        body: dict[str, Any] | list[Any] | None,
        local_endpoint: str,
        extra_headers: dict[str, str] | None = None,
        retry_on_401: bool = True,
    ) -> dict[str, Any]:
        self.ensure_configured()
        token_info = self.get_app_access_token()
        timestamp = self._timestamp()
        nonce = str(uuid.uuid4())
        trace_id = str(uuid.uuid4())
        request_body = body or {}
        headers = {
            "Content-Type": "application/json",
            "Accept-Language": settings.dahua_language,
            "Version": settings.dahua_api_version,
            "Timestamp": timestamp,
            "Nonce": nonce,
            "AccessKey": settings.dahua_access_key,
            "AppAccessToken": token_info["appAccessToken"],
            "ProductId": settings.dahua_product_id,
            "X-TraceId-Header": trace_id,
        }
        if extra_headers:
            headers.update(extra_headers)

        body_json = json.dumps(request_body, ensure_ascii=False)
        sign = self._sign_utils.open_sign(
            {
                "access_key": settings.dahua_access_key,
                "app_access_token": token_info["appAccessToken"],
                "timestamp": timestamp,
                "nonce": nonce,
                "method": "POST",
                "body": body_json,
                "secret_access_key": settings.dahua_secret_key,
                "headers": {key.lower(): value for key, value in headers.items()},
            }
        )
        headers["Sign"] = sign

        try:
            return self._post_json(path, headers=headers, body=request_body, local_endpoint=local_endpoint)
        except DahuaApiError as error:
            vendor_code = None
            if isinstance(error.payload, dict):
                vendor_code = str(error.payload.get("code", ""))
            if retry_on_401 and (error.status_code == 401 or vendor_code == "401"):
                self.get_app_access_token(force_refresh=True)
                return self.post_open_api(
                    path=path,
                    body=request_body,
                    local_endpoint=local_endpoint,
                    extra_headers=extra_headers,
                    retry_on_401=False,
                )
            raise

    def _post_json(
        self,
        path: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any] | list[Any],
        local_endpoint: str,
    ) -> dict[str, Any]:
        url = self._build_url(path)
        response: requests.Response | None = None
        try:
            response = self._session.post(
                url,
                headers=headers,
                json=body,
                timeout=settings.request_timeout_seconds,
            )
            payload = response.json()
        except requests.RequestException as error:
            log_vendor_call(
                local_endpoint=local_endpoint,
                vendor_endpoint=url,
                request_summary=body if isinstance(body, dict) else {"payload": body},
                response_status=getattr(response, "status_code", None),
                response_payload={"text": getattr(response, "text", None)},
                success=False,
                trace_id=headers.get("X-TraceId-Header", ""),
                error=str(error),
            )
            raise DahuaApiError(f"Request to Dahua API failed: {error}", status_code=getattr(response, "status_code", None))
        except ValueError as error:
            log_vendor_call(
                local_endpoint=local_endpoint,
                vendor_endpoint=url,
                request_summary=body if isinstance(body, dict) else {"payload": body},
                response_status=getattr(response, "status_code", None),
                response_payload={"text": getattr(response, "text", None)},
                success=False,
                trace_id=headers.get("X-TraceId-Header", ""),
                error=f"Invalid JSON response: {error}",
            )
            raise DahuaApiError(
                f"Dahua API returned invalid JSON: {error}",
                status_code=getattr(response, "status_code", None),
                payload=getattr(response, "text", None),
            )

        success = response.ok and str(payload.get("code", "")) in {"200", "0", ""}
        if response.ok and "code" not in payload:
            success = True

        log_vendor_call(
            local_endpoint=local_endpoint,
            vendor_endpoint=url,
            request_summary=body if isinstance(body, dict) else {"payload": body},
            response_status=response.status_code,
            response_payload=payload,
            success=success,
            trace_id=headers.get("X-TraceId-Header", ""),
            error=None if success else payload.get("msg") or payload.get("message"),
        )
        if not success:
            raise DahuaApiError(
                payload.get("msg") or payload.get("message") or "Dahua API call failed",
                status_code=response.status_code,
                payload=payload,
            )
        return payload

    @staticmethod
    def _timestamp() -> str:
        return str(int(utc_now().timestamp() * 1000))

    @staticmethod
    def _extract_required(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
        current: Any = payload
        for key in path:
            if not isinstance(current, dict) or key not in current:
                raise DahuaApiError(f"Missing expected field: {'.'.join(path)}", payload=payload)
            current = current[key]
        return current

    @staticmethod
    def _parse_expiry(data: dict[str, Any]) -> datetime:
        for key in ("expireTime", "expiresAt", "expiryTime", "expireAt"):
            value = data.get(key)
            if isinstance(value, str):
                try:
                    parsed = datetime.fromisoformat(value)
                    if parsed.tzinfo is None:
                        return parsed.replace(tzinfo=timezone.utc)
                    return parsed.astimezone(timezone.utc)
                except ValueError:
                    continue
            if isinstance(value, int):
                if value > 10_000_000_000:
                    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
                return datetime.fromtimestamp(value, tz=timezone.utc)

        for key in ("expiresIn", "expireIn", "ttl"):
            value = data.get(key)
            if isinstance(value, int) and value > 0:
                return utc_after(max(30, value - 60))

        return utc_after(55 * 60)

    @staticmethod
    def _build_url(path: str) -> str:
        normalized_path = path if path.startswith("/") else f"/{path}"
        return f"https://{settings.dahua_domain}{normalized_path}"


client = DahuaOpenApiClient()
