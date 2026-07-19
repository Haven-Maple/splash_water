from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from time import monotonic, sleep
from typing import Any, Literal

import numpy as np

from app.utils.logging_utils import logger

from inspector.config import RecognitionGlobalConfig
from inspector.models import SampledSequence


class FlvSamplerError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        reason: Literal["stream_failed", "insufficient_frames"] = "stream_failed",
    ) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(slots=True)
class StreamRecoveryBudget:
    maxReopens: int
    baseBackoffMs: int
    budgetMs: int
    startedAt: float | None = None
    reopenCount: int = 0
    attempts: list[dict[str, object]] | None = None

    @classmethod
    def from_config(cls, config: RecognitionGlobalConfig) -> "StreamRecoveryBudget":
        return cls(
            maxReopens=config.streamRecoveryMaxReopens,
            baseBackoffMs=config.streamRecoveryBaseBackoffMs,
            budgetMs=config.streamRecoveryBudgetMs,
            startedAt=None,
            attempts=[],
        )

    def elapsed_ms(self) -> int:
        if self.startedAt is None:
            return 0
        return max(0, int(round((monotonic() - self.startedAt) * 1000)))

    def begin(self) -> None:
        if self.startedAt is None:
            self.startedAt = monotonic()

    def can_reopen(self) -> bool:
        return self.reopenCount < self.maxReopens and self.elapsed_ms() < self.budgetMs

    def remaining_ms(self) -> int:
        return max(0, self.budgetMs - self.elapsed_ms())

    def deadline(self) -> float | None:
        return self.startedAt + self.budgetMs / 1000.0 if self.startedAt is not None else None

    def next_backoff_ms(self) -> int:
        return min(self.baseBackoffMs * (2**self.reopenCount), max(0, self.budgetMs - self.elapsed_ms()))

    def record(
        self,
        *,
        stage: str,
        backoff_ms: int,
        open_elapsed_ms: int,
        url_changed: bool,
        session_opened: bool,
    ) -> int:
        self.reopenCount += 1
        assert self.attempts is not None
        self.attempts.append(
            {
                "reopenIndex": self.reopenCount,
                "stage": stage,
                "backoffMs": backoff_ms,
                "openElapsedMs": open_elapsed_ms,
                "urlChanged": url_changed,
                "sessionOpened": session_opened,
                "streamRecovered": False,
            }
        )
        return self.reopenCount

    def mark_stream_recovered(self, reopen_index: int | None) -> None:
        if reopen_index is None or self.attempts is None:
            return
        for attempt in self.attempts:
            if attempt["reopenIndex"] == reopen_index:
                attempt["streamRecovered"] = True
                return

    def snapshot(self) -> dict[str, object]:
        return {
            "maxReopens": self.maxReopens,
            "budgetMs": self.budgetMs,
            "elapsedMs": self.elapsed_ms(),
            "reopenCount": self.reopenCount,
            "started": self.startedAt is not None,
            "exhausted": not self.can_reopen(),
            "attempts": self.attempts or [],
        }


@dataclass(slots=True)
class FlvStreamSession:
    streamType: str
    streamUrl: str
    capture: Any
    readTimeoutMs: int = 0
    maxConsecutiveReadFailures: int = 3
    quickFailureWindowMs: int = 250
    lastReadFailureReason: Literal["stream_read_timeout", "stream_eof", "stream_read_failed"] | None = None
    lastReadFailureCount: int = 0
    lastReadFailureElapsedMs: int = 0
    lastReadCallElapsedMs: int = 0
    openElapsedMs: int = 0
    streamUrlFingerprint: str = ""
    recoveryAttemptIndex: int | None = None

    def read_frame_until(self, deadline: float) -> tuple[np.ndarray, float] | None:
        self._clear_last_read_failure()
        consecutive_failures = 0
        first_failure_at: float | None = None
        while monotonic() < deadline:
            if not self._capture_is_opened():
                now = monotonic()
                failure_elapsed_ms = self._failure_elapsed_ms(first_failure_at, now)
                self._mark_read_failure("stream_eof", max(1, consecutive_failures), failure_elapsed_ms)
                return None

            try:
                read_started_at = monotonic()
                success, frame = self.capture.read()
                now = monotonic()
                self.lastReadCallElapsedMs = max(0, int(round((now - read_started_at) * 1000)))
            except Exception as error:
                now = monotonic()
                self.lastReadCallElapsedMs = max(0, int(round((now - read_started_at) * 1000)))
                consecutive_failures += 1
                first_failure_at = first_failure_at or now
                failure_elapsed_ms = self._failure_elapsed_ms(first_failure_at, now)
                failure_reason = (
                    "stream_read_timeout"
                    if self.readTimeoutMs > 0 and self.lastReadCallElapsedMs >= self.readTimeoutMs
                    else self._classify_capture_exception(error)
                )
                self._mark_read_failure(failure_reason, consecutive_failures, failure_elapsed_ms)
                logger.warning(
                    "FLV session read raised %s for %s after %s failures elapsedMs=%s",
                    failure_reason,
                    self.streamUrl,
                    consecutive_failures,
                    failure_elapsed_ms,
                )
                return None

            if self.readTimeoutMs > 0 and self.lastReadCallElapsedMs >= self.readTimeoutMs:
                self._mark_read_failure("stream_read_timeout", 1, self.lastReadCallElapsedMs)
                logger.warning(
                    "FLV session read exceeded configured timeout for %s callElapsedMs=%s timeoutMs=%s",
                    self.streamUrl,
                    self.lastReadCallElapsedMs,
                    self.readTimeoutMs,
                )
                return None
            if success and frame is not None:
                self._clear_last_read_failure()
                return frame, now

            consecutive_failures += 1
            first_failure_at = first_failure_at or now
            failure_elapsed_ms = self._failure_elapsed_ms(first_failure_at, now)
            failure_reason = self._classify_failed_read(
                now=now,
                deadline=deadline,
                consecutive_failures=consecutive_failures,
                failure_elapsed_ms=failure_elapsed_ms,
            )
            if failure_reason is not None:
                self._mark_read_failure(failure_reason, consecutive_failures, failure_elapsed_ms)
                logger.warning(
                    "FLV session read stopped with %s for %s after %s failures elapsedMs=%s",
                    failure_reason,
                    self.streamUrl,
                    consecutive_failures,
                    failure_elapsed_ms,
                )
                return None
            sleep(0.01)

        self._mark_read_failure(
            "stream_read_timeout",
            max(1, consecutive_failures),
            self._failure_elapsed_ms(first_failure_at, monotonic()),
        )
        return None

    def release(self) -> None:
        self.capture.release()

    def __enter__(self) -> "FlvStreamSession":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()

    def _capture_is_opened(self) -> bool:
        if not hasattr(self.capture, "isOpened"):
            return True
        try:
            return bool(self.capture.isOpened())
        except Exception:
            return True

    def _mark_read_failure(
        self,
        reason: Literal["stream_read_timeout", "stream_eof", "stream_read_failed"],
        count: int,
        elapsed_ms: int,
    ) -> None:
        self.lastReadFailureReason = reason
        self.lastReadFailureCount = count
        self.lastReadFailureElapsedMs = elapsed_ms

    def _clear_last_read_failure(self) -> None:
        self.lastReadFailureReason = None
        self.lastReadFailureCount = 0
        self.lastReadFailureElapsedMs = 0
        self.lastReadCallElapsedMs = 0

    @staticmethod
    def _failure_elapsed_ms(first_failure_at: float | None, now: float) -> int:
        if first_failure_at is None:
            return 0
        return max(0, int(round((now - first_failure_at) * 1000)))

    def _classify_failed_read(
        self,
        *,
        now: float,
        deadline: float,
        consecutive_failures: int,
        failure_elapsed_ms: int,
    ) -> Literal["stream_read_timeout", "stream_eof", "stream_read_failed"] | None:
        if not self._capture_is_opened():
            return "stream_eof"
        if now >= deadline:
            return "stream_read_timeout"
        if consecutive_failures < self.maxConsecutiveReadFailures:
            return None
        if failure_elapsed_ms >= max(120, self.quickFailureWindowMs):
            if self.readTimeoutMs > 0 and failure_elapsed_ms >= min(self.readTimeoutMs, 1000):
                return "stream_read_timeout"
            return "stream_read_failed"
        return None

    @staticmethod
    def _classify_capture_exception(
        error: Exception,
    ) -> Literal["stream_read_timeout", "stream_eof", "stream_read_failed"]:
        message = str(error).lower()
        if "timed out" in message or "timeout" in message or "unable to read from socket" in message:
            return "stream_read_timeout"
        if "prematurely" in message or "eof" in message or "end of file" in message:
            return "stream_eof"
        return "stream_read_failed"


class FlvSequenceSampler:
    def __init__(self, global_config: RecognitionGlobalConfig) -> None:
        self.global_config = global_config

    def open_session(
        self,
        *,
        device_id: str,
        channel_id: str,
        recovery_timeout_ms: int | None = None,
        recovery_deadline: float | None = None,
    ) -> FlvStreamSession:
        opened_at = monotonic()
        try:
            from app.services.dahua_stream_service import stream_service
        except Exception as error:
            raise FlvSamplerError(f"Stream runtime dependencies are unavailable: {error}") from error

        try:
            import cv2  # type: ignore[import-not-found]
        except Exception as error:
            raise FlvSamplerError(f"OpenCV runtime dependency is unavailable: {error}") from error

        if recovery_timeout_ms is not None and recovery_timeout_ms <= 0:
            raise FlvSamplerError("FLV recovery budget expired before requesting a new stream URL")
        if recovery_deadline is None and recovery_timeout_ms is not None:
            recovery_deadline = opened_at + recovery_timeout_ms / 1000.0
        url_timeout_ms = min(
            self.global_config.streamOpenTimeoutMs,
            recovery_timeout_ms if recovery_timeout_ms is not None else self.global_config.streamOpenTimeoutMs,
        )
        try:
            if recovery_timeout_ms is None:
                stream = stream_service.get_flv_stream(device_id, channel_id)
            else:
                stream = stream_service.get_flv_stream(
                    device_id,
                    channel_id,
                    timeout=max(0.001, url_timeout_ms / 1000),
                    deadline=recovery_deadline,
                )
        except Exception as error:
            raise FlvSamplerError(f"Failed to obtain a fresh FLV stream URL: {error}") from error
        if recovery_deadline is not None:
            open_timeout_ms = max(0, int((recovery_deadline - monotonic()) * 1000))
            if open_timeout_ms <= 0:
                raise FlvSamplerError("FLV recovery budget expired before opening the refreshed stream")
        else:
            open_timeout_ms = self.global_config.streamOpenTimeoutMs
        open_timeout_ms = min(self.global_config.streamOpenTimeoutMs, open_timeout_ms)
        capture = self._open_capture_with_timeouts(cv2, stream.streamUrl, open_timeout_ms=open_timeout_ms)
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not capture.isOpened():
            capture.release()
            raise FlvSamplerError(f"Failed to open FLV stream within {open_timeout_ms} ms")

        return FlvStreamSession(
            streamType=stream.streamType,
            streamUrl=stream.streamUrl,
            capture=capture,
            readTimeoutMs=self.global_config.frameReadTimeoutMs,
            openElapsedMs=max(1, int(round((monotonic() - opened_at) * 1000))),
            streamUrlFingerprint=self._url_fingerprint(stream.streamUrl),
        )

    @staticmethod
    def _url_fingerprint(stream_url: str) -> str:
        return sha256(stream_url.encode("utf-8")).hexdigest()[:12]

    def _open_capture_with_timeouts(self, cv2: Any, stream_url: str, *, open_timeout_ms: int | None = None) -> Any:
        required_properties = ("CAP_FFMPEG", "CAP_PROP_OPEN_TIMEOUT_MSEC", "CAP_PROP_READ_TIMEOUT_MSEC")
        missing_properties = [name for name in required_properties if not hasattr(cv2, name)]
        if missing_properties:
            raise FlvSamplerError(
                "OpenCV backend does not support parameterized FFmpeg stream timeouts: "
                f"missing {', '.join(missing_properties)}"
            )

        try:
            capture = cv2.VideoCapture()
        except Exception as error:
            raise FlvSamplerError(f"Failed to create OpenCV VideoCapture for FFmpeg stream: {error}") from error

        parameters = [
            int(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC),
            int(open_timeout_ms if open_timeout_ms is not None else self.global_config.streamOpenTimeoutMs),
            int(cv2.CAP_PROP_READ_TIMEOUT_MSEC),
            int(self.global_config.frameReadTimeoutMs),
        ]
        try:
            opened = capture.open(stream_url, int(cv2.CAP_FFMPEG), parameters)
        except Exception as error:
            capture.release()
            raise FlvSamplerError(
                "OpenCV FFmpeg parameterized stream open is unavailable; refusing an unbounded fallback: "
                f"{error}"
            ) from error
        if not opened:
            capture.release()
            raise FlvSamplerError(
                "OpenCV FFmpeg parameterized stream open failed "
                f"within configured timeout {self.global_config.streamOpenTimeoutMs} ms"
            )
        return capture

    def sample(self, *, device_id: str, channel_id: str) -> SampledSequence:
        with self.open_session(device_id=device_id, channel_id=channel_id) as session:
            return self.sample_from_session(session)

    def sample_from_session(self, session: FlvStreamSession) -> SampledSequence:
        target_frame_count = self.global_config.sequenceFrameCount
        frame_interval_s = 1.0 / float(self.global_config.sampleFps)
        configured_duration_s = self.global_config.sampleDurationMs / 1000
        required_window_s = max(configured_duration_s, target_frame_count * frame_interval_s)
        sample_started_at = monotonic()
        hard_deadline = sample_started_at + required_window_s + self.global_config.frameReadTimeoutMs / 1000
        next_sample_at = sample_started_at

        sampled_frames: list[np.ndarray] = []
        sampled_timestamps_ms: list[int] = []

        while len(sampled_frames) < target_frame_count and monotonic() < hard_deadline:
            frame_result = session.read_frame_until(hard_deadline)
            if frame_result is None:
                break

            frame, now = frame_result
            if now + 0.002 < next_sample_at:
                continue

            sampled_frames.append(frame.copy())
            sampled_timestamps_ms.append(int(round((now - sample_started_at) * 1000)))
            next_sample_at = sample_started_at + len(sampled_frames) * frame_interval_s

        elapsed_ms = int(round((monotonic() - sample_started_at) * 1000))
        return self._build_sequence(
            stream_type=session.streamType,
            stream_url=session.streamUrl,
            frames=sampled_frames,
            timestamps_ms=sampled_timestamps_ms,
            target_frame_count=target_frame_count,
            configured_sample_fps=float(self.global_config.sampleFps),
            configured_duration_ms=self.global_config.sampleDurationMs,
            elapsed_ms=elapsed_ms,
        )

    def build_sequence_from_frames(
        self,
        *,
        stream_type: str,
        stream_url: str,
        frames: list[np.ndarray],
        timestamps_ms: list[int],
        configured_sample_fps: float | None = None,
        configured_duration_ms: int | None = None,
        target_frame_count: int | None = None,
    ) -> SampledSequence:
        if timestamps_ms:
            elapsed_ms = max(timestamps_ms[-1], 1)
        else:
            elapsed_ms = 0
        return self._build_sequence(
            stream_type=stream_type,
            stream_url=stream_url,
            frames=frames,
            timestamps_ms=timestamps_ms,
            target_frame_count=target_frame_count or max(1, len(frames)),
            configured_sample_fps=configured_sample_fps or float(self.global_config.sampleFps),
            configured_duration_ms=configured_duration_ms or max(1, elapsed_ms),
            elapsed_ms=max(1, elapsed_ms),
            allow_partial=True,
        )

    def _build_sequence(
        self,
        *,
        stream_type: str,
        stream_url: str,
        frames: list[np.ndarray],
        timestamps_ms: list[int],
        target_frame_count: int,
        configured_sample_fps: float,
        configured_duration_ms: int,
        elapsed_ms: int,
        allow_partial: bool = False,
    ) -> SampledSequence:
        sampled_frame_count = len(frames)
        if sampled_frame_count == 0:
            raise FlvSamplerError("FLV sampling produced zero frames", reason="insufficient_frames")

        if not allow_partial and sampled_frame_count < target_frame_count:
            raise FlvSamplerError(
                f"FLV sampling produced {sampled_frame_count}/{target_frame_count} frames within {elapsed_ms} ms",
                reason="insufficient_frames",
            )

        stacked_frames = np.stack(frames, axis=0)
        actual_sample_fps = sampled_frame_count / max(elapsed_ms / 1000, 0.001)
        logger.info(
            "FLV sampler captured %s frames from %s in %s ms",
            sampled_frame_count,
            stream_url,
            elapsed_ms,
        )
        return SampledSequence(
            streamType=stream_type,
            streamUrl=stream_url,
            frames=stacked_frames,
            frameTimestampsMs=timestamps_ms,
            targetFrameCount=target_frame_count,
            sampledFrameCount=sampled_frame_count,
            configuredSampleFps=float(configured_sample_fps),
            actualSampleFps=actual_sample_fps,
            configuredSampleDurationMs=int(configured_duration_ms),
            actualSampleDurationMs=elapsed_ms,
            frameWidth=int(stacked_frames.shape[2]),
            frameHeight=int(stacked_frames.shape[1]),
        )
