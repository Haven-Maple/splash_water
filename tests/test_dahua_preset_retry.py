from __future__ import annotations

import unittest
import sys
import types
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

# The bundled test runtime omits requests; the exercised control retry code is mocked before transport.
if "requests" not in sys.modules:
    requests_stub = types.ModuleType("requests")

    class _RequestException(Exception):
        pass

    class _ConnectTimeout(_RequestException):
        pass

    class _ReadTimeout(_RequestException):
        pass

    class _ConnectionError(_RequestException):
        pass

    requests_stub.RequestException = _RequestException
    requests_stub.ConnectTimeout = _ConnectTimeout
    requests_stub.ReadTimeout = _ReadTimeout
    requests_stub.ConnectionError = _ConnectionError
    requests_stub.Response = object
    requests_stub.Session = lambda: types.SimpleNamespace()
    sys.modules["requests"] = requests_stub

from app.services.dahua_preset_service import DahuaPresetService
from app.utils.request_sign_adapter import DahuaApiError, DahuaOpenApiClient
import app.utils.request_sign_adapter as request_sign_adapter_module


class DahuaPresetRetryTests(unittest.TestCase):
    def _control_client(self) -> DahuaOpenApiClient:
        return DahuaOpenApiClient.__new__(DahuaOpenApiClient)

    def test_read_timeout_retries_once_and_returns_attempt_diagnostics(self) -> None:
        client = self._control_client()
        client._last_trace_id = None
        timeout = DahuaApiError(
            "read timed out",
            network_failure_kind="read_timeout",
            trace_id="trace-first",
        )
        client.post_open_api = Mock(side_effect=[timeout, {"code": "200", "data": {"ok": True}}])

        with patch("app.utils.request_sign_adapter.sleep") as sleep_mock:
            payload, diagnostics = client.post_open_api_control_with_retry(
                path="/turn",
                body={"index": 1},
                local_endpoint="/api/preset/turn",
            )

        self.assertEqual(payload["code"], "200")
        self.assertEqual(client.post_open_api.call_count, 2)
        self.assertEqual(client.post_open_api.call_args.kwargs["timeout"], (2.0, 3.0))
        self.assertEqual(diagnostics["attemptCount"], 2)
        self.assertTrue(diagnostics["unknownStateRetrySucceeded"])
        sleep_mock.assert_called_once_with(0.5)

    def test_http_or_business_rejection_does_not_retry(self) -> None:
        client = self._control_client()
        client._last_trace_id = None
        rejected = DahuaApiError("business rejected", status_code=400, payload={"code": "400"})
        client.post_open_api = Mock(side_effect=rejected)

        with self.assertRaises(DahuaApiError) as raised:
            client.post_open_api_control_with_retry(
                path="/turn",
                body={"index": 1},
                local_endpoint="/api/preset/turn",
            )

        self.assertEqual(client.post_open_api.call_count, 1)
        self.assertEqual(raised.exception.attempts[0]["failureCategory"], "response_error")

    def test_retry_rebuilds_signed_headers_for_each_attempt(self) -> None:
        class _Signer:
            @staticmethod
            def open_sign(values):  # noqa: ANN001
                return f"signed-{values['nonce']}"

        client = self._control_client()
        client._sign_utils = _Signer()
        client._last_trace_id = None
        client.ensure_configured = Mock()
        client.get_app_access_token = Mock(return_value={"appAccessToken": "token"})
        client._post_json = Mock(
            side_effect=[
                DahuaApiError("read timed out", network_failure_kind="read_timeout"),
                {"code": "200", "data": {"ok": True}},
            ]
        )

        with patch("app.utils.request_sign_adapter.sleep"):
            client.post_open_api_control_with_retry(
                path="/turn",
                body={"index": 1},
                local_endpoint="/api/preset/turn",
            )

        first_headers = client._post_json.call_args_list[0].kwargs["headers"]
        second_headers = client._post_json.call_args_list[1].kwargs["headers"]
        self.assertNotEqual(first_headers["Nonce"], second_headers["Nonce"])
        self.assertNotEqual(first_headers["X-TraceId-Header"], second_headers["X-TraceId-Header"])
        self.assertNotEqual(first_headers["Sign"], second_headers["Sign"])

    def test_control_request_refreshes_token_once_after_401_with_short_timeout(self) -> None:
        class _Signer:
            @staticmethod
            def open_sign(values):  # noqa: ANN001
                return f"signed-{values['nonce']}"

        client = self._control_client()
        client._sign_utils = _Signer()
        client._last_trace_id = None
        client._last_token_fetch_elapsed_ms = 0
        client.ensure_configured = Mock()
        client.get_app_access_token = Mock(
            side_effect=[
                {"appAccessToken": "expired"},
                {"appAccessToken": "refreshed", "tokenFetchElapsedMs": 77},
                {"appAccessToken": "refreshed"},
            ]
        )
        client._post_json = Mock(
            side_effect=[
                DahuaApiError("unauthorized", status_code=401, payload={"code": "401"}),
                {"code": "200", "data": {"ok": True}},
            ]
        )

        payload, diagnostics = client.post_open_api_control_with_retry(
            path="/turn",
            body={"index": 1},
            local_endpoint="/api/preset/turn",
        )

        self.assertEqual(payload["code"], "200")
        self.assertEqual(diagnostics["attemptCount"], 1)
        self.assertEqual(diagnostics["attempts"][0]["tokenFetchElapsedMs"], 77)
        self.assertEqual(client.get_app_access_token.call_count, 3)
        self.assertEqual(client.get_app_access_token.call_args_list[0].kwargs, {"timeout": (2.0, 3.0), "deadline": None})
        self.assertEqual(
            client.get_app_access_token.call_args_list[1].kwargs,
            {"force_refresh": True, "timeout": (2.0, 3.0), "deadline": None},
        )
        self.assertEqual(client.get_app_access_token.call_args_list[2].kwargs, {"timeout": (2.0, 3.0), "deadline": None})

    def test_expired_deadline_after_token_fetch_does_not_dispatch_stream_url_request(self) -> None:
        class _Signer:
            @staticmethod
            def open_sign(values):  # noqa: ANN001
                return f"signed-{values['nonce']}"

        client = self._control_client()
        client._sign_utils = _Signer()
        client._last_trace_id = None
        client.ensure_configured = Mock()
        client.get_app_access_token = Mock(return_value={"appAccessToken": "token", "tokenFetchElapsedMs": 9000})
        client._post_json = Mock()

        with patch.object(request_sign_adapter_module, "monotonic", return_value=10.1):
            with self.assertRaises(DahuaApiError):
                client.post_open_api(
                    path="/stream",
                    body={"deviceId": "device"},
                    local_endpoint="/api/stream/flv",
                    timeout=10.0,
                    deadline=10.0,
                )

        client._post_json.assert_not_called()

    def test_turn_preset_preserves_control_retry_diagnostics(self) -> None:
        service = DahuaPresetService()
        diagnostics = {
            "attemptCount": 2,
            "attempts": [{"attempt": 1, "traceId": "first"}, {"attempt": 2, "traceId": "second"}],
            "unknownStateRetrySucceeded": True,
        }
        with patch(
            "app.services.dahua_preset_service.client.post_open_api_control_with_retry",
            return_value=({"data": {"accepted": True}}, diagnostics),
        ):
            response = service.turn_preset("device", "0", 1)

        self.assertTrue(response.accepted)
        self.assertEqual(response.attemptCount, 2)
        self.assertTrue(response.unknownStateRetrySucceeded)
        self.assertEqual(response.attempts[-1]["traceId"], "second")
