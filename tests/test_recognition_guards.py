from __future__ import annotations

import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from contextlib import redirect_stderr
from time import monotonic, perf_counter
from unittest.mock import Mock, PropertyMock, patch

import numpy as np

import inspector.flv_sampler as flv_sampler_module
import inspector.night_ir_gap_fill_scan as gap_fill_scan_module
from app.schemas.calibration import RoiModel
from inspector.config import RecognitionGlobalConfig, build_recognition_config
from inspector.flv_sampler import FlvSamplerError, FlvSequenceSampler, FlvStreamSession
from inspector.frame_scoring import WeightedFrameScorer
from inspector.models import (
    AlignedSequence,
    FrameFeature,
    RecognitionEvidencePaths,
    RecognitionRunResult,
    RecognitionScoreSummary,
    RecognitionTarget,
    RecognitionTiming,
    ReplaySaveState,
    SampleQualityMetrics,
    SampledSequence,
    SceneModeDiagnostics,
    SceneModeStabilityMetrics,
    StreamStartupFreshnessMetrics,
)
from inspector.pseudo_multi_point_test import (
    PseudoMultiPointRoundResult,
    PseudoMultiPointRuntimeConfig,
    PseudoMultiPointRunner,
    PseudoMultiPointSummary,
    TransitionPresetStepResult,
    build_summary,
    emit_round_progress,
)
from inspector.replay_store import ReplayStore
from inspector.run_once_service import RunOnceService
from inspector.roi_tolerance import (
    RoiToleranceSequenceMetrics,
    generate_night_roi_candidates,
    select_sequence_candidate,
)
from inspector.scene_mode_resolver import SceneModeDecision
from inspector.scene_mode_stability import SceneModeStabilityGuard, SceneModeStabilityResult, SceneModeStabilityWindow
from inspector.temporal_voting import TemporalVoteResolver
from inspector.visual_readiness import VisualReadinessChecker, VisualReadinessMetrics, VisualReadinessOutcome


class _FakeSession:
    def __init__(self, frames: list[np.ndarray], *, frame_interval_s: float = 0.1) -> None:
        self._frames = [frame.copy() for frame in frames]
        self._index = 0
        self._base_time: float | None = None
        self._frame_interval_s = frame_interval_s
        self.streamType = "flv"
        self.streamUrl = "fake://stream"

    def read_frame_until(self, deadline: float) -> tuple[np.ndarray, float] | None:
        if self._index >= len(self._frames):
            return None
        if self._base_time is None:
            self._base_time = monotonic()
        frame = self._frames[self._index]
        self._index += 1
        return frame, self._base_time + (self._index * self._frame_interval_s)

    def release(self) -> None:
        return None


class _FailingCapture:
    def __init__(self, *, opened: bool = True) -> None:
        self._opened = opened
        self.read_calls = 0

    def read(self) -> tuple[bool, None]:
        self.read_calls += 1
        return False, None

    def isOpened(self) -> bool:
        return self._opened

    def release(self) -> None:
        self._opened = False


class _InterruptingSession(_FakeSession):
    def __init__(
        self,
        frames: list[np.ndarray],
        *,
        frame_interval_s: float = 0.1,
        failure_reason: str = "stream_eof",
        failure_count: int = 3,
    ) -> None:
        super().__init__(frames, frame_interval_s=frame_interval_s)
        self.failure_reason = failure_reason
        self.failure_count = failure_count
        self.lastReadFailureReason: str | None = None
        self.lastReadFailureCount = 0
        self.lastReadFailureElapsedMs = 0
        self.released = False

    def read_frame_until(self, deadline: float) -> tuple[np.ndarray, float] | None:
        if self._index < len(self._frames):
            self.lastReadFailureReason = None
            self.lastReadFailureCount = 0
            self.lastReadFailureElapsedMs = 0
            return super().read_frame_until(deadline)
        self.lastReadFailureReason = self.failure_reason
        self.lastReadFailureCount = self.failure_count
        self.lastReadFailureElapsedMs = 180
        return None

    def release(self) -> None:
        self.released = True


class _FakeTransitionTurner:
    def turn_preset(self, device_id: str, channel_id: str, preset_index: int):  # noqa: ANN001
        class _Response:
            accepted = True
            raw = {"deviceId": device_id, "channelId": channel_id, "presetIndex": preset_index}

        return _Response()


class _FakeRunOnceService:
    def __init__(self, result: RecognitionRunResult) -> None:
        self._result = result

    def run(self, *, config_path: Path, requested_preset_index: int | None) -> RecognitionRunResult:
        return self._result


def _make_checkerboard_frame(size: tuple[int, int] = (120, 160), *, block: int = 8) -> np.ndarray:
    height, width = size
    y, x = np.indices((height, width))
    pattern = (((x // block) + (y // block)) % 2) * 255
    frame = np.repeat(pattern[..., None], 3, axis=2).astype(np.uint8)
    return frame


def _make_single_edge_frame(size: tuple[int, int] = (120, 160)) -> np.ndarray:
    frame = np.full((size[0], size[1], 3), 127, dtype=np.uint8)
    frame[10:30, 10:30, :] = 255
    return frame


class NightRoiToleranceTests(unittest.TestCase):
    @staticmethod
    def _sequence() -> SampledSequence:
        frame = np.zeros((120, 160, 3), dtype=np.uint8)
        return SampledSequence(
            streamType="flv",
            streamUrl="fake://stream",
            frames=np.stack([frame] * 20),
            frameTimestampsMs=[index * 100 for index in range(20)],
            targetFrameCount=20,
            sampledFrameCount=20,
            configuredSampleFps=10,
            actualSampleFps=10.0,
            configuredSampleDurationMs=2000,
            actualSampleDurationMs=1900,
            frameWidth=160,
            frameHeight=120,
        )

    @staticmethod
    def _feature(frame_index: int, *, center_coverage: float) -> FrameFeature:
        return FrameFeature(
            frameIndex=frame_index,
            brightThreshold=120.0,
            roiBrightnessQ99=220.0,
            roiBrightnessMax=255.0,
            localResidualMotion=0.08,
            dynamicAreaRatio=0.2,
            highlightDisturbance=0.08,
            largestBrightComponentRatio=0.5,
            brightComponentCount=1,
            fragmentationScore=0.05,
            centerBrightCoverage=center_coverage,
            upperHalfBrightRatio=0.3,
            lowerHalfBrightRatio=0.3,
            verticalSpreadRatio=0.6,
            gapFillRatio=0.9,
            temporalAreaVariance=0.2,
            temporalShapeVariance=0.4,
        )

    def test_candidates_cover_offsets_and_scale_with_base_first(self) -> None:
        base_roi = RoiModel(x=50, y=40, width=104, height=64)

        candidates = generate_night_roi_candidates(base_roi, frame_width=320, frame_height=240)

        self.assertEqual(len(candidates), 18)
        self.assertTrue(candidates[0].isBase)
        self.assertEqual(candidates[0].roi, base_roi)
        shifted = next(
            item for item in candidates
            if item.offsetXRatio == 0.08 and item.offsetYRatio == 0.08 and item.scale == 1.0
        )
        self.assertEqual(shifted.roi, RoiModel(x=58, y=45, width=104, height=64))
        expanded = next(
            item for item in candidates
            if item.offsetXRatio == 0.0 and item.offsetYRatio == 0.0 and item.scale == 1.1
        )
        self.assertEqual(expanded.roi, RoiModel(x=45, y=37, width=114, height=70))

    def test_out_of_bounds_variants_are_skipped_without_resizing_base_roi(self) -> None:
        base_roi = RoiModel(x=0, y=0, width=40, height=30)

        candidates = generate_night_roi_candidates(base_roi, frame_width=100, frame_height=80)

        self.assertEqual(candidates[0].roi, base_roi)
        self.assertTrue(any(item.skipReason == "out_of_bounds" for item in candidates[1:]))
        self.assertTrue(all(item.roi is None or item.roi.width in {40, 44} for item in candidates))

    def test_sequence_selection_prefers_base_on_exact_tie(self) -> None:
        candidates = generate_night_roi_candidates(RoiModel(x=30, y=30, width=40, height=30), 160, 120)
        metrics = {
            item.key: RoiToleranceSequenceMetrics(
                framePassCount=12,
                hardGatePassCount=12,
                weightedFrameScoreMean=0.8,
                dynamicEvidencePassCount=14,
            )
            for item in candidates
            if item.roi is not None
        }

        selected = select_sequence_candidate(candidates, metrics)

        self.assertTrue(selected.isBase)

    def test_sequence_selection_uses_complete_window_not_per_frame_combination(self) -> None:
        candidates = generate_night_roi_candidates(RoiModel(x=30, y=30, width=40, height=30), 160, 120)
        base = candidates[0]
        shifted = next(item for item in candidates if item.roi is not None and not item.isBase)
        metrics = {
            base.key: RoiToleranceSequenceMetrics(9, 12, 0.95, 18),
            shifted.key: RoiToleranceSequenceMetrics(12, 11, 0.70, 12),
        }

        selected = select_sequence_candidate([base, shifted], metrics)

        self.assertEqual(selected.key, shifted.key)

    def test_shifted_night_roi_can_rescue_complete_sequence_without_changing_base_thresholds(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="night_ir",
            hardGateMinCenterBrightCoverage=0.46,
            hardGateMinGapFillRatio=0.76,
            nightRoiToleranceEnabled=True,
        )
        service = RunOnceService(global_config=config)
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi=RoiModel(x=50, y=40, width=60, height=40),
        )

        def extract(_frames, roi):  # noqa: ANN001
            center = 0.5 if roi.x != 50 else 0.2
            return [self._feature(index, center_coverage=center) for index in range(20)]

        sequence = self._sequence()
        # The bright region falls outside the base ROI's center but inside shifted candidates.
        sequence.frames[:, 55:65, 98:108, :] = 255
        aligned = AlignedSequence(
            alignedFrames=sequence.frames,
            globalShifts=[(0, 0)] * 20,
            shiftMagnitudes=[0.0] * 20,
            appliedGlobalShifts=[(0, 0)] * 20,
            appliedShiftMagnitudes=[0.0] * 20,
            overflowFlags=[False] * 20,
            alignmentApplied=False,
        )
        with patch("inspector.run_once_service.FullFrameAligner.align", return_value=aligned), patch(
            "inspector.run_once_service.FrameFeatureExtractor.extract", side_effect=extract
        ) as extract_mock:
            result = service._run_detection_pass(sequence=sequence, target=target, effective_config=config)

        self.assertEqual(result.voteDecision.visualState, "has_splash")
        self.assertIsNotNone(result.roiTolerance)
        self.assertFalse(result.roiTolerance.selectedCandidate.isBase)
        self.assertEqual(result.roiTolerance.baseFramePassCount, 0)
        self.assertEqual(result.roiTolerance.selectedFramePassCount, 20)
        self.assertLessEqual(result.roiTolerance.evaluatedCandidateCount, 3)
        self.assertLessEqual(extract_mock.call_count, 3)
        self.assertTrue(result.roiTolerance.rescued)
        self.assertEqual(config.hardGateMinCenterBrightCoverage, 0.46)
        self.assertEqual(config.hardGateMinGapFillRatio, 0.76)

    def test_night_roi_search_does_not_turn_uniform_failure_into_has_splash(self) -> None:
        config = RecognitionGlobalConfig(sceneMode="night_ir", nightRoiToleranceEnabled=True)
        service = RunOnceService(global_config=config)
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi=RoiModel(x=50, y=40, width=60, height=40),
        )
        aligned = AlignedSequence(
            alignedFrames=self._sequence().frames,
            globalShifts=[(0, 0)] * 20,
            shiftMagnitudes=[0.0] * 20,
            appliedGlobalShifts=[(0, 0)] * 20,
            appliedShiftMagnitudes=[0.0] * 20,
            overflowFlags=[False] * 20,
            alignmentApplied=False,
        )
        with patch("inspector.run_once_service.FullFrameAligner.align", return_value=aligned), patch(
            "inspector.run_once_service.FrameFeatureExtractor.extract",
            return_value=[self._feature(index, center_coverage=0.0) for index in range(20)],
        ) as extract_mock:
            result = service._run_detection_pass(sequence=self._sequence(), target=target, effective_config=config)

        self.assertEqual(result.voteDecision.visualState, "no_splash")
        self.assertIsNotNone(result.roiTolerance)
        self.assertTrue(result.roiTolerance.selectedCandidate.isBase)
        self.assertFalse(result.roiTolerance.rescued)
        self.assertLessEqual(extract_mock.call_count, 3)

    def test_day_visible_does_not_enter_night_roi_candidate_search(self) -> None:
        config = RecognitionGlobalConfig(sceneMode="day_visible", nightRoiToleranceEnabled=True)
        service = RunOnceService(global_config=config)
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi=RoiModel(x=50, y=40, width=60, height=40),
        )
        aligned = AlignedSequence(
            alignedFrames=self._sequence().frames,
            globalShifts=[(0, 0)] * 20,
            shiftMagnitudes=[0.0] * 20,
            appliedGlobalShifts=[(0, 0)] * 20,
            appliedShiftMagnitudes=[0.0] * 20,
            overflowFlags=[False] * 20,
            alignmentApplied=False,
        )
        with patch("inspector.run_once_service.FullFrameAligner.align", return_value=aligned), patch(
            "inspector.run_once_service.FrameFeatureExtractor.extract",
            return_value=[self._feature(index, center_coverage=0.5) for index in range(20)],
        ) as extract:
            result = service._run_detection_pass(sequence=self._sequence(), target=target, effective_config=config)

        self.assertIsNone(result.roiTolerance)
        extract.assert_called_once()


class VisualReadinessCheckerTests(unittest.TestCase):
    def test_laplacian_variance_separates_sharp_and_blurry_frames(self) -> None:
        config = RecognitionGlobalConfig(
            visualReadinessMinFrames=3,
            visualReadinessMinSharpness=10.0,
            visualReadinessMaxStabilityScore=0.2,
            visualReadinessMinElapsedMs=0,
            visualReadinessMinReadyWindowMs=0,
        )
        checker = VisualReadinessChecker(config)

        sharp = np.zeros((120, 160, 3), dtype=np.uint8)
        sharp[:, 78:82, :] = 255
        sharp[58:62, :, :] = 255
        blur = np.full((120, 160, 3), 127, dtype=np.uint8)

        sharpness_sharp = checker._laplacian_variance(checker._prepare_grayscale(sharp))
        sharpness_blur = checker._laplacian_variance(checker._prepare_grayscale(blur))

        self.assertGreater(sharpness_sharp, config.visualReadinessMinSharpness)
        self.assertLess(sharpness_blur, config.visualReadinessMinSharpness)


class FlvStreamSessionTests(unittest.TestCase):
    def test_parameterized_ffmpeg_open_passes_timeout_properties_before_open(self) -> None:
        class _Capture:
            def __init__(self) -> None:
                self.opened = False
                self.open_calls: list[tuple[str, int, list[int]]] = []

            def open(self, url: str, backend: int, parameters: list[int]) -> bool:
                self.open_calls.append((url, backend, parameters))
                self.opened = True
                return True

            def isOpened(self) -> bool:
                return self.opened

            def set(self, _property: int, _value: int) -> bool:
                return True

            def release(self) -> None:
                self.opened = False

        capture = _Capture()
        cv2_module = types.SimpleNamespace(
            CAP_FFMPEG=1900,
            CAP_PROP_OPEN_TIMEOUT_MSEC=53,
            CAP_PROP_READ_TIMEOUT_MSEC=54,
            CAP_PROP_BUFFERSIZE=38,
            VideoCapture=Mock(return_value=capture),
        )
        stream_module = types.ModuleType("app.services.dahua_stream_service")
        stream_module.stream_service = types.SimpleNamespace(
            get_flv_stream=Mock(return_value=types.SimpleNamespace(streamType="flv", streamUrl="https://stream"))
        )
        sampler = FlvSequenceSampler(RecognitionGlobalConfig(streamOpenTimeoutMs=6000, frameReadTimeoutMs=3000))

        with patch.dict(sys.modules, {"cv2": cv2_module, "app.services.dahua_stream_service": stream_module}):
            session = sampler.open_session(device_id="device", channel_id="0")

        self.assertEqual(capture.open_calls, [("https://stream", 1900, [53, 6000, 54, 3000])])
        self.assertEqual(session.readTimeoutMs, 3000)

    def test_blocked_read_uses_real_call_elapsed_time_for_timeout_diagnosis(self) -> None:
        class _SlowCapture:
            def isOpened(self) -> bool:
                return True

            def read(self) -> tuple[bool, None]:
                return True, None

        session = FlvStreamSession(
            streamType="flv",
            streamUrl="fake://stream",
            capture=_SlowCapture(),
            readTimeoutMs=3000,
        )
        monotonic_values = iter([0.0, 0.0, 3.2])

        with patch.object(flv_sampler_module, "monotonic", side_effect=lambda: next(monotonic_values)):
            frame_result = session.read_frame_until(10.0)

        self.assertIsNone(frame_result)
        self.assertEqual(session.lastReadFailureReason, "stream_read_timeout")
        self.assertEqual(session.lastReadCallElapsedMs, 3200)
        self.assertEqual(session.lastReadFailureElapsedMs, 3200)

    def test_read_frame_until_exits_quickly_after_consecutive_failures(self) -> None:
        session = FlvStreamSession(
            streamType="flv",
            streamUrl="fake://stream",
            capture=_FailingCapture(),
            readTimeoutMs=3000,
            maxConsecutiveReadFailures=3,
            quickFailureWindowMs=120,
        )
        monotonic_values = iter([0.0, 0.0, 0.05, 0.05, 0.10, 0.10, 0.15, 0.15, 0.20, 0.20, 0.25, 0.25])

        with patch.object(flv_sampler_module, "monotonic", side_effect=lambda: next(monotonic_values)):
            with patch.object(flv_sampler_module, "sleep", return_value=None):
                frame_result = session.read_frame_until(1.0)

        self.assertIsNone(frame_result)
        self.assertEqual(session.lastReadFailureReason, "stream_read_failed")
        self.assertGreaterEqual(session.lastReadFailureCount, 3)
        self.assertGreaterEqual(session.lastReadFailureElapsedMs, 120)

    def test_wait_until_ready_requires_consecutive_clear_frames(self) -> None:
        config = RecognitionGlobalConfig(
            visualReadinessMinFrames=3,
            visualReadinessMinSharpness=10.0,
            visualReadinessMaxStabilityScore=0.2,
            visualReadinessTimeoutMs=500,
            visualReadinessMinElapsedMs=0,
            visualReadinessMinReadyWindowMs=0,
        )
        checker = VisualReadinessChecker(config)

        blurry = np.full((120, 160, 3), 127, dtype=np.uint8)
        sharp = np.zeros((120, 160, 3), dtype=np.uint8)
        sharp[:, 78:82, :] = 255
        sharp[58:62, :, :] = 255
        session = _FakeSession([blurry, sharp, sharp, sharp, sharp])

        outcome = checker.wait_until_ready(session)

        self.assertTrue(outcome.metrics.ready)
        self.assertEqual(outcome.metrics.reason, "visual_ready")
        self.assertEqual(outcome.metrics.framesChecked, 5)

    def test_wait_until_ready_returns_blurry_reason_when_never_sharp(self) -> None:
        config = RecognitionGlobalConfig(
            visualReadinessMinFrames=3,
            visualReadinessMinSharpness=10.0,
            visualReadinessMaxStabilityScore=0.2,
            visualReadinessTimeoutMs=500,
            visualReadinessMinElapsedMs=0,
            visualReadinessMinReadyWindowMs=0,
        )
        checker = VisualReadinessChecker(config)

        blurry = np.full((120, 160, 3), 127, dtype=np.uint8)
        session = _FakeSession([blurry, blurry, blurry, blurry])

        outcome = checker.wait_until_ready(session)

        self.assertFalse(outcome.metrics.ready)
        self.assertEqual(outcome.metrics.reason, "visual_not_ready_blurry")

    def test_wait_until_ready_uses_target_roi_not_unrelated_sharp_edges(self) -> None:
        config = RecognitionGlobalConfig(
            visualReadinessMinFrames=3,
            visualReadinessMinSharpness=10.0,
            visualReadinessMaxStabilityScore=0.2,
            visualReadinessTimeoutMs=500,
            visualReadinessUseTargetRoi=True,
            visualReadinessRoiExpandRatio=0.0,
            visualReadinessCropRatio=0.6,
            visualReadinessMinElapsedMs=0,
            visualReadinessMinReadyWindowMs=0,
        )
        checker = VisualReadinessChecker(config)

        frame = np.full((120, 160, 3), 127, dtype=np.uint8)
        frame[55:65, :, :] = 255
        frame[:, 75:85, :] = 255
        roi = types.SimpleNamespace(x=0, y=0, width=30, height=30)

        without_roi = checker.wait_until_ready(_FakeSession([frame, frame, frame, frame]))
        with_roi = checker.wait_until_ready(_FakeSession([frame, frame, frame, frame]), roi=roi)

        self.assertTrue(without_roi.metrics.ready)
        self.assertFalse(with_roi.metrics.ready)
        self.assertEqual(with_roi.metrics.reason, "visual_not_ready_blurry")

    def test_wait_until_ready_requires_min_ready_window_and_elapsed(self) -> None:
        config = RecognitionGlobalConfig(
            visualReadinessMinFrames=4,
            visualReadinessMinSharpness=10.0,
            visualReadinessMaxStabilityScore=0.2,
            visualReadinessTimeoutMs=500,
            visualReadinessMinElapsedMs=300,
            visualReadinessMinReadyWindowMs=250,
        )
        checker = VisualReadinessChecker(config)

        sharp = np.zeros((120, 160, 3), dtype=np.uint8)
        sharp[:, 78:82, :] = 255
        sharp[58:62, :, :] = 255

        fast_session = _FakeSession([sharp, sharp, sharp, sharp], frame_interval_s=0.01)
        slow_session = _FakeSession([sharp, sharp, sharp, sharp, sharp], frame_interval_s=0.12)

        fast_outcome = checker.wait_until_ready(fast_session)
        slow_outcome = checker.wait_until_ready(slow_session)

        self.assertFalse(fast_outcome.metrics.ready)
        self.assertEqual(fast_outcome.metrics.reason, "visual_not_ready_min_elapsed")
        self.assertTrue(slow_outcome.metrics.ready)

    def test_wait_until_ready_reports_ready_window_short_separately(self) -> None:
        config = RecognitionGlobalConfig(
            visualReadinessMinFrames=4,
            visualReadinessMinSharpness=10.0,
            visualReadinessMaxStabilityScore=0.2,
            visualReadinessTimeoutMs=500,
            visualReadinessMinElapsedMs=0,
            visualReadinessMinReadyWindowMs=250,
        )
        checker = VisualReadinessChecker(config)

        sharp = np.zeros((120, 160, 3), dtype=np.uint8)
        sharp[:, 78:82, :] = 255
        sharp[58:62, :, :] = 255

        session = _FakeSession([sharp, sharp, sharp, sharp], frame_interval_s=0.02)
        outcome = checker.wait_until_ready(session)

        self.assertFalse(outcome.metrics.ready)
        self.assertEqual(outcome.metrics.reason, "visual_not_ready_ready_window_short")

    def test_wait_until_ready_accumulates_window_beyond_min_frames(self) -> None:
        config = RecognitionGlobalConfig(
            visualReadinessMinFrames=4,
            visualReadinessMinSharpness=10.0,
            visualReadinessMaxStabilityScore=0.2,
            visualReadinessTimeoutMs=1200,
            visualReadinessMinElapsedMs=0,
            visualReadinessMinReadyWindowMs=400,
        )
        checker = VisualReadinessChecker(config)

        sharp = np.zeros((120, 160, 3), dtype=np.uint8)
        sharp[:, 78:82, :] = 255
        sharp[58:62, :, :] = 255

        session = _FakeSession([sharp, sharp, sharp, sharp, sharp, sharp, sharp], frame_interval_s=0.08)
        outcome = checker.wait_until_ready(session)

        self.assertTrue(outcome.metrics.ready)
        self.assertGreaterEqual(outcome.metrics.readyWindowMsActual, 400)

    def test_wait_until_ready_rejects_stable_blur(self) -> None:
        config = RecognitionGlobalConfig(
            visualReadinessMinFrames=4,
            visualReadinessMinSharpness=100.0,
            visualReadinessMaxStabilityScore=0.2,
            visualReadinessTimeoutMs=1200,
            visualReadinessMinElapsedMs=300,
            visualReadinessMinReadyWindowMs=250,
            visualReadinessMinImprovementRatio=1.2,
            visualReadinessStableHighSharpnessMultiplier=4.0,
            visualReadinessStableBlurMaxTrend=5.0,
        )
        checker = VisualReadinessChecker(config)

        soft = np.full((120, 160, 3), 126, dtype=np.uint8)
        slightly_better = np.full((120, 160, 3), 127, dtype=np.uint8)

        session = _FakeSession(
            [soft, soft, slightly_better, slightly_better, slightly_better, slightly_better],
            frame_interval_s=0.12,
        )
        outcome = checker.wait_until_ready(session)

        self.assertFalse(outcome.metrics.ready)
        self.assertEqual(outcome.metrics.reason, "visual_not_ready_blurry")
        self.assertTrue(outcome.metrics.stableBlurRejected)
        self.assertTrue(outcome.metrics.minElapsedGatePassed)
        self.assertTrue(outcome.metrics.minReadyWindowGatePassed)

    def test_wait_until_ready_accepts_gradual_sharpness_convergence(self) -> None:
        config = RecognitionGlobalConfig(
            visualReadinessMinFrames=4,
            visualReadinessMinSharpness=10.0,
            visualReadinessMaxStabilityScore=0.2,
            visualReadinessTimeoutMs=1200,
            visualReadinessMinElapsedMs=300,
            visualReadinessMinReadyWindowMs=250,
            visualReadinessMinImprovementRatio=1.2,
            visualReadinessStableHighSharpnessMultiplier=3.0,
        )
        checker = VisualReadinessChecker(config)

        blurry = np.full((120, 160, 3), 127, dtype=np.uint8)
        medium = np.zeros((120, 160, 3), dtype=np.uint8)
        medium[:, 78:82, :] = 140
        medium[58:62, :, :] = 140
        sharp = np.zeros((120, 160, 3), dtype=np.uint8)
        sharp[:, 78:82, :] = 255
        sharp[58:62, :, :] = 255

        session = _FakeSession(
            [blurry, medium, medium, sharp, sharp, sharp, sharp],
            frame_interval_s=0.12,
        )
        outcome = checker.wait_until_ready(session)

        self.assertTrue(outcome.metrics.ready)
        self.assertGreater(outcome.metrics.sharpnessImprovementRatio or 0.0, 1.2)
        self.assertGreater(outcome.metrics.sharpnessTrend or 0.0, 0.0)
        self.assertGreaterEqual(outcome.metrics.readyWindowMsActual, 250)

    def test_wait_until_ready_accepts_initially_clear_and_stable_scene(self) -> None:
        config = RecognitionGlobalConfig(
            visualReadinessMinFrames=4,
            visualReadinessMinSharpness=100.0,
            visualReadinessMaxStabilityScore=0.2,
            visualReadinessTimeoutMs=1200,
            visualReadinessMinElapsedMs=300,
            visualReadinessMinReadyWindowMs=250,
            visualReadinessMinImprovementRatio=1.5,
            visualReadinessStableHighSharpnessMultiplier=4.0,
            visualReadinessStableBlurMaxTrend=5.0,
        )
        checker = VisualReadinessChecker(config)

        clear = np.zeros((120, 160, 3), dtype=np.uint8)
        clear[:, 76:84, :] = 220
        clear[56:64, :, :] = 220

        session = _FakeSession([clear, clear, clear, clear, clear], frame_interval_s=0.12)
        outcome = checker.wait_until_ready(session)

        self.assertTrue(outcome.metrics.ready)
        self.assertFalse(outcome.metrics.stableBlurRejected)
        self.assertTrue(outcome.metrics.minElapsedGatePassed)
        self.assertTrue(outcome.metrics.minReadyWindowGatePassed)

    def test_night_ir_converged_requires_sharpness_margin_for_borderline_frame(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="night_ir",
            visualReadinessMinSharpness=50.0,
            visualReadinessMinSharpnessMargin=8.0,
            visualReadinessRequireRobustScoreMargin=True,
            visualReadinessMaxStabilityScore=0.2,
        )
        checker = VisualReadinessChecker(config)

        self.assertFalse(
            checker._converged(
                {
                    "baselineSharpnessMean": 35.0,
                    "baselineSharpnessMin": 32.0,
                    "sharpnessMean": 54.0,
                    "sharpnessMin": 51.0,
                    "sharpnessRobustScore": 54.0,
                    "stabilityScore": 0.01,
                    "sharpnessImprovementRatio": 1.5,
                }
            )
        )

    def test_night_ir_converged_still_accepts_clearly_sharp_stable_frame(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="night_ir",
            visualReadinessMinSharpness=50.0,
            visualReadinessMinSharpnessMargin=8.0,
            visualReadinessRequireRobustScoreMargin=True,
            visualReadinessMaxStabilityScore=0.2,
        )
        checker = VisualReadinessChecker(config)

        self.assertTrue(
            checker._converged(
                {
                    "baselineSharpnessMean": 35.0,
                    "baselineSharpnessMin": 32.0,
                    "sharpnessMean": 63.0,
                    "sharpnessMin": 58.0,
                    "sharpnessRobustScore": 63.0,
                    "stabilityScore": 0.01,
                    "sharpnessImprovementRatio": 1.8,
                }
            )
        )

    def test_night_ir_uses_night_specific_post_ready_recheck(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="night_ir",
            visualReadinessPostReadyRecheckFrames=0,
            visualReadinessPostReadyRecheckWindowMs=0,
            visualReadinessNightPostReadyRecheckFrames=1,
            visualReadinessNightPostReadyRecheckWindowMs=120,
        )
        checker = VisualReadinessChecker(config)

        self.assertTrue(checker._post_ready_recheck_enabled())
        self.assertEqual(checker._active_post_ready_recheck_frames(), 1)
        self.assertEqual(checker._active_post_ready_recheck_window_ms(), 120)

    def test_post_ready_recheck_grace_allows_edge_candidate_to_finish(self) -> None:
        config = RecognitionGlobalConfig(
            visualReadinessMinFrames=2,
            visualReadinessMinSharpness=10.0,
            visualReadinessMinSharpCellRatio=0.2,
            visualReadinessMaxStabilityScore=0.2,
            visualReadinessTimeoutMs=500,
            visualReadinessMinElapsedMs=0,
            visualReadinessMinObserveMs=0,
            visualReadinessMinReadyWindowMs=0,
            visualReadinessPostReadyRecheckFrames=0,
            visualReadinessPostReadyRecheckWindowMs=180,
            visualReadinessPostReadyRecheckGraceMs=220,
        )
        checker = VisualReadinessChecker(config)

        sharp = np.zeros((120, 160, 3), dtype=np.uint8)
        sharp[:, 78:82, :] = 255
        sharp[58:62, :, :] = 255
        session = _FakeSession([sharp, sharp, sharp, sharp], frame_interval_s=0.17)

        outcome = checker.wait_until_ready(session)

        self.assertTrue(outcome.metrics.ready)
        self.assertEqual(outcome.metrics.reason, "visual_ready")
        self.assertTrue(outcome.metrics.postReadyRecheckPassed)
        self.assertGreaterEqual(outcome.metrics.postReadyRecheckWindowMsActual, 180)

    def test_converged_does_not_bypass_improvement_after_blurry_start(self) -> None:
        config = RecognitionGlobalConfig(
            visualReadinessMinFrames=4,
            visualReadinessMinSharpness=100.0,
            visualReadinessMaxStabilityScore=0.2,
            visualReadinessMinImprovementRatio=1.25,
            visualReadinessStableHighSharpnessMultiplier=2.0,
        )
        checker = VisualReadinessChecker(config)

        self.assertFalse(
            checker._converged(
                {
                    "baselineSharpnessMean": 95.0,
                    "baselineSharpnessMin": 92.0,
                    "sharpnessMean": 108.0,
                    "sharpnessMin": 101.0,
                    "stabilityScore": 0.01,
                    "sharpnessImprovementRatio": 1.13,
                }
            )
        )

    def test_wait_until_ready_rejects_single_high_contrast_patch_when_whole_roi_is_blurry(self) -> None:
        config = RecognitionGlobalConfig(
            visualReadinessMinFrames=3,
            visualReadinessMinSharpness=100.0,
            visualReadinessMinSharpCellRatio=0.45,
            visualReadinessMaxStabilityScore=0.2,
            visualReadinessTimeoutMs=900,
            visualReadinessMinElapsedMs=0,
            visualReadinessMinReadyWindowMs=0,
        )
        checker = VisualReadinessChecker(config)

        single_edge = _make_single_edge_frame()
        session = _FakeSession([single_edge, single_edge, single_edge, single_edge], frame_interval_s=0.12)
        outcome = checker.wait_until_ready(session)

        self.assertFalse(outcome.metrics.ready)
        self.assertEqual(outcome.metrics.reason, "visual_not_ready_blurry")
        self.assertLess(outcome.metrics.sharpCellRatio or 0.0, config.visualReadinessMinSharpCellRatio)

    def test_wait_until_ready_continues_observing_stable_blur_until_timeout(self) -> None:
        config = RecognitionGlobalConfig(
            visualReadinessMinFrames=4,
            visualReadinessMinSharpness=100.0,
            visualReadinessMaxStabilityScore=0.2,
            visualReadinessTimeoutMs=1800,
            visualReadinessMinElapsedMs=300,
            visualReadinessMinObserveMs=900,
            visualReadinessMinReadyWindowMs=250,
            visualReadinessMinImprovementRatio=1.2,
            visualReadinessStableHighSharpnessMultiplier=4.0,
            visualReadinessStableBlurMaxTrend=5.0,
        )
        checker = VisualReadinessChecker(config)

        blurry = np.full((120, 160, 3), 127, dtype=np.uint8)
        session = _FakeSession([blurry for _ in range(16)], frame_interval_s=0.15)
        outcome = checker.wait_until_ready(session)

        self.assertFalse(outcome.metrics.ready)
        self.assertEqual(outcome.metrics.reason, "visual_not_ready_blurry")
        self.assertTrue(outcome.metrics.stableBlurRejected)
        self.assertGreaterEqual(outcome.metrics.elapsedMs, 1500)

    def test_wait_until_ready_honors_day_min_observe_before_passing(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="day_visible",
            visualReadinessMinFrames=3,
            visualReadinessMinSharpness=50.0,
            visualReadinessMinSharpCellRatio=0.45,
            visualReadinessMaxStabilityScore=0.2,
            visualReadinessTimeoutMs=2200,
            visualReadinessMinElapsedMs=0,
            visualReadinessMinObserveMs=1000,
            visualReadinessMinReadyWindowMs=0,
        )
        checker = VisualReadinessChecker(config)

        sharp = _make_checkerboard_frame()
        session = _FakeSession([sharp for _ in range(12)], frame_interval_s=0.12)
        outcome = checker.wait_until_ready(session)

        self.assertTrue(outcome.metrics.ready)
        self.assertTrue(outcome.metrics.minObserveGatePassed)
        self.assertGreaterEqual(outcome.metrics.elapsedMs, 1000)

    def test_wait_until_ready_rechecks_after_candidate_and_recovers_if_blur_returns(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="day_visible",
            visualReadinessMinFrames=3,
            visualReadinessMinSharpness=50.0,
            visualReadinessMinSharpCellRatio=0.45,
            visualReadinessMaxStabilityScore=0.2,
            visualReadinessTimeoutMs=2200,
            visualReadinessMinElapsedMs=0,
            visualReadinessMinObserveMs=0,
            visualReadinessMinReadyWindowMs=0,
            visualReadinessPostReadyRecheckFrames=2,
            visualReadinessPostReadyRecheckWindowMs=150,
        )
        checker = VisualReadinessChecker(config)

        sharp = _make_checkerboard_frame()
        blur = np.full((120, 160, 3), 127, dtype=np.uint8)
        session = _FakeSession(
            [sharp, sharp, sharp, blur, sharp, sharp, sharp, sharp, sharp, sharp],
            frame_interval_s=0.12,
        )
        outcome = checker.wait_until_ready(session)

        self.assertTrue(outcome.metrics.ready)
        self.assertTrue(outcome.metrics.continuedAfterCandidateReject)
        self.assertTrue(outcome.metrics.postReadyRecheckPassed)
        self.assertEqual(outcome.metrics.postReadyRecheckReason, "visual_ready_recheck_passed")
        self.assertIsNotNone(outcome.confirmFrameIndex)


class StaticBrightSuppressionTests(unittest.TestCase):
    def test_static_bright_interference_gate_suppresses_false_positive(self) -> None:
        config = RecognitionGlobalConfig(sceneMode="day_visible", staticBrightSuppressionEnabled=True)
        resolver = TemporalVoteResolver(config)
        summary = RecognitionScoreSummary(
            sceneMode="day_visible",
            sampledFrameCount=20,
            framePassCount=15,
            overflowFrameCount=0,
            globalMotionExceeded=False,
            largestBrightComponentRatio=0.2,
            centerBrightCoverage=0.2,
            highlightMotionMean=0.001,
            temporalAreaVariance=0.005,
            temporalShapeVariance=0.01,
        )

        decision = resolver.resolve(summary)

        self.assertEqual(decision.visualState, "no_splash")
        self.assertEqual(decision.reason, "static_bright_interference_gate")
        self.assertTrue(decision.staticBrightInterferenceSuppressed)

    def test_night_true_has_splash_is_not_killed_by_static_bright_gate(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="night_ir",
            staticBrightSuppressionEnabled=True,
            staticBrightMiddleBandSuppressionEnabled=True,
            sequenceVoteThreshold=0.6,
        )
        resolver = TemporalVoteResolver(config)
        summary = RecognitionScoreSummary(
            sceneMode="night_ir",
            sampledFrameCount=20,
            framePassCount=19,
            overflowFrameCount=0,
            globalMotionExceeded=False,
            largestBrightComponentRatio=0.42,
            centerBrightCoverage=0.24,
            highlightMotionMean=0.001,
            temporalAreaVariance=0.005,
            temporalShapeVariance=0.01,
        )

        decision = resolver.resolve(summary)

        self.assertEqual(decision.visualState, "has_splash")
        self.assertEqual(decision.reason, "pass_ratio_high")
        self.assertFalse(decision.staticBrightInterferenceSuppressed)

    def test_day_middle_band_static_bright_interference_falls_back_to_no_splash(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="day_visible",
            staticBrightSuppressionEnabled=True,
            staticBrightMiddleBandSuppressionEnabled=True,
            staticBrightMiddleBandMinPassRatio=0.45,
            sequenceVoteThreshold=0.6,
        )
        resolver = TemporalVoteResolver(config)
        summary = RecognitionScoreSummary(
            sceneMode="day_visible",
            sampledFrameCount=20,
            framePassCount=10,
            overflowFrameCount=0,
            globalMotionExceeded=False,
            largestBrightComponentRatio=0.18,
            centerBrightCoverage=0.16,
            highlightMotionMean=0.001,
            temporalAreaVariance=0.01,
            temporalShapeVariance=0.03,
        )

        decision = resolver.resolve(summary)

        self.assertEqual(decision.visualState, "no_splash")
        self.assertEqual(decision.reason, "static_bright_interference_middle_band_gate")
        self.assertTrue(decision.staticBrightInterferenceSuppressed)

    def test_night_middle_band_static_bright_interference_remains_undetermined(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="night_ir",
            staticBrightSuppressionEnabled=True,
            staticBrightMiddleBandSuppressionEnabled=True,
            staticBrightMiddleBandMinPassRatio=0.45,
            sequenceVoteThreshold=0.6,
        )
        resolver = TemporalVoteResolver(config)
        summary = RecognitionScoreSummary(
            sceneMode="night_ir",
            sampledFrameCount=20,
            framePassCount=10,
            overflowFrameCount=0,
            globalMotionExceeded=False,
            largestBrightComponentRatio=0.18,
            centerBrightCoverage=0.16,
            highlightMotionMean=0.001,
            temporalAreaVariance=0.01,
            temporalShapeVariance=0.03,
        )

        decision = resolver.resolve(summary)

        self.assertEqual(decision.visualState, "undetermined")
        self.assertEqual(decision.reason, "pass_ratio_middle_band")

    def test_day_true_has_splash_high_motion_is_not_killed_by_middle_band_static_gate(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="day_visible",
            staticBrightSuppressionEnabled=True,
            staticBrightMiddleBandSuppressionEnabled=True,
            staticBrightMiddleBandMinPassRatio=0.45,
            sequenceVoteThreshold=0.6,
        )
        resolver = TemporalVoteResolver(config)
        summary = RecognitionScoreSummary(
            sceneMode="day_visible",
            sampledFrameCount=20,
            framePassCount=10,
            overflowFrameCount=0,
            globalMotionExceeded=False,
            largestBrightComponentRatio=0.18,
            centerBrightCoverage=0.16,
            highlightMotionMean=0.03,
            temporalAreaVariance=0.08,
            temporalShapeVariance=0.2,
        )

        decision = resolver.resolve(summary)

        self.assertEqual(decision.visualState, "undetermined")
        self.assertEqual(decision.reason, "pass_ratio_middle_band")


class NightGapFillThresholdTests(unittest.TestCase):
    @staticmethod
    def _night_feature(frame_index: int, *, gap_fill_ratio: float) -> FrameFeature:
        return FrameFeature(
            frameIndex=frame_index,
            brightThreshold=150.0,
            roiBrightnessQ99=180.0,
            roiBrightnessMax=188.0,
            localResidualMotion=0.03,
            dynamicAreaRatio=0.022,
            highlightDisturbance=0.02,
            largestBrightComponentRatio=0.45,
            brightComponentCount=1,
            fragmentationScore=0.08,
            centerBrightCoverage=0.83,
            upperHalfBrightRatio=0.48,
            lowerHalfBrightRatio=0.42,
            verticalSpreadRatio=1.0,
            gapFillRatio=gap_fill_ratio,
            temporalAreaVariance=0.05,
            temporalShapeVariance=0.25,
        )

    @staticmethod
    def _dummy_sequence(frame_count: int) -> SampledSequence:
        return SampledSequence(
            streamType="flv",
            streamUrl="fake://stream",
            frames=np.zeros((frame_count, 4, 4, 3), dtype=np.uint8),
            frameTimestampsMs=[index * 100 for index in range(frame_count)],
            targetFrameCount=frame_count,
            sampledFrameCount=frame_count,
            configuredSampleFps=10.0,
            actualSampleFps=10.0,
            configuredSampleDurationMs=max(0, (frame_count - 1) * 100),
            actualSampleDurationMs=max(0, (frame_count - 1) * 100),
            frameWidth=4,
            frameHeight=4,
        )

    def test_night_ir_fragmented_true_splash_passes_with_gap_fill_076(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="night_ir",
            sequenceVoteThreshold=0.6,
            framePassThreshold=0.3,
            hardGateMinGapFillRatio=0.76,
        )
        service = RunOnceService(global_config=config, raw_config={"sceneMode": "night_ir"})
        features = [self._night_feature(index, gap_fill_ratio=0.768) for index in range(19)] + [
            self._night_feature(index + 19, gap_fill_ratio=0.74) for index in range(1)
        ]
        frame_scores = WeightedFrameScorer(config).score(features)
        score_summary = service._score_summary(
            effective_config=config,
            sequence=self._dummy_sequence(len(features)),
            frame_features=features,
            frame_scores=frame_scores,
        )

        decision = TemporalVoteResolver(config).resolve(score_summary)

        self.assertEqual(score_summary.gapFillPassCount, 19)
        self.assertEqual(score_summary.hardGateMinGapFillRatioConfigured, 0.76)
        self.assertEqual(score_summary.framePassCount, 19)
        self.assertEqual(decision.visualState, "has_splash")
        self.assertEqual(decision.reason, "pass_ratio_high")

    def test_night_ir_no_splash_stays_blocked_with_gap_fill_076(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="night_ir",
            sequenceVoteThreshold=0.6,
            framePassThreshold=0.3,
            hardGateMinGapFillRatio=0.76,
        )
        service = RunOnceService(global_config=config, raw_config={"sceneMode": "night_ir"})
        features = [self._night_feature(index, gap_fill_ratio=0.592) for index in range(20)]
        frame_scores = WeightedFrameScorer(config).score(features)
        score_summary = service._score_summary(
            effective_config=config,
            sequence=self._dummy_sequence(len(features)),
            frame_features=features,
            frame_scores=frame_scores,
        )

        decision = TemporalVoteResolver(config).resolve(score_summary)

        self.assertEqual(score_summary.gapFillPassCount, 0)
        self.assertEqual(score_summary.framePassCount, 0)
        self.assertEqual(decision.visualState, "no_splash")
        self.assertEqual(decision.reason, "pass_ratio_low")

    def test_night_ir_boundary_threshold_0760_is_first_temporal_vote_pass(self) -> None:
        features = [self._night_feature(index, gap_fill_ratio=0.768) for index in range(11)] + [
            self._night_feature(11, gap_fill_ratio=0.762)
        ] + [
            self._night_feature(index + 12, gap_fill_ratio=0.74) for index in range(8)
        ]

        config_0765 = RecognitionGlobalConfig(
            sceneMode="night_ir",
            sequenceVoteThreshold=0.6,
            framePassThreshold=0.3,
            hardGateMinGapFillRatio=0.765,
        )
        service_0765 = RunOnceService(global_config=config_0765, raw_config={"sceneMode": "night_ir"})
        frame_scores_0765 = WeightedFrameScorer(config_0765).score(features)
        score_summary_0765 = service_0765._score_summary(
            effective_config=config_0765,
            sequence=self._dummy_sequence(len(features)),
            frame_features=features,
            frame_scores=frame_scores_0765,
        )
        decision_0765 = TemporalVoteResolver(config_0765).resolve(score_summary_0765)

        config_0760 = RecognitionGlobalConfig(
            sceneMode="night_ir",
            sequenceVoteThreshold=0.6,
            framePassThreshold=0.3,
            hardGateMinGapFillRatio=0.76,
        )
        service_0760 = RunOnceService(global_config=config_0760, raw_config={"sceneMode": "night_ir"})
        frame_scores_0760 = WeightedFrameScorer(config_0760).score(features)
        score_summary_0760 = service_0760._score_summary(
            effective_config=config_0760,
            sequence=self._dummy_sequence(len(features)),
            frame_features=features,
            frame_scores=frame_scores_0760,
        )
        decision_0760 = TemporalVoteResolver(config_0760).resolve(score_summary_0760)

        self.assertEqual(score_summary_0765.gapFillPassCount, 11)
        self.assertEqual(score_summary_0765.framePassCount, 11)
        self.assertEqual(decision_0765.visualState, "undetermined")
        self.assertEqual(decision_0765.reason, "pass_ratio_middle_band")
        self.assertEqual(score_summary_0760.gapFillPassCount, 12)
        self.assertEqual(score_summary_0760.framePassCount, 12)
        self.assertEqual(decision_0760.visualState, "has_splash")
        self.assertEqual(decision_0760.reason, "pass_ratio_high")


class NightGapFillScannerTests(unittest.TestCase):
    def test_scanner_evaluates_each_case_with_its_snapshot_config(self) -> None:
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 4, "height": 4},
        )
        case = gap_fill_scan_module.ReplayCase(
            runId="run",
            roundIndex=1,
            expectedVisualState="no_splash",
            currentVisualState="no_splash",
            currentExecutionResult="success",
            sequencePath=Path("sequence.npz"),
            configSnapshotPath=Path("recognition-config.snapshot.json"),
            target=target,
            sampledFrameCount=20,
            targetFrameCount=20,
            configuredSampleFps=10.0,
            actualSampleFps=10.0,
            configuredSampleDurationMs=1900,
            actualSampleDurationMs=1900,
        )
        features = [
            NightGapFillThresholdTests._night_feature(index, gap_fill_ratio=0.80)
            for index in range(20)
        ]
        cached_case = gap_fill_scan_module.CachedReplayCase(
            case=case,
            sequence=NightGapFillThresholdTests._dummy_sequence(20),
            baseRawConfig={
                "sceneMode": "night_ir",
                "sequenceVoteThreshold": 0.6,
                "sequenceFrameCount": 20,
                "framePassThreshold": 0.3,
                "hardGateMinLargestBrightComponentRatio": 0.5,
                "hardGateMinCenterBrightCoverage": 0.2,
                "hardGateMinVerticalSpreadRatio": 0.1,
                "hardGateMinContinuousBrightRatio": 0.1,
                "hardGateMinLocalMotion": 0.0,
                "hardGateMinDynamicAreaRatio": 0.0,
                "hardGateMinHighlightMotion": 0.0,
                "hardGateMinGapFillRatio": 0.9,
                "hardGateMinTemporalAreaVariance": 0.0,
                "hardGateMinTemporalShapeVariance": 0.0,
            },
            alignedSequence=AlignedSequence(
                alignedFrames=np.zeros((20, 4, 4, 3), dtype=np.uint8),
                globalShifts=[(0, 0) for _ in range(20)],
                shiftMagnitudes=[0.0 for _ in range(20)],
                appliedGlobalShifts=[(0, 0) for _ in range(20)],
                appliedShiftMagnitudes=[0.0 for _ in range(20)],
                overflowFlags=[False for _ in range(20)],
                alignmentApplied=False,
            ),
            frameFeatures=features,
            preAlignmentRoiMotion=0.0,
            postAlignmentRoiMotion=0.0,
        )

        report = gap_fill_scan_module._evaluate_threshold([cached_case], threshold=0.76)
        case_result = report["cases"][0]

        self.assertEqual(case_result["configSnapshotPath"], "recognition-config.snapshot.json")
        self.assertEqual(case_result["gapFillPassCount"], 20)
        self.assertEqual(case_result["hardGatePassCount"], 0)
        self.assertEqual(case_result["candidateVisualState"], "no_splash")


class VisualNotReadyMappingTests(unittest.TestCase):
    def test_visual_not_ready_reason_maps_to_timeout_result(self) -> None:
        self.assertEqual(
            RunOnceService._visual_not_ready_execution_result("visual_not_ready_timeout"),
            "visual_not_ready_timeout",
        )

    def test_visual_not_ready_min_elapsed_maps_to_timeout_result(self) -> None:
        self.assertEqual(
            RunOnceService._visual_not_ready_execution_result("visual_not_ready_min_elapsed"),
            "visual_not_ready_timeout",
        )

    def test_visual_not_ready_ready_window_short_maps_to_timeout_result(self) -> None:
        self.assertEqual(
            RunOnceService._visual_not_ready_execution_result("visual_not_ready_ready_window_short"),
            "visual_not_ready_timeout",
        )

    def test_visual_not_ready_blurry_maps_to_blurry_before_detection_result(self) -> None:
        self.assertEqual(
            RunOnceService._visual_not_ready_execution_result("visual_not_ready_blurry"),
            "visual_blurry_before_detection",
        )

    def test_visual_not_ready_blurry_and_unstable_maps_to_blurry_before_detection_result(self) -> None:
        self.assertEqual(
            RunOnceService._visual_not_ready_execution_result("visual_not_ready_blurry_and_unstable"),
            "visual_blurry_before_detection",
        )

    def test_scene_mode_probe_incomplete_maps_to_dedicated_execution_result(self) -> None:
        self.assertEqual(
            RunOnceService._scene_mode_execution_result("scene_mode_probe_incomplete"),
            "scene_mode_probe_incomplete",
        )

    def test_failure_result_with_auto_scene_mode_keeps_score_summary_scene_mode_none(self) -> None:
        config = RecognitionGlobalConfig(sceneMode="auto")
        service = RunOnceService(
            global_config=config,
            raw_config={
                "sceneMode": "auto",
                "dayVisible": {"algorithmVersion": "test-day"},
                "nightIr": {"algorithmVersion": "test-night"},
            },
        )
        result = service._failure_result(
            config_path=Path("demo.json"),
            requested_preset_index=1,
            execution_result="visual_not_ready_timeout",
            message="test failure",
            timing=RecognitionTiming(),
            started_at=monotonic(),
        )

        self.assertEqual(result.sceneMode, "auto")
        self.assertIsNone(result.effectiveSceneMode)
        self.assertIsNone(result.scoreSummary.sceneMode)


class RecognitionConfigOverrideTests(unittest.TestCase):
    def test_build_recognition_config_applies_day_observe_and_recheck_overrides(self) -> None:
        config = build_recognition_config(
            {
                "sceneMode": "auto",
                "visualReadinessMinObserveMs": 0,
                "visualReadinessPostReadyRecheckFrames": 0,
                "visualReadinessPostReadyRecheckWindowMs": 0,
                "dayVisible": {
                    "visualReadinessMinObserveMs": 1200,
                    "visualReadinessPostReadyRecheckFrames": 2,
                    "visualReadinessPostReadyRecheckWindowMs": 240,
                },
                "nightIr": {
                    "visualReadinessMinObserveMs": 0,
                    "visualReadinessPostReadyRecheckFrames": 0,
                    "visualReadinessPostReadyRecheckWindowMs": 0,
                },
            },
            "day_visible",
        )

        self.assertEqual(config.sceneMode, "day_visible")
        self.assertEqual(config.visualReadinessMinObserveMs, 1200)
        self.assertEqual(config.visualReadinessPostReadyRecheckFrames, 2)
        self.assertEqual(config.visualReadinessPostReadyRecheckWindowMs, 240)

    def test_build_recognition_config_applies_night_readiness_overrides(self) -> None:
        config = build_recognition_config(
            {
                "sceneMode": "auto",
                "visualReadinessMinSharpness": 300.0,
                "visualReadinessMinReadyWindowMs": 400,
                "visualReadinessMinObserveMs": 1200,
                "visualReadinessPostReadyRecheckFrames": 2,
                "visualReadinessPostReadyRecheckWindowMs": 240,
                "visualReadinessNightPostReadyRecheckFrames": 0,
                "visualReadinessNightPostReadyRecheckWindowMs": 0,
                "visualReadinessMinImprovementRatio": 1.2,
                "visualReadinessStableHighSharpnessMultiplier": 2.0,
                "visualReadinessStableBlurMaxTrend": 40.0,
                "nightIr": {
                    "visualReadinessMinSharpness": 50.0,
                    "visualReadinessMinReadyWindowMs": 280,
                    "visualReadinessMinObserveMs": 0,
                    "visualReadinessPostReadyRecheckFrames": 0,
                    "visualReadinessPostReadyRecheckWindowMs": 0,
                    "visualReadinessNightPostReadyRecheckFrames": 2,
                    "visualReadinessNightPostReadyRecheckWindowMs": 180,
                    "visualReadinessMinImprovementRatio": 1.08,
                    "visualReadinessStableHighSharpnessMultiplier": 1.4,
                    "visualReadinessStableBlurMaxTrend": 18.0,
                },
            },
            "night_ir",
        )

        self.assertEqual(config.sceneMode, "night_ir")
        self.assertEqual(config.visualReadinessMinSharpness, 50.0)
        self.assertEqual(config.visualReadinessMinReadyWindowMs, 280)
        self.assertEqual(config.visualReadinessMinObserveMs, 0)
        self.assertEqual(config.visualReadinessPostReadyRecheckFrames, 0)
        self.assertEqual(config.visualReadinessPostReadyRecheckWindowMs, 0)
        self.assertEqual(config.visualReadinessNightPostReadyRecheckFrames, 2)
        self.assertEqual(config.visualReadinessNightPostReadyRecheckWindowMs, 180)
        self.assertEqual(config.visualReadinessMinImprovementRatio, 1.08)
        self.assertEqual(config.visualReadinessStableHighSharpnessMultiplier, 1.4)
        self.assertEqual(config.visualReadinessStableBlurMaxTrend, 18.0)

    def test_build_recognition_config_applies_night_sample_quality_overrides(self) -> None:
        config = build_recognition_config(
            {
                "sceneMode": "auto",
                "sampleQualityTimeoutMs": 4500,
                "sampleQualityMaxRecoveries": 2,
                "dayVisible": {
                    "sampleQualityTimeoutMs": 5200,
                    "sampleQualityMaxRecoveries": 3,
                },
                "nightIr": {
                    "sampleQualityTimeoutMs": 5700,
                    "sampleQualityMaxRecoveries": 3,
                },
            },
            "night_ir",
        )

        self.assertEqual(config.sceneMode, "night_ir")
        self.assertEqual(config.sampleQualityTimeoutMs, 5700)
        self.assertEqual(config.sampleQualityMaxRecoveries, 3)

    def test_build_recognition_config_keeps_day_and_night_sample_quality_overrides_separate(self) -> None:
        raw_config = {
            "sceneMode": "auto",
            "sampleQualityTimeoutMs": 4500,
            "sampleQualityMaxRecoveries": 2,
            "dayVisible": {
                "sampleQualityTimeoutMs": 5200,
                "sampleQualityMaxRecoveries": 3,
            },
            "nightIr": {
                "sampleQualityTimeoutMs": 5700,
                "sampleQualityMaxRecoveries": 3,
            },
        }

        day_config = build_recognition_config(raw_config, "day_visible")
        night_config = build_recognition_config(raw_config, "night_ir")

        self.assertEqual(day_config.sampleQualityTimeoutMs, 5200)
        self.assertEqual(day_config.sampleQualityMaxRecoveries, 3)
        self.assertEqual(night_config.sampleQualityTimeoutMs, 5700)
        self.assertEqual(night_config.sampleQualityMaxRecoveries, 3)

    def test_build_recognition_config_applies_day_sample_quality_overrides(self) -> None:
        config = build_recognition_config(
            {
                "sceneMode": "auto",
                "sampleQualityTimeoutMs": 4500,
                "sampleQualityMaxRecoveries": 2,
                "dayVisible": {
                    "sampleQualityTimeoutMs": 5200,
                    "sampleQualityMaxRecoveries": 3,
                },
                "nightIr": {
                    "sampleQualityTimeoutMs": 4500,
                    "sampleQualityMaxRecoveries": 2,
                },
            },
            "day_visible",
        )

        self.assertEqual(config.sceneMode, "day_visible")
        self.assertEqual(config.sampleQualityTimeoutMs, 5200)
        self.assertEqual(config.sampleQualityMaxRecoveries, 3)

    def test_build_recognition_config_ignores_nested_day_twilight_until_profile_is_selected(self) -> None:
        config = build_recognition_config(
            {
                "sceneMode": "auto",
                "sampleQualityTimeoutMs": 4500,
                "sampleQualityMaxRecoveries": 2,
                "dayVisible": {
                    "sampleQualityTimeoutMs": 5200,
                    "sampleQualityMaxRecoveries": 3,
                    "twilight": {
                        "sampleQualityTimeoutMs": 6000,
                        "sampleQualityMaxRecoveries": 4,
                    },
                },
            },
            "day_visible",
        )

        self.assertEqual(config.sampleQualityTimeoutMs, 5200)
        self.assertEqual(config.sampleQualityMaxRecoveries, 3)


class RunOnceVisualReadinessTests(unittest.TestCase):
    @staticmethod
    def _scene_mode_diagnostics(
        *,
        brightness_mean: float,
        colorfulness_mean: float,
        saturation_p90: float,
        day_visible_score: float,
        night_ir_score: float,
        score_margin: float,
        classification: str = "day_visible",
        suggested_mode: str = "day_visible",
    ) -> SceneModeDiagnostics:
        return SceneModeDiagnostics(
            classification=classification,
            suggestedMode=suggested_mode,
            inspectedFrameCount=4,
            centerCropRatio=0.6,
            colorfulnessMean=colorfulness_mean,
            saturationP90=saturation_p90,
            channelDeltaMean=9.0,
            channelCorrelation=0.9,
            brightnessMean=brightness_mean,
            brightnessStd=22.0,
            dayVisibleScore=day_visible_score,
            nightIrScore=night_ir_score,
            scoreMargin=score_margin,
        )

    @classmethod
    def _scene_mode_stability_result(
        cls,
        *,
        stable: bool,
        classification: str,
        suggested_mode: str,
        transition_timeout: bool = False,
        relock_count: int = 0,
        relock_reason: str | None = None,
    ) -> SceneModeStabilityResult:
        diagnostics = cls._scene_mode_diagnostics(
            brightness_mean=92.0 if suggested_mode == "day_visible" else 44.0,
            colorfulness_mean=16.0 if suggested_mode == "day_visible" else 1.5,
            saturation_p90=0.12 if suggested_mode == "day_visible" else 0.01,
            day_visible_score=0.88 if suggested_mode == "day_visible" else 0.22,
            night_ir_score=0.2 if suggested_mode == "day_visible" else 0.9,
            score_margin=0.68,
            classification=classification,
            suggested_mode=suggested_mode,
        )
        decision = SceneModeDecision(
            classification=classification,
            suggestedMode=suggested_mode,
            confidence=0.9,
            reason=(
                "visible_colorfulness_supports_day_visible"
                if suggested_mode == "day_visible"
                else "low_color_delta_and_high_channel_correlation_support_night_ir"
            ),
            diagnostics=diagnostics,
        )
        frame = np.full((12, 12, 3), 64 if suggested_mode == "night_ir" else 220, dtype=np.uint8)
        return SceneModeStabilityResult(
            enabled=True,
            stable=stable,
            initialMode=suggested_mode,
            finalMode=suggested_mode,
            elapsedMs=420,
            windowCount=2,
            transitionObserved=transition_timeout,
            relockCount=relock_count,
            relockReason=relock_reason,
            transitionTimeout=transition_timeout,
            reason="scene_mode_stable" if stable else "scene_mode_transition_timeout",
            finalDecision=decision,
            streamType="flv",
            streamUrl="fake://stream",
            startFrame=frame.copy(),
            settledFrame=frame.copy(),
            windows=[
                SceneModeStabilityWindow(decision=decision, startFrame=frame.copy(), endFrame=frame.copy(), frameCount=4),
                SceneModeStabilityWindow(decision=decision, startFrame=frame.copy(), endFrame=frame.copy(), frameCount=4),
            ],
            observedFrames=[frame.copy(), frame.copy()],
            frameTimestampsMs=[0, 100],
        )

    def test_scene_mode_stability_guard_waits_for_two_consistent_windows_before_readiness(self) -> None:
        stable_day = np.zeros((24, 24, 3), dtype=np.uint8)
        stable_day[..., 1] = 180
        stable_day[..., 2] = 240
        session = _FakeSession(
            [stable_day, stable_day, stable_day, stable_day, stable_day, stable_day, stable_day, stable_day],
            frame_interval_s=0.1,
        )
        config = RecognitionGlobalConfig(
            sceneMode="auto",
            sceneAutoFrameCount=4,
            sceneModeStabilityFramesPerWindow=4,
            sceneModeStabilityRequiredWindows=2,
            sceneModeStabilityTimeoutMs=1200,
        )
        guard = SceneModeStabilityGuard(config)

        result = guard.observe(session)

        self.assertTrue(result.stable)
        self.assertEqual(result.initialMode, "day_visible")
        self.assertEqual(result.finalMode, "day_visible")
        self.assertEqual(result.windowCount, 2)
        self.assertFalse(result.transitionTimeout)

    def test_scene_mode_stability_guard_times_out_during_mode_switch(self) -> None:
        day_frame = np.zeros((24, 24, 3), dtype=np.uint8)
        day_frame[..., 1] = 180
        day_frame[..., 2] = 240
        ir_frame = np.full((24, 24, 3), 120, dtype=np.uint8)
        session = _FakeSession(
            [day_frame, day_frame, day_frame, day_frame, ir_frame, ir_frame, ir_frame, ir_frame],
            frame_interval_s=0.1,
        )
        config = RecognitionGlobalConfig(
            sceneMode="auto",
            sceneAutoFrameCount=4,
            sceneModeStabilityFramesPerWindow=4,
            sceneModeStabilityRequiredWindows=2,
            sceneModeStabilityTimeoutMs=900,
        )
        guard = SceneModeStabilityGuard(config)

        result = guard.observe(session)

        self.assertFalse(result.stable)
        self.assertTrue(result.transitionTimeout)
        self.assertTrue(result.transitionObserved)
        self.assertEqual(result.reason, "scene_mode_transition_timeout")

    def test_scene_mode_stability_guard_rejects_incomplete_windows(self) -> None:
        stable_day = np.zeros((24, 24, 3), dtype=np.uint8)
        stable_day[..., 1] = 180
        stable_day[..., 2] = 240
        session = _FakeSession([stable_day, stable_day], frame_interval_s=0.1)
        config = RecognitionGlobalConfig(
            sceneMode="auto",
            sceneAutoFrameCount=4,
            sceneModeStabilityFramesPerWindow=4,
            sceneModeStabilityRequiredWindows=2,
            sceneModeStabilityTimeoutMs=1200,
        )
        guard = SceneModeStabilityGuard(config)

        result = guard.observe(session)

        self.assertFalse(result.stable)
        self.assertFalse(result.transitionTimeout)
        self.assertEqual(result.reason, "scene_mode_probe_incomplete")
        self.assertEqual(result.windowCount, 0)
        self.assertEqual(len(result.observedFrames), 2)

    def test_combined_debug_key_frames_keep_scene_mode_stability_separate_from_readiness(self) -> None:
        service = RunOnceService(global_config=RecognitionGlobalConfig(sceneMode="auto"))
        stability_frame = np.zeros((8, 8, 3), dtype=np.uint8)
        readiness_frame = np.full((8, 8, 3), 255, dtype=np.uint8)
        readiness_outcome = VisualReadinessOutcome(
            metrics=VisualReadinessMetrics(ready=True, reason="visual_ready"),
            frames=[readiness_frame.copy()],
            frameTimestampsMs=[0],
            frameCapturedAts=[10.0],
            streamType="flv",
            streamUrl="fake://stream",
            readyFrameIndex=0,
            confirmFrameIndex=0,
        )

        key_frames = service._combined_debug_key_frames(
            scene_mode_stability_result=__import__("types").SimpleNamespace(
                startFrame=stability_frame.copy(),
                settledFrame=stability_frame.copy(),
            ),
            readiness_outcome=readiness_outcome,
        )

        self.assertIsNotNone(key_frames)
        self.assertTrue(np.array_equal(key_frames["sceneModeStabilityStartFrame"], stability_frame))
        self.assertTrue(np.array_equal(key_frames["sceneModeStabilitySettledFrame"], stability_frame))
        self.assertTrue(np.array_equal(key_frames["visualReadinessStartFrame"], readiness_frame))
        self.assertFalse(
            np.array_equal(key_frames["sceneModeStabilityStartFrame"], key_frames["visualReadinessStartFrame"])
        )

    def test_run_once_returns_scene_mode_transition_timeout_before_readiness(self) -> None:
        calibration = types.SimpleNamespace(
            deviceId="device",
            channelId="0",
            targetId="target",
            targetName="target",
            presetIndex=1,
            presetName="preset",
            roi={"x": 0, "y": 0, "width": 16, "height": 16},
            focusAnchorRoi={"x": 4, "y": 4, "width": 8, "height": 8},
            notes="",
            snapshotPath="snapshots/demo.png",
            snapshotUrl="/artifacts/snapshots/demo.png",
            updatedAt="2026-01-01T00:00:00+00:00",
        )
        config = RecognitionGlobalConfig(
            sceneMode="auto",
            visualReadinessEnabled=True,
            streamStartupFreshnessEnabled=False,
        )
        service = RunOnceService(
            global_config=config,
            raw_config={
                "sceneMode": "auto",
                "dayVisible": {"algorithmVersion": "test-day"},
                "nightIr": {"algorithmVersion": "test-night"},
            },
        )
        fake_session = _FakeSession([np.zeros((24, 24, 3), dtype=np.uint8)] * 8, frame_interval_s=0.1)
        service.sampler.open_session = Mock(return_value=fake_session)
        service.replay_store.persist_async = Mock(
            return_value=(
                {"metadataPath": "replays/replay-metadata.json"},
                ReplaySaveState(status="pending", statusPath="replays/replay-save-status.json", message="scheduled"),
            )
        )
        service.scene_mode_stability_guard.observe = Mock(
            return_value=self._scene_mode_stability_result(
                stable=False,
                classification="ambiguous",
                suggested_mode="night_ir",
                transition_timeout=True,
            )
        )

        fake_preset_module = types.ModuleType("app.services.dahua_preset_service")
        fake_preset_module.preset_service = types.SimpleNamespace(turn_preset=Mock(return_value=None))
        fake_sign_module = types.ModuleType("app.utils.request_sign_adapter")
        fake_sign_module.DahuaApiError = type("DahuaApiError", (Exception,), {})

        with patch("inspector.run_once_service.storage_service.load_path", return_value=calibration):
            with patch("app.config.Settings.is_dahua_configured", new_callable=PropertyMock, return_value=True):
                with patch.dict(
                    sys.modules,
                    {
                        "app.services.dahua_preset_service": fake_preset_module,
                        "app.utils.request_sign_adapter": fake_sign_module,
                    },
                ):
                    result = service.run(config_path=Path("demo.json"), requested_preset_index=1)

        self.assertEqual(result.executionResult, "scene_mode_transition_timeout")
        self.assertEqual(result.visualState, "undetermined")
        self.assertIsNotNone(result.sceneModeStability)
        self.assertTrue(result.sceneModeStability.sceneModeTransitionTimeout)

    def test_run_once_returns_scene_mode_probe_incomplete_before_readiness(self) -> None:
        calibration = types.SimpleNamespace(
            deviceId="device",
            channelId="0",
            targetId="target",
            targetName="target",
            presetIndex=1,
            presetName="preset",
            roi={"x": 0, "y": 0, "width": 16, "height": 16},
            focusAnchorRoi={"x": 4, "y": 4, "width": 8, "height": 8},
            notes="",
            snapshotPath="snapshots/demo.png",
            snapshotUrl="/artifacts/snapshots/demo.png",
            updatedAt="2026-01-01T00:00:00+00:00",
        )
        config = RecognitionGlobalConfig(
            sceneMode="auto",
            visualReadinessEnabled=True,
            streamStartupFreshnessEnabled=False,
        )
        service = RunOnceService(
            global_config=config,
            raw_config={
                "sceneMode": "auto",
                "dayVisible": {"algorithmVersion": "test-day"},
                "nightIr": {"algorithmVersion": "test-night"},
            },
        )
        fake_session = _FakeSession([np.zeros((24, 24, 3), dtype=np.uint8)] * 2, frame_interval_s=0.1)
        service.sampler.open_session = Mock(return_value=fake_session)
        service.replay_store.persist_async = Mock(
            return_value=(
                {"metadataPath": "replays/replay-metadata.json"},
                ReplaySaveState(status="pending", statusPath="replays/replay-save-status.json", message="scheduled"),
            )
        )
        stability_result = self._scene_mode_stability_result(
            stable=False,
            classification="ambiguous",
            suggested_mode="night_ir",
            transition_timeout=False,
        )
        stability_result.reason = "scene_mode_probe_incomplete"
        service.scene_mode_stability_guard.observe = Mock(return_value=stability_result)

        fake_preset_module = types.ModuleType("app.services.dahua_preset_service")
        fake_preset_module.preset_service = types.SimpleNamespace(turn_preset=Mock(return_value=None))
        fake_sign_module = types.ModuleType("app.utils.request_sign_adapter")
        fake_sign_module.DahuaApiError = type("DahuaApiError", (Exception,), {})

        with patch("inspector.run_once_service.storage_service.load_path", return_value=calibration):
            with patch("app.config.Settings.is_dahua_configured", new_callable=PropertyMock, return_value=True):
                with patch.dict(
                    sys.modules,
                    {
                        "app.services.dahua_preset_service": fake_preset_module,
                        "app.utils.request_sign_adapter": fake_sign_module,
                    },
                ):
                    result = service.run(config_path=Path("demo.json"), requested_preset_index=1)

        self.assertEqual(result.executionResult, "scene_mode_probe_incomplete")
        self.assertEqual(result.sceneModeReason, "scene_mode_probe_incomplete")
        self.assertEqual(result.visualState, "undetermined")
        self.assertIsNotNone(result.sceneModeStability)
        self.assertFalse(result.sceneModeStability.sceneModeTransitionTimeout)

    def test_run_once_auto_mode_falls_back_to_single_probe_when_scene_stability_disabled(self) -> None:
        calibration = types.SimpleNamespace(
            deviceId="device",
            channelId="0",
            targetId="target",
            targetName="target",
            presetIndex=1,
            presetName="preset",
            roi={"x": 0, "y": 0, "width": 16, "height": 16},
            focusAnchorRoi={"x": 4, "y": 4, "width": 8, "height": 8},
            notes="",
            snapshotPath="snapshots/demo.png",
            snapshotUrl="/artifacts/snapshots/demo.png",
            updatedAt="2026-01-01T00:00:00+00:00",
        )
        config = RecognitionGlobalConfig(
            sceneMode="auto",
            sceneModeStabilityEnabled=False,
            visualReadinessEnabled=True,
            sceneAutoFrameCount=2,
            streamStartupFreshnessEnabled=False,
            visualReadinessMinFrames=2,
            visualReadinessMinElapsedMs=0,
            visualReadinessMinReadyWindowMs=0,
            visualReadinessMinSharpness=300.0,
        )
        service = RunOnceService(
            global_config=config,
            raw_config={
                "sceneMode": "auto",
                "dayVisible": {"algorithmVersion": "test-day", "visualReadinessMinSharpness": 280.0},
                "nightIr": {"algorithmVersion": "test-night", "visualReadinessMinSharpness": 90.0},
            },
        )

        ir_frame = np.full((24, 24, 3), 120, dtype=np.uint8)
        fake_session = _FakeSession([ir_frame, ir_frame, ir_frame, ir_frame], frame_interval_s=0.12)
        service.sampler.open_session = Mock(return_value=fake_session)
        service.sampler.sample_from_session = Mock()
        service.replay_store.persist_async = Mock(
            return_value=(
                {
                    "metadataPath": "replays/replay-metadata.json",
                    "representativeFramePath": "replays/representative-frame.ppm",
                },
                ReplaySaveState(
                    status="pending",
                    statusPath="replays/replay-save-status.json",
                    message="scheduled",
                ),
            )
        )

        def _fake_wait_until_ready(checker_self, readiness_session, *, roi=None):  # noqa: ANN001
            self.assertEqual(checker_self.global_config.sceneMode, "night_ir")
            self.assertEqual(checker_self.global_config.visualReadinessMinSharpness, 90.0)
            self.assertEqual(roi.model_dump(), calibration.focusAnchorRoi)
            return VisualReadinessOutcome(
                metrics=VisualReadinessMetrics(
                    ready=False,
                    reason="visual_not_ready_blurry",
                    sharpnessMean=40.0,
                    sharpnessMin=35.0,
                    stabilityScore=0.01,
                    framesChecked=2,
                    elapsedMs=220,
                ),
                frames=[ir_frame.copy()],
                frameTimestampsMs=[0],
                frameCapturedAts=[100.0],
                streamType="flv",
                streamUrl="fake://stream",
            )

        fake_preset_module = types.ModuleType("app.services.dahua_preset_service")
        fake_preset_module.preset_service = types.SimpleNamespace(turn_preset=Mock(return_value=None))
        fake_sign_module = types.ModuleType("app.utils.request_sign_adapter")
        fake_sign_module.DahuaApiError = type("DahuaApiError", (Exception,), {})

        with patch("inspector.run_once_service.storage_service.load_path", return_value=calibration):
            with patch("app.config.Settings.is_dahua_configured", new_callable=PropertyMock, return_value=True):
                with patch.dict(
                    sys.modules,
                    {
                        "app.services.dahua_preset_service": fake_preset_module,
                        "app.utils.request_sign_adapter": fake_sign_module,
                    },
                ):
                    with patch.object(VisualReadinessChecker, "wait_until_ready", autospec=True, side_effect=_fake_wait_until_ready):
                        result = service.run(config_path=Path("demo.json"), requested_preset_index=1)

        self.assertEqual(result.executionResult, "visual_blurry_before_detection")
        self.assertEqual(result.effectiveSceneMode, "night_ir")
        self.assertEqual(result.sceneModeReason, "low_color_delta_and_high_channel_correlation_support_night_ir")
        self.assertIsNotNone(result.sceneModeStability)
        self.assertFalse(result.sceneModeStability.enabled)
        self.assertFalse(result.sceneModeStability.sceneModeStable)
        service.sampler.sample_from_session.assert_not_called()

    def test_day_visible_low_brightness_with_color_signals_uses_twilight_profile(self) -> None:
        raw_config = {
            "sceneMode": "auto",
            "visualReadinessMinObserveMs": 900,
            "visualReadinessTimeoutMs": 3500,
            "visualReadinessPostReadyRecheckFrames": 1,
            "visualReadinessPostReadyRecheckWindowMs": 180,
            "sampleQualityTimeoutMs": 5200,
            "sampleQualityMaxRecoveries": 3,
            "dayVisible": {
                "visualReadinessTimeoutMs": 3800,
                "visualReadinessMinObserveMs": 1200,
                "visualReadinessPostReadyRecheckFrames": 2,
                "visualReadinessPostReadyRecheckWindowMs": 240,
                "sampleQualityTimeoutMs": 5200,
                "sampleQualityMaxRecoveries": 3,
                "twilight": {
                    "visualReadinessTimeoutMs": 4500,
                    "visualReadinessMinObserveMs": 1500,
                    "visualReadinessPostReadyRecheckFrames": 2,
                    "visualReadinessPostReadyRecheckWindowMs": 320,
                    "sampleQualityTimeoutMs": 6000,
                    "sampleQualityMaxRecoveries": 4,
                },
            },
            "nightIr": {
                "sampleQualityTimeoutMs": 4500,
                "sampleQualityMaxRecoveries": 2,
            },
        }
        service = RunOnceService(global_config=RecognitionGlobalConfig(sceneMode="auto"), raw_config=raw_config)
        profile = service._resolve_scene_profile(
            "day_visible",
            types.SimpleNamespace(
                diagnostics=self._scene_mode_diagnostics(
                    brightness_mean=92.0,
                    colorfulness_mean=16.0,
                    saturation_p90=0.1,
                    day_visible_score=0.86,
                    night_ir_score=0.24,
                    score_margin=0.62,
                )
            ),
        )

        self.assertEqual(profile.effectiveSceneProfile, "day_visible_twilight")
        self.assertTrue(profile.twilightProfileApplied)
        self.assertEqual(profile.twilightProfileReason, "brightness_low_but_day_visible_signals_remain_strong")
        self.assertEqual(profile.twilightBrightnessMean, 92.0)
        self.assertEqual(profile.effectiveConfig.sampleQualityTimeoutMs, 6000)
        self.assertEqual(profile.effectiveConfig.sampleQualityMaxRecoveries, 4)
        self.assertEqual(profile.effectiveConfig.visualReadinessTimeoutMs, 4500)
        self.assertEqual(profile.effectiveConfig.visualReadinessMinObserveMs, 1500)
        self.assertEqual(profile.effectiveConfig.visualReadinessPostReadyRecheckWindowMs, 320)

    def test_normal_day_visible_does_not_use_twilight_profile(self) -> None:
        raw_config = {
            "sceneMode": "auto",
            "dayVisible": {
                "sampleQualityTimeoutMs": 5200,
                "sampleQualityMaxRecoveries": 3,
                "twilight": {
                    "sampleQualityTimeoutMs": 6000,
                    "sampleQualityMaxRecoveries": 4,
                },
            },
            "nightIr": {},
        }
        service = RunOnceService(global_config=RecognitionGlobalConfig(sceneMode="auto"), raw_config=raw_config)
        profile = service._resolve_scene_profile(
            "day_visible",
            types.SimpleNamespace(
                diagnostics=self._scene_mode_diagnostics(
                    brightness_mean=148.0,
                    colorfulness_mean=18.0,
                    saturation_p90=0.12,
                    day_visible_score=0.9,
                    night_ir_score=0.18,
                    score_margin=0.72,
                )
            ),
        )

        self.assertEqual(profile.effectiveSceneProfile, "day_visible_normal")
        self.assertFalse(profile.twilightProfileApplied)
        self.assertEqual(profile.twilightProfileReason, "brightness_above_twilight_band")
        self.assertEqual(profile.effectiveConfig.sampleQualityTimeoutMs, 5200)
        self.assertEqual(profile.effectiveConfig.sampleQualityMaxRecoveries, 3)

    def test_night_ir_never_uses_twilight_profile(self) -> None:
        service = RunOnceService(global_config=RecognitionGlobalConfig(sceneMode="auto"))

        profile = service._resolve_scene_profile(
            "night_ir",
            types.SimpleNamespace(
                diagnostics=self._scene_mode_diagnostics(
                    brightness_mean=40.0,
                    colorfulness_mean=2.0,
                    saturation_p90=0.01,
                    day_visible_score=0.2,
                    night_ir_score=0.88,
                    score_margin=0.68,
                    classification="night_ir",
                    suggested_mode="night_ir",
                )
            ),
        )

        self.assertEqual(profile.effectiveSceneProfile, "night_ir")
        self.assertFalse(profile.twilightProfileApplied)
        self.assertEqual(profile.twilightProfileReason, "effective_scene_mode_is_night_ir")

    def test_stream_startup_freshness_disabled_emits_disabled_reason(self) -> None:
        service = RunOnceService(global_config=RecognitionGlobalConfig(streamStartupFreshnessEnabled=False))

        freshness = service._guard_stream_startup_freshness(_FakeSession([]))

        self.assertFalse(freshness.enabled)
        self.assertEqual(freshness.exitReason, "disabled")

    def test_stream_startup_freshness_times_out_before_jump(self) -> None:
        service = RunOnceService(
            global_config=RecognitionGlobalConfig(
                streamStartupFreshnessTimeoutMs=600,
                streamStartupFreshnessStableFrames=2,
            )
        )
        steady = np.zeros((24, 24, 3), dtype=np.uint8)

        freshness = service._guard_stream_startup_freshness(_FakeSession([steady, steady, steady], frame_interval_s=0.1))

        self.assertTrue(freshness.enabled)
        self.assertEqual(freshness.exitReason, "timeout_no_jump")
        self.assertFalse(freshness.jumpDetected)

    def test_stream_startup_freshness_times_out_after_jump_without_stable_window(self) -> None:
        service = RunOnceService(
            global_config=RecognitionGlobalConfig(
                streamStartupFreshnessTimeoutMs=600,
                streamStartupFreshnessStableFrames=3,
                streamStartupFreshnessStableThreshold=0.01,
            )
        )
        old_frame = np.zeros((24, 24, 3), dtype=np.uint8)
        jump_frame = np.full((24, 24, 3), 255, dtype=np.uint8)
        noisy_frame = jump_frame.copy()
        noisy_frame[::2, ::2, :] = 0

        freshness = service._guard_stream_startup_freshness(
            _FakeSession([old_frame, jump_frame, noisy_frame, jump_frame], frame_interval_s=0.1)
        )

        self.assertTrue(freshness.jumpDetected)
        self.assertFalse(freshness.stableAfterJump)
        self.assertEqual(freshness.exitReason, "timeout_after_jump_no_stable")

    def test_stream_startup_freshness_reports_no_frames(self) -> None:
        service = RunOnceService(global_config=RecognitionGlobalConfig(streamStartupFreshnessTimeoutMs=600))

        freshness = service._guard_stream_startup_freshness(_FakeSession([]))

        self.assertEqual(freshness.exitReason, "no_frames")
        self.assertEqual(freshness.consumedFrames, 0)

    def test_run_once_stops_before_sampling_when_visual_readiness_fails(self) -> None:
        calibration = types.SimpleNamespace(
            deviceId="device",
            channelId="0",
            targetId="target",
            targetName="target",
            presetIndex=1,
            presetName="preset",
            roi={"x": 0, "y": 0, "width": 16, "height": 16},
            focusAnchorRoi={"x": 4, "y": 4, "width": 8, "height": 8},
            notes="",
            snapshotPath="snapshots/demo.png",
            snapshotUrl="/artifacts/snapshots/demo.png",
            updatedAt="2026-01-01T00:00:00+00:00",
        )
        config = RecognitionGlobalConfig(sceneMode="day_visible", visualReadinessEnabled=True)
        service = RunOnceService(
            global_config=config,
            raw_config={"sceneMode": "day_visible", "dayVisible": {"algorithmVersion": "test-day"}},
        )
        fake_session = _FakeSession([np.zeros((24, 24, 3), dtype=np.uint8)])
        service.sampler.open_session = Mock(return_value=fake_session)
        service.sampler.sample_from_session = Mock()
        service.sampler.build_sequence_from_frames = Mock(return_value=Mock())
        service.replay_store.persist_async = Mock(
            return_value=(
                {
                    "metadataPath": "replays/replay-metadata.json",
                    "representativeFramePath": "replays/representative-frame.ppm",
                },
                ReplaySaveState(
                    status="pending",
                    statusPath="replays/replay-save-status.json",
                    message="scheduled",
                ),
            )
        )

        fake_preset_module = types.ModuleType("app.services.dahua_preset_service")
        fake_preset_module.preset_service = types.SimpleNamespace(turn_preset=Mock(return_value=None))
        fake_sign_module = types.ModuleType("app.utils.request_sign_adapter")
        fake_sign_module.DahuaApiError = type("DahuaApiError", (Exception,), {})

        def _fake_wait_until_ready(_checker_self, _session, *, roi=None):  # noqa: ANN001
            self.assertEqual(roi.model_dump(), calibration.focusAnchorRoi)
            return VisualReadinessOutcome(
                metrics=VisualReadinessMetrics(
                    ready=False,
                    reason="visual_not_ready_blurry_and_unstable",
                    sharpnessMean=12.0,
                    sharpnessMin=8.0,
                    stabilityScore=0.22,
                    framesChecked=4,
                    elapsedMs=420,
                ),
                frames=[np.zeros((24, 24, 3), dtype=np.uint8)],
                frameTimestampsMs=[0],
                frameCapturedAts=[100.0],
                streamType="flv",
                streamUrl="fake://stream",
            )

        with patch("inspector.run_once_service.storage_service.load_path", return_value=calibration):
            with patch("app.config.Settings.is_dahua_configured", new_callable=PropertyMock, return_value=True):
                with patch.dict(
                    sys.modules,
                    {
                        "app.services.dahua_preset_service": fake_preset_module,
                        "app.utils.request_sign_adapter": fake_sign_module,
                    },
                ):
                    with patch.object(
                        VisualReadinessChecker,
                        "wait_until_ready",
                        autospec=True,
                        side_effect=_fake_wait_until_ready,
                    ):
                        result = service.run(config_path=Path("demo.json"), requested_preset_index=1)

        self.assertEqual(result.executionResult, "visual_blurry_before_detection")
        self.assertEqual(result.visualState, "undetermined")
        self.assertFalse(result.visualReadinessPassed)
        self.assertEqual(result.visualReadinessReason, "visual_not_ready_blurry_and_unstable")
        self.assertEqual(result.timing.sampleMs, 0)
        self.assertGreater(result.timing.visualReadinessMs, 0)
        self.assertEqual(result.replaySave.status, "pending")
        self.assertEqual(result.replaySave.statusPath, "replays/replay-save-status.json")
        self.assertEqual(result.evidencePaths.replayMetadataPath, "replays/replay-metadata.json")
        self.assertEqual(result.evidencePaths.representativeFramePath, "replays/representative-frame.ppm")
        service.sampler.sample_from_session.assert_not_called()
        self.assertFalse(result.focusAnchorRoiFallbackUsed)
        self.assertEqual(result.focusAnchorRoiSource, "focus_anchor_roi")

    def test_run_detection_pass_uses_detection_roi_even_when_focus_anchor_exists(self) -> None:
        config = RecognitionGlobalConfig(sceneMode="day_visible")
        service = RunOnceService(global_config=config, raw_config={"sceneMode": "day_visible"})
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 1, "y": 2, "width": 10, "height": 12},
            focusAnchorRoi={"x": 20, "y": 22, "width": 8, "height": 8},
        )
        frame = _make_checkerboard_frame(size=(24, 24))
        sequence = SampledSequence(
            streamType="flv",
            streamUrl="fake://stream",
            frames=np.stack([frame], axis=0),
            frameTimestampsMs=[0],
            targetFrameCount=1,
            sampledFrameCount=1,
            configuredSampleFps=10.0,
            actualSampleFps=10.0,
            configuredSampleDurationMs=100,
            actualSampleDurationMs=100,
            frameWidth=24,
            frameHeight=24,
        )
        aligned_sequence = AlignedSequence(
            alignedFrames=sequence.frames,
            globalShifts=[(0, 0)],
            shiftMagnitudes=[0.0],
            appliedGlobalShifts=[(0, 0)],
            appliedShiftMagnitudes=[0.0],
            overflowFlags=[False],
            alignmentApplied=False,
        )
        vote_decision = types.SimpleNamespace(
            visualState="no_splash",
            passRatio=0.0,
            overflowFrameRatio=0.0,
            motionReductionRatio=1.0,
            reliabilityGateTriggered=False,
            reason="test",
            staticBrightInterferenceSuppressed=False,
        )

        with patch("inspector.run_once_service.FullFrameAligner.align", return_value=aligned_sequence):
            with patch("inspector.run_once_service.FrameFeatureExtractor.extract", return_value=[]) as mock_extract:
                with patch("inspector.run_once_service.WeightedFrameScorer.score", return_value=[]):
                    with patch("inspector.run_once_service.TemporalVoteResolver.resolve", return_value=vote_decision):
                        service._run_detection_pass(
                            sequence=sequence,
                            target=target,
                            effective_config=config,
                        )

        self.assertEqual(mock_extract.call_args.args[1], target.roi)

    def test_focus_anchor_roi_falls_back_to_detection_roi_for_legacy_calibration(self) -> None:
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 1, "y": 2, "width": 10, "height": 12},
            focusAnchorRoi=None,
        )
        service = RunOnceService(global_config=RecognitionGlobalConfig())

        roi, source, fallback_used = service._focus_anchor_roi(target)

        self.assertEqual(roi, target.roi)
        self.assertEqual(source, "roi_fallback")
        self.assertTrue(fallback_used)

    def test_focus_anchor_roi_resolution_does_not_reuse_previous_target(self) -> None:
        service = RunOnceService(global_config=RecognitionGlobalConfig())
        first_target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target-a",
            targetName="target-a",
            roi={"x": 0, "y": 0, "width": 10, "height": 10},
            focusAnchorRoi={"x": 2, "y": 2, "width": 4, "height": 4},
        )
        second_target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=2,
            presetName="preset",
            targetId="target-b",
            targetName="target-b",
            roi={"x": 20, "y": 20, "width": 10, "height": 10},
            focusAnchorRoi={"x": 24, "y": 24, "width": 3, "height": 3},
        )

        first_roi, _, _ = service._focus_anchor_roi(first_target)
        second_roi, _, _ = service._focus_anchor_roi(second_target)

        self.assertEqual(first_roi.model_dump(), first_target.focusAnchorRoi.model_dump())
        self.assertEqual(second_roi.model_dump(), second_target.focusAnchorRoi.model_dump())
        self.assertNotEqual(first_roi.model_dump(), second_roi.model_dump())

    def test_run_once_auto_mode_uses_scene_specific_readiness_config_before_sampling(self) -> None:
        calibration = types.SimpleNamespace(
            deviceId="device",
            channelId="0",
            targetId="target",
            targetName="target",
            presetIndex=1,
            presetName="preset",
            roi={"x": 0, "y": 0, "width": 16, "height": 16},
            focusAnchorRoi={"x": 4, "y": 4, "width": 8, "height": 8},
            notes="",
            snapshotPath="snapshots/demo.png",
            snapshotUrl="/artifacts/snapshots/demo.png",
            updatedAt="2026-01-01T00:00:00+00:00",
        )
        config = RecognitionGlobalConfig(
            sceneMode="auto",
            visualReadinessEnabled=True,
            sceneAutoFrameCount=2,
            streamStartupFreshnessEnabled=False,
            visualReadinessMinFrames=2,
            visualReadinessMinElapsedMs=0,
            visualReadinessMinReadyWindowMs=0,
            visualReadinessMinSharpness=300.0,
        )
        service = RunOnceService(
            global_config=config,
            raw_config={
                "sceneMode": "auto",
                "visualReadinessMinSharpness": 300.0,
                "dayVisible": {
                    "algorithmVersion": "test-day",
                    "visualReadinessMinSharpness": 280.0,
                },
                "nightIr": {
                    "algorithmVersion": "test-night",
                    "visualReadinessMinSharpness": 90.0,
                },
            },
        )
        service.scene_mode_stability_guard.observe = Mock(
            return_value=self._scene_mode_stability_result(
                stable=True,
                classification="night_ir",
                suggested_mode="night_ir",
            )
        )

        ir_frame = np.full((24, 24, 3), 120, dtype=np.uint8)
        fake_session = _FakeSession([ir_frame, ir_frame, ir_frame, ir_frame], frame_interval_s=0.12)
        service.sampler.open_session = Mock(return_value=fake_session)
        service.sampler.sample_from_session = Mock()
        service.replay_store.persist_async = Mock(
            return_value=(
                {
                    "metadataPath": "replays/replay-metadata.json",
                    "representativeFramePath": "replays/representative-frame.ppm",
                },
                ReplaySaveState(
                    status="pending",
                    statusPath="replays/replay-save-status.json",
                    message="scheduled",
                ),
            )
        )

        def _fake_wait_until_ready(checker_self, readiness_session, *, roi=None):  # noqa: ANN001
            self.assertEqual(checker_self.global_config.sceneMode, "night_ir")
            self.assertEqual(checker_self.global_config.visualReadinessMinSharpness, 90.0)
            self.assertEqual(roi.model_dump(), calibration.focusAnchorRoi)
            return VisualReadinessOutcome(
                metrics=VisualReadinessMetrics(
                    ready=False,
                    reason="visual_not_ready_blurry",
                    sharpnessMean=40.0,
                    sharpnessMin=35.0,
                    stabilityScore=0.01,
                    framesChecked=4,
                    elapsedMs=420,
                ),
                frames=[ir_frame.copy()],
                frameTimestampsMs=[0],
                frameCapturedAts=[100.0],
                streamType="flv",
                streamUrl="fake://stream",
            )

        fake_preset_module = types.ModuleType("app.services.dahua_preset_service")
        fake_preset_module.preset_service = types.SimpleNamespace(turn_preset=Mock(return_value=None))
        fake_sign_module = types.ModuleType("app.utils.request_sign_adapter")
        fake_sign_module.DahuaApiError = type("DahuaApiError", (Exception,), {})

        with patch("inspector.run_once_service.storage_service.load_path", return_value=calibration):
            with patch("app.config.Settings.is_dahua_configured", new_callable=PropertyMock, return_value=True):
                with patch.dict(
                    sys.modules,
                    {
                        "app.services.dahua_preset_service": fake_preset_module,
                        "app.utils.request_sign_adapter": fake_sign_module,
                    },
                ):
                    with patch.object(VisualReadinessChecker, "wait_until_ready", autospec=True, side_effect=_fake_wait_until_ready):
                        result = service.run(config_path=Path("demo.json"), requested_preset_index=1)

        self.assertEqual(result.executionResult, "visual_blurry_before_detection")
        self.assertEqual(result.visualReadinessReason, "visual_not_ready_blurry")
        self.assertEqual(result.sceneMode, "auto")
        self.assertEqual(result.effectiveSceneMode, "night_ir")
        self.assertEqual(result.sceneModeReason, "low_color_delta_and_high_channel_correlation_support_night_ir")
        service.sampler.sample_from_session.assert_not_called()

    def test_run_once_auto_mode_preserves_scene_probe_diagnostics_when_sampling_fails(self) -> None:
        calibration = types.SimpleNamespace(
            deviceId="device",
            channelId="0",
            targetId="target",
            targetName="target",
            presetIndex=1,
            presetName="preset",
            roi={"x": 0, "y": 0, "width": 16, "height": 16},
            focusAnchorRoi={"x": 4, "y": 4, "width": 8, "height": 8},
            notes="",
            snapshotPath="snapshots/demo.png",
            snapshotUrl="/artifacts/snapshots/demo.png",
            updatedAt="2026-01-01T00:00:00+00:00",
        )
        config = RecognitionGlobalConfig(
            sceneMode="auto",
            visualReadinessEnabled=True,
            sceneAutoFrameCount=2,
            streamStartupFreshnessEnabled=False,
            visualReadinessMinFrames=2,
            visualReadinessMinElapsedMs=0,
            visualReadinessMinReadyWindowMs=0,
            visualReadinessMinSharpness=300.0,
        )
        service = RunOnceService(
            global_config=config,
            raw_config={
                "sceneMode": "auto",
                "dayVisible": {"algorithmVersion": "test-day", "visualReadinessMinSharpness": 280.0},
                "nightIr": {"algorithmVersion": "test-night", "visualReadinessMinSharpness": 90.0},
            },
        )
        service.scene_mode_stability_guard.observe = Mock(
            return_value=self._scene_mode_stability_result(
                stable=True,
                classification="night_ir",
                suggested_mode="night_ir",
            )
        )

        ir_frame = np.full((24, 24, 3), 120, dtype=np.uint8)
        fake_session = _FakeSession([ir_frame, ir_frame, ir_frame, ir_frame], frame_interval_s=0.12)
        service.sampler.open_session = Mock(return_value=fake_session)
        def _fake_wait_until_ready(checker_self, readiness_session, *, roi=None):  # noqa: ANN001
            self.assertEqual(checker_self.global_config.sceneMode, "night_ir")
            return VisualReadinessOutcome(
                metrics=VisualReadinessMetrics(
                    ready=True,
                    reason="visual_ready",
                    sharpnessMean=120.0,
                    sharpnessMin=110.0,
                    stabilityScore=0.01,
                    framesChecked=4,
                    elapsedMs=420,
                ),
                frames=[ir_frame.copy()],
                frameTimestampsMs=[0],
                frameCapturedAts=[100.0],
                streamType="flv",
                streamUrl="fake://stream",
            )

        fake_preset_module = types.ModuleType("app.services.dahua_preset_service")
        fake_preset_module.preset_service = types.SimpleNamespace(turn_preset=Mock(return_value=None))
        fake_sign_module = types.ModuleType("app.utils.request_sign_adapter")
        fake_sign_module.DahuaApiError = type("DahuaApiError", (Exception,), {})

        with patch("inspector.run_once_service.storage_service.load_path", return_value=calibration):
            with patch("app.config.Settings.is_dahua_configured", new_callable=PropertyMock, return_value=True):
                with patch.dict(
                    sys.modules,
                    {
                        "app.services.dahua_preset_service": fake_preset_module,
                        "app.utils.request_sign_adapter": fake_sign_module,
                    },
                ):
                    with patch.object(
                        VisualReadinessChecker,
                        "wait_until_ready",
                        autospec=True,
                        side_effect=_fake_wait_until_ready,
                    ):
                        with patch.object(
                            service,
                            "_sample_with_quality_guard",
                            side_effect=FlvSamplerError("not enough", reason="insufficient_frames"),
                        ):
                            result = service.run(config_path=Path("demo.json"), requested_preset_index=1)

        self.assertEqual(result.executionResult, "insufficient_frames")
        self.assertEqual(result.sceneMode, "auto")
        self.assertEqual(result.effectiveSceneMode, "night_ir")
        self.assertEqual(result.sceneModeReason, "low_color_delta_and_high_channel_correlation_support_night_ir")
        self.assertIsNotNone(result.sceneModeDiagnostics)
        self.assertEqual(result.algorithmVersion, "test-night")
        self.assertEqual(result.scoreSummary.sceneMode, "night_ir")

    def test_sample_quality_guard_accepts_continuous_clear_sequence(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="day_visible",
            sampleDurationMs=400,
            sampleFps=10,
            sequenceFrameCount=4,
            visualReadinessMinSharpness=50.0,
            visualReadinessMinSharpCellRatio=0.45,
            sampleQualityTimeoutMs=1500,
            sampleQualityMaxRecoveries=2,
        )
        service = RunOnceService(global_config=config, raw_config={"sceneMode": "day_visible"})
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 120, "height": 120},
        )
        sharp = _make_checkerboard_frame()
        readiness_outcome = VisualReadinessOutcome(
            metrics=VisualReadinessMetrics(ready=True, reason="visual_ready"),
            frames=[sharp.copy()],
            frameTimestampsMs=[0],
            frameCapturedAts=[10.0],
            streamType="flv",
            streamUrl="fake://stream",
            readyFrameIndex=0,
            confirmFrameIndex=0,
        )

        fake_session = _FakeSession([sharp, sharp, sharp, sharp], frame_interval_s=0.1)
        fake_session._base_time = 10.0
        sequence, guard_result = service._sample_with_quality_guard(
            session=fake_session,
            effective_config=config,
            target=target,
            readiness_outcome=readiness_outcome,
        )

        self.assertIsNotNone(sequence)
        self.assertTrue(guard_result.passed)
        self.assertEqual(guard_result.metrics.reason, "sample_quality_passed")
        self.assertEqual(guard_result.metrics.acceptedFrameCount, 4)
        self.assertGreaterEqual(guard_result.metrics.reusedReadinessFrames, 1)

    def test_sample_quality_guard_restarts_after_blur_and_then_passes(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="day_visible",
            sampleDurationMs=400,
            sampleFps=10,
            sequenceFrameCount=4,
            visualReadinessMinSharpness=50.0,
            visualReadinessMinSharpCellRatio=0.45,
            sampleQualityTimeoutMs=2000,
            sampleQualityMaxRecoveries=2,
        )
        service = RunOnceService(global_config=config, raw_config={"sceneMode": "day_visible"})
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 120, "height": 120},
        )
        sharp = _make_checkerboard_frame()
        blurry = np.full((120, 160, 3), 127, dtype=np.uint8)
        readiness_outcome = VisualReadinessOutcome(
            metrics=VisualReadinessMetrics(ready=True, reason="visual_ready"),
            frames=[sharp.copy()],
            frameTimestampsMs=[0],
            frameCapturedAts=[10.0],
            streamType="flv",
            streamUrl="fake://stream",
            readyFrameIndex=0,
            confirmFrameIndex=0,
        )

        fake_session = _FakeSession([sharp, blurry, sharp, sharp, sharp, sharp], frame_interval_s=0.1)
        fake_session._base_time = 10.0
        sequence, guard_result = service._sample_with_quality_guard(
            session=fake_session,
            effective_config=config,
            target=target,
            readiness_outcome=readiness_outcome,
        )

        self.assertIsNotNone(sequence)
        self.assertTrue(guard_result.passed)
        self.assertEqual(guard_result.metrics.reason, "sample_quality_recovered_and_passed")
        self.assertEqual(guard_result.metrics.recoveryCount, 1)
        self.assertTrue(guard_result.metrics.restartedDuringSampling)
        self.assertIsNotNone(guard_result.degradedFrame)

    def test_sample_quality_guard_daytime_allows_third_short_recovery_and_passes(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="day_visible",
            sampleDurationMs=400,
            sampleFps=10,
            sequenceFrameCount=4,
            visualReadinessMinSharpness=50.0,
            visualReadinessMinSharpCellRatio=0.45,
            sampleQualityTimeoutMs=5200,
            sampleQualityMaxRecoveries=3,
        )
        service = RunOnceService(global_config=config, raw_config={"sceneMode": "day_visible"})
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 120, "height": 120},
        )
        sharp = _make_checkerboard_frame()
        blurry = np.full_like(sharp, 127)
        readiness_outcome = VisualReadinessOutcome(
            metrics=VisualReadinessMetrics(ready=True, reason="visual_ready"),
            frames=[sharp.copy()],
            frameTimestampsMs=[0],
            frameCapturedAts=[10.0],
            streamType="flv",
            streamUrl="fake://stream",
            readyFrameIndex=0,
            confirmFrameIndex=0,
        )

        fake_session = _FakeSession(
            [sharp, blurry, sharp, blurry, sharp, blurry, sharp, sharp, sharp, sharp],
            frame_interval_s=0.1,
        )
        fake_session._base_time = 10.0
        sequence, guard_result = service._sample_with_quality_guard(
            session=fake_session,
            effective_config=config,
            target=target,
            readiness_outcome=readiness_outcome,
        )

        self.assertIsNotNone(sequence)
        self.assertTrue(guard_result.passed)
        self.assertEqual(guard_result.metrics.reason, "sample_quality_recovered_and_passed")
        self.assertEqual(guard_result.metrics.recoveryCount, 3)

    def test_sample_quality_guard_night_ir_allows_third_short_recovery_and_passes(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="night_ir",
            sampleDurationMs=400,
            sampleFps=10,
            sequenceFrameCount=4,
            visualReadinessMinSharpness=50.0,
            visualReadinessMinSharpCellRatio=0.45,
            sampleQualityTimeoutMs=5200,
            sampleQualityMaxRecoveries=3,
        )
        service = RunOnceService(global_config=config, raw_config={"sceneMode": "night_ir"})
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 120, "height": 120},
        )
        sharp = _make_checkerboard_frame()
        blurry = np.full_like(sharp, 127)
        readiness_outcome = VisualReadinessOutcome(
            metrics=VisualReadinessMetrics(ready=True, reason="visual_ready"),
            frames=[sharp.copy()],
            frameTimestampsMs=[0],
            frameCapturedAts=[10.0],
            streamType="flv",
            streamUrl="fake://stream",
            readyFrameIndex=0,
            confirmFrameIndex=0,
        )

        fake_session = _FakeSession(
            [sharp, blurry, sharp, blurry, sharp, blurry, sharp, sharp, sharp, sharp],
            frame_interval_s=0.1,
        )
        fake_session._base_time = 10.0
        sequence, guard_result = service._sample_with_quality_guard(
            session=fake_session,
            effective_config=config,
            target=target,
            readiness_outcome=readiness_outcome,
        )

        self.assertIsNotNone(sequence)
        self.assertTrue(guard_result.passed)
        self.assertEqual(guard_result.metrics.reason, "sample_quality_recovered_and_passed")
        self.assertEqual(guard_result.metrics.recoveryCount, 3)

    def test_sample_quality_guard_night_ir_5700_budget_recovers_two_late_focus_regressions(self) -> None:
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 120, "height": 120},
        )
        sharp = _make_checkerboard_frame()
        blurry = np.full_like(sharp, 127)
        readiness_outcome = VisualReadinessOutcome(
            metrics=VisualReadinessMetrics(ready=True, reason="visual_ready"),
            frames=[sharp.copy()],
            frameTimestampsMs=[0],
            frameCapturedAts=[100.0],
            streamType="flv",
            streamUrl="fake://stream",
            readyFrameIndex=0,
            confirmFrameIndex=0,
        )
        frames = (
            [sharp.copy() for _ in range(14)]
            + [blurry.copy()]
            + [sharp.copy() for _ in range(18)]
            + [blurry.copy()]
            + [sharp.copy() for _ in range(20)]
        )

        config_old = RecognitionGlobalConfig(
            sceneMode="night_ir",
            sampleDurationMs=2000,
            sampleFps=10,
            sequenceFrameCount=20,
            visualReadinessMinSharpness=50.0,
            visualReadinessMinSharpCellRatio=0.45,
            sampleQualityTimeoutMs=5200,
            sampleQualityMaxRecoveries=3,
        )
        service_old = RunOnceService(global_config=config_old, raw_config={"sceneMode": "night_ir"})
        session_old = _FakeSession(frames, frame_interval_s=0.1)
        session_old._base_time = 100.0
        monotonic_old = [0.0] + [step * 0.1 for step in range(80)]
        with patch("inspector.run_once_service.monotonic", side_effect=monotonic_old):
            old_sequence, old_guard_result = service_old._sample_with_quality_guard(
                session=session_old,
                effective_config=config_old,
                target=target,
                readiness_outcome=readiness_outcome,
            )

        self.assertIsNone(old_sequence)
        self.assertFalse(old_guard_result.passed)
        self.assertEqual(old_guard_result.metrics.reason, "sample_quality_focus_regressed")
        self.assertEqual(old_guard_result.metrics.rejectSharpnessCount, 2)
        self.assertEqual(old_guard_result.metrics.rejectClearCellRatioCount, 2)

        config_new = RecognitionGlobalConfig(
            sceneMode="night_ir",
            sampleDurationMs=2000,
            sampleFps=10,
            sequenceFrameCount=20,
            visualReadinessMinSharpness=50.0,
            visualReadinessMinSharpCellRatio=0.45,
            sampleQualityTimeoutMs=5700,
            sampleQualityMaxRecoveries=3,
        )
        service_new = RunOnceService(global_config=config_new, raw_config={"sceneMode": "night_ir"})
        session_new = _FakeSession(frames, frame_interval_s=0.1)
        session_new._base_time = 100.0
        monotonic_new = [0.0] + [step * 0.1 for step in range(80)]
        with patch("inspector.run_once_service.monotonic", side_effect=monotonic_new):
            new_sequence, new_guard_result = service_new._sample_with_quality_guard(
                session=session_new,
                effective_config=config_new,
                target=target,
                readiness_outcome=readiness_outcome,
            )

        self.assertIsNotNone(new_sequence)
        self.assertTrue(new_guard_result.passed)
        self.assertEqual(new_guard_result.metrics.reason, "sample_quality_recovered_and_passed")
        self.assertEqual(new_guard_result.metrics.recoveryCount, 2)
        self.assertEqual(new_guard_result.metrics.rejectSharpnessCount, 2)
        self.assertEqual(new_guard_result.metrics.rejectClearCellRatioCount, 2)
        self.assertEqual(new_guard_result.metrics.rejectStabilityCount, 2)
        self.assertIsNotNone(new_guard_result.metrics.firstRejectedSharpness)
        self.assertIsNotNone(new_guard_result.metrics.lastRejectedSharpness)

    def test_sample_quality_guard_night_ir_extended_budget_still_rejects_persistent_blur(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="night_ir",
            sampleDurationMs=2000,
            sampleFps=10,
            sequenceFrameCount=20,
            visualReadinessMinSharpness=50.0,
            visualReadinessMinSharpCellRatio=0.45,
            sampleQualityTimeoutMs=5700,
            sampleQualityMaxRecoveries=3,
        )
        service = RunOnceService(global_config=config, raw_config={"sceneMode": "night_ir"})
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 120, "height": 120},
        )
        sharp = _make_checkerboard_frame()
        blurry = np.full_like(sharp, 127)
        readiness_outcome = VisualReadinessOutcome(
            metrics=VisualReadinessMetrics(ready=True, reason="visual_ready"),
            frames=[sharp.copy()],
            frameTimestampsMs=[0],
            frameCapturedAts=[100.0],
            streamType="flv",
            streamUrl="fake://stream",
            readyFrameIndex=0,
            confirmFrameIndex=0,
        )
        session = _FakeSession([blurry.copy() for _ in range(25)], frame_interval_s=0.1)
        session._base_time = 100.0

        sequence, guard_result = service._sample_with_quality_guard(
            session=session,
            effective_config=config,
            target=target,
            readiness_outcome=readiness_outcome,
        )

        self.assertIsNone(sequence)
        self.assertFalse(guard_result.passed)
        self.assertEqual(guard_result.metrics.reason, "sample_quality_blurry_after_ready")
        self.assertGreaterEqual(guard_result.metrics.rejectSharpnessCount, 1)
        self.assertGreaterEqual(guard_result.metrics.rejectClearCellRatioCount, 1)

    def test_sample_quality_guard_records_diagnostics_when_first_sampling_frame_fails(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="night_ir",
            sampleDurationMs=400,
            sampleFps=10,
            sequenceFrameCount=4,
            visualReadinessMinSharpness=50.0,
            visualReadinessMinSharpCellRatio=0.45,
            sampleQualityTimeoutMs=800,
            sampleQualityMaxRecoveries=1,
        )
        service = RunOnceService(global_config=config, raw_config={"sceneMode": "night_ir"})
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 120, "height": 120},
        )
        sharp = _make_checkerboard_frame()
        blurry = np.full_like(sharp, 127)
        readiness_outcome = VisualReadinessOutcome(
            metrics=VisualReadinessMetrics(ready=True, reason="visual_ready"),
            frames=[],
            frameTimestampsMs=[],
            frameCapturedAts=[],
            streamType="flv",
            streamUrl="fake://stream",
            readyFrameIndex=None,
            confirmFrameIndex=None,
        )
        session = _FakeSession([blurry.copy(), blurry.copy(), blurry.copy()], frame_interval_s=0.1)
        session._base_time = 100.0

        sequence, guard_result = service._sample_with_quality_guard(
            session=session,
            effective_config=config,
            target=target,
            readiness_outcome=readiness_outcome,
        )

        self.assertIsNone(sequence)
        self.assertFalse(guard_result.passed)
        self.assertEqual(guard_result.metrics.reason, "sample_quality_blurry_after_ready")
        self.assertEqual(guard_result.metrics.rejectedFrames, 3)
        self.assertGreaterEqual(guard_result.metrics.rejectSharpnessCount, 1)
        self.assertGreaterEqual(guard_result.metrics.rejectClearCellRatioCount, 1)
        self.assertIsNotNone(guard_result.metrics.firstRejectedFrameIndex)
        self.assertIsNotNone(guard_result.metrics.lastRejectedFrameIndex)
        self.assertIsNotNone(guard_result.degradedFrame)

    def test_sample_quality_replay_metadata_contains_reject_diagnostics(self) -> None:
        config = RecognitionGlobalConfig(sceneMode="night_ir", sampleQualityTimeoutMs=5700, sampleQualityMaxRecoveries=3)
        service = RunOnceService(global_config=config, raw_config={"sceneMode": "night_ir"})
        service.replay_store.persist_async = Mock(
            return_value=({}, ReplaySaveState(status="pending", message="scheduled"))
        )
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 120, "height": 120},
        )
        frame = _make_checkerboard_frame()
        guard_result = __import__("types").SimpleNamespace(
            observedFrames=[frame.copy(), frame.copy()],
            observedTimestampsMs=[0, 100],
            streamType="flv",
            streamUrl="fake://stream",
            metrics=SampleQualityMetrics(
                passed=False,
                reason="sample_quality_focus_regressed",
                rejectSharpnessCount=3,
                rejectClearCellRatioCount=2,
                rejectStabilityCount=1,
                firstRejectedFrameIndex=4,
                firstRejectedElapsedMs=420,
                firstRejectedSharpness=43.0,
                firstRejectedClearCellRatio=0.24,
                firstRejectedStability=0.09,
                lastRejectedFrameIndex=17,
                lastRejectedElapsedMs=1980,
                lastRejectedSharpness=47.0,
                lastRejectedClearCellRatio=0.33,
                lastRejectedStability=0.22,
            ),
            attemptStartFrame=frame.copy(),
            degradedFrame=frame.copy(),
            lastQualifiedFrame=frame.copy(),
            acceptedMiddleFrame=None,
            acceptedEndFrame=None,
        )

        service._persist_sample_quality_replay(
            target=target,
            guard_result=guard_result,
            config_path="demo.json",
            execution_result="sample_quality_timeout",
            effective_config=config,
            requested_scene_mode="night_ir",
            effective_scene_mode="night_ir",
            scene_mode_decision=None,
            effective_scene_profile="night_ir",
            twilight_profile_applied=None,
            twilight_profile_reason=None,
            twilight_brightness_mean=None,
            focus_anchor_roi_fallback_used=False,
            focus_anchor_roi_source="focus_anchor_roi",
            stream_startup_freshness=None,
            stream_startup_freshness_result=None,
            scene_mode_stability=None,
            scene_mode_stability_result=None,
            visual_readiness=VisualReadinessMetrics(ready=True, reason="visual_ready"),
            readiness_outcome=None,
        )

        extra_metadata = service.replay_store.persist_async.call_args.kwargs["extra_metadata"]
        sample_quality_metadata = extra_metadata["sampleQuality"]
        self.assertEqual(sample_quality_metadata["rejectSharpnessCount"], 3)
        self.assertEqual(sample_quality_metadata["rejectClearCellRatioCount"], 2)
        self.assertEqual(sample_quality_metadata["rejectStabilityCount"], 1)
        self.assertEqual(sample_quality_metadata["firstRejectedFrameIndex"], 4)
        self.assertEqual(sample_quality_metadata["lastRejectedFrameIndex"], 17)
        self.assertEqual(extra_metadata["sampleQualityRejectSharpnessCount"], 3)
        self.assertEqual(extra_metadata["sampleQualityLastRejectedStability"], 0.22)

    def test_sample_quality_guard_returns_degraded_after_recovery_budget_is_exhausted(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="day_visible",
            sampleDurationMs=400,
            sampleFps=10,
            sequenceFrameCount=4,
            visualReadinessMinSharpness=50.0,
            visualReadinessMinSharpCellRatio=0.45,
            sampleQualityTimeoutMs=2000,
            sampleQualityMaxRecoveries=1,
        )
        service = RunOnceService(global_config=config, raw_config={"sceneMode": "day_visible"})
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 120, "height": 120},
        )
        sharp = _make_checkerboard_frame()
        blurry = np.full_like(sharp, 127)
        readiness_outcome = VisualReadinessOutcome(
            metrics=VisualReadinessMetrics(ready=True, reason="visual_ready"),
            frames=[sharp.copy()],
            frameTimestampsMs=[0],
            frameCapturedAts=[10.0],
            streamType="flv",
            streamUrl="fake://stream",
            readyFrameIndex=0,
            confirmFrameIndex=0,
        )

        fake_session = _FakeSession([sharp, blurry, sharp, blurry, sharp, sharp], frame_interval_s=0.1)
        fake_session._base_time = 10.0
        sequence, guard_result = service._sample_with_quality_guard(
            session=fake_session,
            effective_config=config,
            target=target,
            readiness_outcome=readiness_outcome,
        )

        self.assertIsNone(sequence)
        self.assertFalse(guard_result.passed)
        self.assertEqual(guard_result.metrics.reason, "sample_quality_recovery_budget_exhausted")
        self.assertEqual(guard_result.metrics.recoveryCount, 2)

    def test_sample_quality_recovery_count_counts_restart_attempts_including_terminal_exhaustion(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="day_visible",
            sampleDurationMs=400,
            sampleFps=10,
            sequenceFrameCount=4,
            visualReadinessMinSharpness=50.0,
            visualReadinessMinSharpCellRatio=0.45,
            sampleQualityTimeoutMs=2000,
            sampleQualityMaxRecoveries=1,
        )
        service = RunOnceService(global_config=config, raw_config={"sceneMode": "day_visible"})
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 120, "height": 120},
        )
        sharp = _make_checkerboard_frame()
        blurry = np.full_like(sharp, 127)
        readiness_outcome = VisualReadinessOutcome(
            metrics=VisualReadinessMetrics(ready=True, reason="visual_ready"),
            frames=[sharp.copy()],
            frameTimestampsMs=[0],
            frameCapturedAts=[10.0],
            streamType="flv",
            streamUrl="fake://stream",
            readyFrameIndex=0,
            confirmFrameIndex=0,
        )

        fake_session = _FakeSession([sharp, blurry, sharp, blurry, sharp, sharp], frame_interval_s=0.1)
        fake_session._base_time = 10.0
        sequence, guard_result = service._sample_with_quality_guard(
            session=fake_session,
            effective_config=config,
            target=target,
            readiness_outcome=readiness_outcome,
        )

        self.assertIsNone(sequence)
        self.assertFalse(guard_result.passed)
        self.assertEqual(guard_result.metrics.recoveryCount, 2)
        self.assertEqual(
            service._sample_quality_recovery_count_semantics(),
            "restart_attempts_including_budget_exhausting_restart",
        )

    def test_sample_quality_guard_rejects_clear_but_too_sparse_long_window_sequence(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="day_visible",
            sampleDurationMs=400,
            sampleFps=10,
            sequenceFrameCount=4,
            visualReadinessMinSharpness=50.0,
            visualReadinessMinSharpCellRatio=0.45,
            sampleQualityTimeoutMs=2000,
            sampleQualityMaxRecoveries=0,
        )
        service = RunOnceService(global_config=config, raw_config={"sceneMode": "day_visible"})
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 120, "height": 120},
        )
        sharp = _make_checkerboard_frame()
        readiness_outcome = VisualReadinessOutcome(
            metrics=VisualReadinessMetrics(ready=True, reason="visual_ready"),
            frames=[sharp.copy()],
            frameTimestampsMs=[0],
            frameCapturedAts=[10.0],
            streamType="flv",
            streamUrl="fake://stream",
            readyFrameIndex=0,
            confirmFrameIndex=0,
        )

        fake_session = _FakeSession([sharp, sharp, sharp, sharp], frame_interval_s=0.3)
        fake_session._base_time = 10.0
        sequence, guard_result = service._sample_with_quality_guard(
            session=fake_session,
            effective_config=config,
            target=target,
            readiness_outcome=readiness_outcome,
        )

        self.assertIsNone(sequence)
        self.assertFalse(guard_result.passed)
        self.assertEqual(guard_result.metrics.reason, "sample_quality_window_too_long")
        self.assertEqual(guard_result.metrics.lastFailureReason, "sample_quality_window_too_long")
        self.assertGreater(guard_result.metrics.sampleWindowMsActual, config.sampleDurationMs)
        self.assertEqual(
            guard_result.metrics.sampleWindowMaxAllowedMs,
            RunOnceService._sample_quality_max_allowed_window_ms(config),
        )
        self.assertTrue(guard_result.metrics.windowTooLongRejected)
        self.assertEqual(guard_result.metrics.windowTooLongCandidateFrameCount, 3)
        self.assertGreater(guard_result.metrics.windowTooLongTriggerSharpness or 0.0, 0.0)
        self.assertGreater(guard_result.metrics.windowTooLongTriggerClearCellRatio or 0.0, 0.0)
        self.assertGreaterEqual(guard_result.metrics.windowTooLongTriggerStability or 0.0, 0.0)

    def test_sample_quality_guard_reports_near_complete_but_broken_after_late_blur(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="night_ir",
            sampleDurationMs=400,
            sampleFps=10,
            sequenceFrameCount=4,
            visualReadinessMinSharpness=50.0,
            visualReadinessMinSharpCellRatio=0.45,
            sampleQualityTimeoutMs=1200,
            sampleQualityMaxRecoveries=1,
        )
        service = RunOnceService(global_config=config, raw_config={"sceneMode": "night_ir"})
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 120, "height": 120},
        )
        sharp = _make_checkerboard_frame()
        blurry = np.full_like(sharp, 127)
        readiness_outcome = VisualReadinessOutcome(
            metrics=VisualReadinessMetrics(ready=True, reason="visual_ready"),
            frames=[sharp.copy()],
            frameTimestampsMs=[0],
            frameCapturedAts=[10.0],
            streamType="flv",
            streamUrl="fake://stream",
            readyFrameIndex=0,
            confirmFrameIndex=0,
        )
        fake_session = _FakeSession([sharp, sharp, blurry], frame_interval_s=0.1)
        fake_session._base_time = 10.0

        sequence, guard_result = service._sample_with_quality_guard(
            session=fake_session,
            effective_config=config,
            target=target,
            readiness_outcome=readiness_outcome,
        )

        self.assertIsNone(sequence)
        self.assertFalse(guard_result.passed)
        self.assertEqual(guard_result.metrics.reason, "sample_quality_near_complete_but_broken")
        self.assertEqual(guard_result.metrics.lastFailureReason, "sample_quality_near_complete_but_broken")
        self.assertIsNotNone(guard_result.lastQualifiedFrame)

    def test_sample_quality_guard_reopens_session_once_after_stream_interruption_and_passes(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="night_ir",
            sampleDurationMs=400,
            sampleFps=10,
            sequenceFrameCount=4,
            visualReadinessMinSharpness=50.0,
            visualReadinessMinSharpCellRatio=0.45,
            sampleQualityTimeoutMs=2000,
            sampleQualityMaxRecoveries=2,
        )
        service = RunOnceService(global_config=config, raw_config={"sceneMode": "night_ir"})
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 120, "height": 120},
        )
        sharp = _make_checkerboard_frame()
        readiness_outcome = VisualReadinessOutcome(
            metrics=VisualReadinessMetrics(ready=True, reason="visual_ready"),
            frames=[sharp.copy()],
            frameTimestampsMs=[0],
            frameCapturedAts=[10.0],
            streamType="flv",
            streamUrl="fake://stream",
            readyFrameIndex=0,
            confirmFrameIndex=0,
        )
        first_session = _InterruptingSession([sharp], failure_reason="stream_eof", failure_count=3)
        first_session._base_time = 10.0
        reopened_session = _FakeSession([sharp, sharp, sharp, sharp], frame_interval_s=0.1)
        reopened_session._base_time = 10.2
        service.sampler.open_session = Mock(return_value=reopened_session)

        sequence, guard_result = service._sample_with_quality_guard(
            session=first_session,
            effective_config=config,
            target=target,
            readiness_outcome=readiness_outcome,
        )

        self.assertIsNotNone(sequence)
        self.assertTrue(guard_result.passed)
        self.assertEqual(guard_result.metrics.reason, "sample_quality_recovered_and_passed")
        self.assertEqual(guard_result.metrics.streamReadFailureReason, "stream_eof")
        self.assertEqual(guard_result.metrics.streamReadFailureCount, 3)
        self.assertTrue(guard_result.metrics.sampleQualityStreamRecovered)
        self.assertTrue(guard_result.metrics.sampleQualitySessionReopened)
        self.assertEqual(guard_result.metrics.sampleQualityStreamRetryCount, 1)
        self.assertIs(guard_result.activeSession, reopened_session)
        self.assertTrue(first_session.released)
        service.sampler.open_session.assert_called_once_with(device_id="device", channel_id="0")

    def test_sample_quality_guard_reports_stream_reason_when_reopen_fails(self) -> None:
        config = RecognitionGlobalConfig(
            sceneMode="night_ir",
            sampleDurationMs=400,
            sampleFps=10,
            sequenceFrameCount=4,
            visualReadinessMinSharpness=50.0,
            visualReadinessMinSharpCellRatio=0.45,
            sampleQualityTimeoutMs=2000,
            sampleQualityMaxRecoveries=2,
        )
        service = RunOnceService(global_config=config, raw_config={"sceneMode": "night_ir"})
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 120, "height": 120},
        )
        sharp = _make_checkerboard_frame()
        readiness_outcome = VisualReadinessOutcome(
            metrics=VisualReadinessMetrics(ready=True, reason="visual_ready"),
            frames=[sharp.copy()],
            frameTimestampsMs=[0],
            frameCapturedAts=[10.0],
            streamType="flv",
            streamUrl="fake://stream",
            readyFrameIndex=0,
            confirmFrameIndex=0,
        )
        first_session = _InterruptingSession([sharp], failure_reason="stream_read_timeout", failure_count=3)
        first_session._base_time = 10.0
        service.sampler.open_session = Mock(side_effect=FlvSamplerError("reopen failed", reason="stream_failed"))

        sequence, guard_result = service._sample_with_quality_guard(
            session=first_session,
            effective_config=config,
            target=target,
            readiness_outcome=readiness_outcome,
        )

        self.assertIsNone(sequence)
        self.assertFalse(guard_result.passed)
        self.assertEqual(guard_result.metrics.reason, "sample_quality_stream_read_timeout")
        self.assertEqual(guard_result.metrics.lastFailureReason, "sample_quality_stream_read_timeout")
        self.assertEqual(guard_result.metrics.streamReadFailureReason, "stream_read_timeout")
        self.assertEqual(guard_result.metrics.streamReadFailureCount, 3)
        self.assertFalse(guard_result.metrics.sampleQualityStreamRecovered)
        self.assertTrue(guard_result.metrics.sampleQualitySessionReopened)
        self.assertEqual(guard_result.metrics.sampleQualityStreamRetryCount, 1)
        self.assertIs(guard_result.activeSession, first_session)

    def test_run_once_auto_mode_uses_scene_specific_sample_quality_config(self) -> None:
        calibration = types.SimpleNamespace(
            deviceId="device",
            channelId="0",
            targetId="target",
            targetName="target",
            presetIndex=1,
            presetName="preset",
            roi={"x": 0, "y": 0, "width": 16, "height": 16},
            focusAnchorRoi={"x": 4, "y": 4, "width": 8, "height": 8},
            notes="",
            snapshotPath="snapshots/demo.png",
            snapshotUrl="/artifacts/snapshots/demo.png",
            updatedAt="2026-01-01T00:00:00+00:00",
        )
        config = RecognitionGlobalConfig(
            sceneMode="auto",
            visualReadinessEnabled=True,
            sceneAutoFrameCount=2,
            streamStartupFreshnessEnabled=False,
            visualReadinessMinFrames=2,
            visualReadinessMinElapsedMs=0,
            visualReadinessMinReadyWindowMs=0,
            sampleDurationMs=400,
            sampleFps=10,
            sequenceFrameCount=4,
            sampleQualityTimeoutMs=4500,
            sampleQualityMaxRecoveries=2,
        )
        service = RunOnceService(
            global_config=config,
            raw_config={
                "sceneMode": "auto",
                "sampleQualityTimeoutMs": 4500,
                "sampleQualityMaxRecoveries": 2,
                "dayVisible": {
                    "algorithmVersion": "test-day",
                    "sampleQualityTimeoutMs": 3200,
                    "sampleQualityMaxRecoveries": 1,
                },
                "nightIr": {
                    "algorithmVersion": "test-night",
                    "sampleQualityTimeoutMs": 5700,
                    "sampleQualityMaxRecoveries": 3,
                    "visualReadinessMinSharpness": 90.0,
                    "visualReadinessNightPostReadyRecheckFrames": 2,
                    "visualReadinessNightPostReadyRecheckWindowMs": 180,
                },
            },
        )
        service.scene_mode_stability_guard.observe = Mock(
            return_value=self._scene_mode_stability_result(
                stable=True,
                classification="night_ir",
                suggested_mode="night_ir",
            )
        )

        ir_frame = np.full((24, 24, 3), 120, dtype=np.uint8)
        fake_session = _FakeSession([ir_frame, ir_frame, ir_frame, ir_frame], frame_interval_s=0.12)
        service.sampler.open_session = Mock(return_value=fake_session)
        service.replay_store.persist_async = Mock(
            return_value=(
                {
                    "metadataPath": "replays/replay-metadata.json",
                    "statusPath": "replays/replay-save-status.json",
                },
                ReplaySaveState(status="pending", statusPath="replays/replay-save-status.json", message="scheduled"),
            )
        )

        def _fake_wait_until_ready(checker_self, readiness_session, *, roi=None):  # noqa: ANN001
            self.assertEqual(checker_self.global_config.sceneMode, "night_ir")
            self.assertEqual(checker_self.global_config.visualReadinessNightPostReadyRecheckFrames, 2)
            self.assertEqual(checker_self.global_config.visualReadinessNightPostReadyRecheckWindowMs, 180)
            return VisualReadinessOutcome(
                metrics=VisualReadinessMetrics(ready=True, reason="visual_ready"),
                frames=[ir_frame.copy()],
                frameTimestampsMs=[0],
                frameCapturedAts=[100.0],
                streamType="flv",
                streamUrl="fake://stream",
                readyFrameIndex=0,
                confirmFrameIndex=0,
            )

        def _fake_sample_quality_guard(*, session, effective_config, target, readiness_outcome, focus_anchor_roi):  # noqa: ANN001
            self.assertEqual(effective_config.sceneMode, "night_ir")
            self.assertEqual(effective_config.sampleQualityTimeoutMs, 5700)
            self.assertEqual(effective_config.sampleQualityMaxRecoveries, 3)
            self.assertEqual(focus_anchor_roi.model_dump(), calibration.focusAnchorRoi)
            return None, __import__("types").SimpleNamespace(
                passed=False,
                sequence=None,
                metrics=SampleQualityMetrics(
                    passed=False,
                    reason="sample_quality_timeout",
                    elapsedMs=100,
                ),
                streamType="flv",
                streamUrl="fake://stream",
                activeSession=session,
                attemptStartFrame=None,
                degradedFrame=None,
                lastQualifiedFrame=None,
                acceptedMiddleFrame=None,
                acceptedEndFrame=None,
                observedFrames=[ir_frame.copy()],
                observedTimestampsMs=[0],
            )

        fake_preset_module = types.ModuleType("app.services.dahua_preset_service")
        fake_preset_module.preset_service = types.SimpleNamespace(turn_preset=Mock(return_value=None))
        fake_sign_module = types.ModuleType("app.utils.request_sign_adapter")
        fake_sign_module.DahuaApiError = type("DahuaApiError", (Exception,), {})

        with patch("inspector.run_once_service.storage_service.load_path", return_value=calibration):
            with patch("app.config.Settings.is_dahua_configured", new_callable=PropertyMock, return_value=True):
                with patch.dict(
                    sys.modules,
                    {
                        "app.services.dahua_preset_service": fake_preset_module,
                        "app.utils.request_sign_adapter": fake_sign_module,
                    },
                ):
                    with patch.object(
                        VisualReadinessChecker,
                        "wait_until_ready",
                        autospec=True,
                        side_effect=_fake_wait_until_ready,
                    ):
                        with patch.object(
                            service,
                            "_sample_with_quality_guard",
                            side_effect=_fake_sample_quality_guard,
                        ):
                            result = service.run(config_path=Path("demo.json"), requested_preset_index=1)

        self.assertEqual(result.executionResult, "sample_quality_timeout")
        self.assertEqual(result.effectiveSceneMode, "night_ir")


    def test_run_once_reopens_once_when_startup_session_reports_stream_failure(self) -> None:
        calibration = types.SimpleNamespace(
            deviceId="device",
            channelId="0",
            targetId="target",
            targetName="target",
            presetIndex=1,
            presetName="preset",
            roi={"x": 0, "y": 0, "width": 16, "height": 16},
            focusAnchorRoi=None,
            notes="",
            snapshotPath="snapshots/demo.png",
            snapshotUrl="/artifacts/snapshots/demo.png",
            updatedAt="2026-01-01T00:00:00+00:00",
        )
        config = RecognitionGlobalConfig(
            sceneMode="night_ir",
            visualReadinessEnabled=False,
            streamStartupFreshnessEnabled=False,
            sampleDurationMs=400,
            sampleFps=10,
            sequenceFrameCount=4,
        )
        service = RunOnceService(global_config=config, raw_config={"sceneMode": "night_ir"})
        first_session = _FakeSession([])
        first_session.lastReadFailureReason = "stream_read_timeout"
        first_session.lastReadFailureCount = 1
        first_session.lastReadCallElapsedMs = 3100
        reopened_session = _FakeSession([np.zeros((24, 24, 3), dtype=np.uint8)])
        sequence = SampledSequence(
            streamType="flv",
            streamUrl="fake://stream",
            frames=np.zeros((4, 24, 24, 3), dtype=np.uint8),
            frameTimestampsMs=[0, 100, 200, 300],
            targetFrameCount=4,
            sampledFrameCount=4,
            configuredSampleFps=10.0,
            actualSampleFps=10.0,
            configuredSampleDurationMs=400,
            actualSampleDurationMs=300,
            frameWidth=24,
            frameHeight=24,
        )
        guard_result = types.SimpleNamespace(
            passed=True,
            sequence=sequence,
            metrics=SampleQualityMetrics(passed=True, reason="sample_quality_passed"),
            activeSession=None,
            attemptStartFrame=None,
            degradedFrame=None,
            lastQualifiedFrame=None,
            acceptedMiddleFrame=None,
            acceptedEndFrame=None,
            observedFrames=None,
            observedTimestampsMs=None,
        )
        service.sampler.open_session = Mock(side_effect=[first_session, reopened_session])
        service._sample_with_quality_guard = Mock(return_value=(sequence, guard_result))
        service.replay_store.persist_async = Mock(
            return_value=({}, ReplaySaveState(status="disabled", message="not needed"))
        )
        fake_preset_module = types.ModuleType("app.services.dahua_preset_service")
        fake_preset_module.preset_service = types.SimpleNamespace(turn_preset=Mock(return_value=None))
        fake_sign_module = types.ModuleType("app.utils.request_sign_adapter")
        fake_sign_module.DahuaApiError = type("DahuaApiError", (Exception,), {})

        with patch("inspector.run_once_service.storage_service.load_path", return_value=calibration):
            with patch("app.config.Settings.is_dahua_configured", new_callable=PropertyMock, return_value=True):
                with patch.dict(
                    sys.modules,
                    {
                        "app.services.dahua_preset_service": fake_preset_module,
                        "app.utils.request_sign_adapter": fake_sign_module,
                    },
                ):
                    result = service.run(config_path=Path("demo.json"), requested_preset_index=1)

        self.assertEqual(result.executionResult, "success")
        self.assertTrue(result.preReadinessSessionReopened)
        self.assertTrue(result.preReadinessStreamRecovered)
        self.assertEqual(result.preReadinessStreamRetryCount, 1)
        self.assertEqual(result.streamReadFailureReason, "stream_read_timeout")
        self.assertEqual(result.streamReadCallElapsedMs, 3100)
        self.assertEqual(service.sampler.open_session.call_count, 2)

    def test_run_once_reports_stream_timeout_when_pre_readiness_reopen_fails(self) -> None:
        calibration = types.SimpleNamespace(
            deviceId="device",
            channelId="0",
            targetId="target",
            targetName="target",
            presetIndex=1,
            presetName="preset",
            roi={"x": 0, "y": 0, "width": 16, "height": 16},
            focusAnchorRoi=None,
            notes="",
            snapshotPath="snapshots/demo.png",
            snapshotUrl="/artifacts/snapshots/demo.png",
            updatedAt="2026-01-01T00:00:00+00:00",
        )
        config = RecognitionGlobalConfig(sceneMode="night_ir", visualReadinessEnabled=False, streamStartupFreshnessEnabled=False)
        service = RunOnceService(global_config=config, raw_config={"sceneMode": "night_ir"})
        first_session = _FakeSession([])
        first_session.lastReadFailureReason = "stream_read_timeout"
        first_session.lastReadFailureCount = 1
        first_session.lastReadCallElapsedMs = 3100
        service.sampler.open_session = Mock(
            side_effect=[first_session, FlvSamplerError("reopen unavailable", reason="stream_failed")]
        )
        fake_preset_module = types.ModuleType("app.services.dahua_preset_service")
        fake_preset_module.preset_service = types.SimpleNamespace(turn_preset=Mock(return_value=None))
        fake_sign_module = types.ModuleType("app.utils.request_sign_adapter")
        fake_sign_module.DahuaApiError = type("DahuaApiError", (Exception,), {})

        with patch("inspector.run_once_service.storage_service.load_path", return_value=calibration):
            with patch("app.config.Settings.is_dahua_configured", new_callable=PropertyMock, return_value=True):
                with patch.dict(
                    sys.modules,
                    {
                        "app.services.dahua_preset_service": fake_preset_module,
                        "app.utils.request_sign_adapter": fake_sign_module,
                    },
                ):
                    result = service.run(config_path=Path("demo.json"), requested_preset_index=1)

        self.assertEqual(result.executionResult, "stream_read_timeout")
        self.assertNotEqual(result.executionResult, "scene_mode_transition_timeout")
        self.assertTrue(result.preReadinessSessionReopened)
        self.assertFalse(result.preReadinessStreamRecovered)


class PseudoMultiPointSummaryTests(unittest.TestCase):
    def test_summary_counts_visual_readiness_and_static_bright_rounds(self) -> None:
        rounds = [
            PseudoMultiPointRoundResult(
                roundIndex=1,
                startedAt="2026-01-01T00:00:00+00:00",
                finishedAt="2026-01-01T00:00:01+00:00",
                status="failed",
                expectedVisualState="no_splash",
                roundElapsedMs=1000,
                roundTimeoutSeconds=25.0,
                transitionSettleMsConfigured=1800,
                transitionSettleWaitMsActual=1800,
                transitionPreset={"presetIndex": 2, "elapsedMs": 100, "timeoutSeconds": 5.0},
                recognitionExecutionResult="visual_not_ready_timeout",
                visualReadinessPassed=False,
                visualReadinessReason="visual_not_ready_timeout",
                staticBrightInterferenceSuppressed=False,
                failureStep="visual_readiness",
            ),
            PseudoMultiPointRoundResult(
                roundIndex=2,
                startedAt="2026-01-01T00:00:01+00:00",
                finishedAt="2026-01-01T00:00:02+00:00",
                status="failed",
                expectedVisualState="no_splash",
                roundElapsedMs=1000,
                roundTimeoutSeconds=25.0,
                transitionSettleMsConfigured=1800,
                transitionSettleWaitMsActual=1800,
                transitionPreset={"presetIndex": 2, "elapsedMs": 100, "timeoutSeconds": 5.0},
                recognitionExecutionResult="visual_blurry_before_detection",
                visualReadinessPassed=False,
                visualReadinessReason="visual_not_ready_blurry_and_unstable",
                staticBrightInterferenceSuppressed=False,
                failureStep="visual_readiness",
            ),
            PseudoMultiPointRoundResult(
                roundIndex=3,
                startedAt="2026-01-01T00:00:03+00:00",
                finishedAt="2026-01-01T00:00:04+00:00",
                status="failed",
                expectedVisualState="no_splash",
                expectedVisualStateMatched=False,
                actualVisualState="undetermined",
                roundElapsedMs=1000,
                roundTimeoutSeconds=25.0,
                transitionSettleMsConfigured=1800,
                transitionSettleWaitMsActual=1800,
                transitionPreset={"presetIndex": 2, "elapsedMs": 100, "timeoutSeconds": 5.0},
                recognitionExecutionResult="sample_quality_timeout",
                sampleQualityPassed=False,
                sampleQualityReason="sample_quality_near_complete_but_broken",
                visualReadinessPassed=True,
                staticBrightInterferenceSuppressed=False,
                failureStep="sample_quality",
            ),
        ]
        summary = build_summary(
            run_status="completed",
            run_id="demo",
            run_dir=__import__("pathlib").Path("demo"),
            started_at="2026-01-01T00:00:00+00:00",
            total_started_perf=0.0,
            runtime_config=__import__("types").SimpleNamespace(
                rounds=3,
                expectedVisualState="no_splash",
                configPath=__import__("pathlib").Path("demo.json"),
                transitionPresetIndex=2,
                sceneModeOverride=None,
                transitionPresetTimeoutSeconds=5.0,
                transitionSettleMs=1800,
                roundTimeoutSeconds=25.0,
            ),
            recognition_preset_index=1,
            effective_requested_scene_mode="day_visible",
            rounds=rounds,
            message=None,
        )

        self.assertEqual(summary.visualReadinessFailedRounds, 2)
        self.assertEqual(summary.visualBlurryBeforeDetectionRounds, 1)
        self.assertEqual(summary.visualNotReadyTimeoutRounds, 1)
        self.assertEqual(summary.staticBrightInterferenceSuppressedRounds, 0)
        self.assertEqual(
            summary.recognitionExecutionBreakdown,
            {"sample_quality_timeout": 1, "visual_blurry_before_detection": 1, "visual_not_ready_timeout": 1},
        )
        self.assertEqual(
            summary.visualReadinessFailureReasons,
            {"visual_not_ready_blurry_and_unstable": 1, "visual_not_ready_timeout": 1},
        )
        self.assertEqual(
            summary.sampleQualityFailureReasons,
            {"sample_quality_near_complete_but_broken": 1},
        )


class PseudoMultiPointRunnerTests(unittest.TestCase):
    @staticmethod
    def _successful_round_recognition_result() -> RecognitionRunResult:
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 1, "height": 1},
        )
        return RecognitionRunResult(
            executionResult="success",
            visualState="no_splash",
            sceneMode="night_ir",
            requestedSceneMode="night_ir",
            effectiveSceneMode="night_ir",
            sceneModeConfidence=1.0,
            sceneModeReason="test",
            sceneModeFallbackUsed=False,
            sceneModeDiagnostics=None,
            dayVisibleVisualState=None,
            nightIrVisualState="no_splash",
            fallbackResolution="not_needed",
            scoreSummary=RecognitionScoreSummary(),
            evidencePaths=RecognitionEvidencePaths(calibrationPath="demo.json"),
            replaySave=ReplaySaveState(status="disabled", message="not needed"),
            timing=RecognitionTiming(),
            algorithmVersion="test",
            configPath="demo.json",
            target=target,
        )

    def test_round_timeout_warn_only_preserves_correct_recognition_and_reports_slo(self) -> None:
        runtime_config = PseudoMultiPointRuntimeConfig(
            configPath=Path("demo.json"),
            transitionPresetIndex=2,
            transitionSettleMs=0,
            rounds=1,
            expectedVisualState="no_splash",
            sceneModeOverride=None,
            transitionPresetTimeoutSeconds=5.0,
            roundTimeoutSeconds=0.001,
            outputRoot=Path("data/pseudo_multi_point_tests"),
        )
        runner = PseudoMultiPointRunner(
            runtime_config,
            run_once_service=_FakeRunOnceService(self._successful_round_recognition_result()),
            transition_turner=_FakeTransitionTurner(),
        )
        round_result = runner._finalize_round(
            round_index=1,
            started_at="2026-01-01T00:00:00+00:00",
            round_started_perf=perf_counter() - 0.1,
            transition_result=TransitionPresetStepResult(presetIndex=2, elapsedMs=1, timeoutSeconds=5.0),
            transition_settle_wait_ms_actual=0,
            recognition_result=self._successful_round_recognition_result(),
            expected_visual_state="no_splash",
            expected_matched=True,
            actual_visual_state="no_splash",
            failure_step=None,
            failure_reason=None,
            status="success",
        )

        self.assertEqual(round_result.status, "success")
        self.assertTrue(round_result.expectedVisualStateMatched)
        self.assertTrue(round_result.roundTimedOut)
        self.assertTrue(round_result.timingSloExceeded)
        self.assertFalse(round_result.strictTimeoutFailed)
        self.assertIsNone(round_result.failureStep)

    def test_round_timeout_fail_turns_correct_recognition_into_strict_timeout_failure(self) -> None:
        runtime_config = PseudoMultiPointRuntimeConfig(
            configPath=Path("demo.json"),
            transitionPresetIndex=2,
            transitionSettleMs=0,
            rounds=1,
            expectedVisualState="no_splash",
            sceneModeOverride=None,
            transitionPresetTimeoutSeconds=5.0,
            roundTimeoutSeconds=0.001,
            outputRoot=Path("data/pseudo_multi_point_tests"),
            roundTimeoutPolicy="fail",
        )
        runner = PseudoMultiPointRunner(
            runtime_config,
            run_once_service=_FakeRunOnceService(self._successful_round_recognition_result()),
            transition_turner=_FakeTransitionTurner(),
        )
        round_result = runner._finalize_round(
            round_index=1,
            started_at="2026-01-01T00:00:00+00:00",
            round_started_perf=perf_counter() - 0.1,
            transition_result=TransitionPresetStepResult(presetIndex=2, elapsedMs=1, timeoutSeconds=5.0),
            transition_settle_wait_ms_actual=0,
            recognition_result=self._successful_round_recognition_result(),
            expected_visual_state="no_splash",
            expected_matched=True,
            actual_visual_state="no_splash",
            failure_step=None,
            failure_reason=None,
            status="success",
        )

        self.assertEqual(round_result.status, "failed")
        self.assertTrue(round_result.timingSloExceeded)
        self.assertTrue(round_result.strictTimeoutFailed)
        self.assertEqual(round_result.failureStep, "round_timeout")

    def test_warn_only_timeout_with_recognition_failure_uses_distinct_console_outcome(self) -> None:
        round_result = PseudoMultiPointRoundResult(
            roundIndex=1,
            startedAt="2026-01-01T00:00:00+00:00",
            finishedAt="2026-01-01T00:00:26+00:00",
            status="failed",
            expectedVisualState="no_splash",
            expectedVisualStateMatched=False,
            actualVisualState="undetermined",
            failureStep="recognition_execution",
            failureReason="stream failed",
            roundElapsedMs=26000,
            roundTimedOut=True,
            roundTimeoutSeconds=25.0,
            roundTimeoutPolicy="warn_only",
            timingSloExceeded=True,
            timingSloReason="Round exceeded timing SLO 25.00s (elapsed 26000 ms).",
            strictTimeoutFailed=False,
            transitionSettleMsConfigured=0,
            transitionSettleWaitMsActual=0,
            transitionPreset=TransitionPresetStepResult(presetIndex=2, elapsedMs=1, timeoutSeconds=5.0),
        )

        with redirect_stderr(io.StringIO()) as output:
            emit_round_progress(round_result)

        self.assertIn("timingOutcome=over_slo_with_recognition_failure", output.getvalue())

    def test_pending_replay_save_keeps_target_paths_separate_from_ready_paths(self) -> None:
        calibration_target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 1, "height": 1},
        )
        result = RecognitionRunResult(
            executionResult="success",
            visualState="no_splash",
            sceneMode="day_visible",
            requestedSceneMode="day_visible",
            effectiveSceneMode="day_visible",
            effectiveSceneProfile="day_visible_twilight",
            sceneModeConfidence=1.0,
            sceneModeReason="test",
            sceneModeFallbackUsed=False,
            sceneModeDiagnostics=None,
            twilightProfileApplied=True,
            twilightProfileReason="brightness_low_but_day_visible_signals_remain_strong",
            twilightBrightnessMean=94.0,
            dayVisibleVisualState="no_splash",
            nightIrVisualState=None,
            fallbackResolution="not_needed",
            visualReadinessPassed=True,
            visualReadinessReason="visual_ready",
            visualReadiness=None,
            sampleQualityMaxRecoveriesConfigured=4,
            sampleQualityRecoveryCountSemantics="restart_attempts_including_budget_exhausting_restart",
            scoreSummary=RecognitionScoreSummary(),
            evidencePaths=RecognitionEvidencePaths(
                calibrationPath="demo.json",
                representativeFramePath="pending/representative-frame.ppm",
                debugImagePath="pending/motion-debug.pgm",
            ),
            replaySave=ReplaySaveState(
                status="pending",
                statusPath="pending/replay-save-status.json",
                message="scheduled",
            ),
            timing=RecognitionTiming(),
            algorithmVersion="test",
            configPath="demo.json",
            target=calibration_target,
            message="ok",
        )
        runner = PseudoMultiPointRunner(
            PseudoMultiPointRuntimeConfig(
                configPath=Path("demo.json"),
                transitionPresetIndex=2,
                transitionSettleMs=0,
                rounds=1,
                expectedVisualState="no_splash",
                sceneModeOverride=None,
                transitionPresetTimeoutSeconds=5.0,
                roundTimeoutSeconds=25.0,
                outputRoot=Path("C:/Users/Maple_Rain/Documents/Items/splash_water/data/pseudo_multi_point_tests"),
            ),
            run_once_service=_FakeRunOnceService(result),
            transition_turner=_FakeTransitionTurner(),
        )

        with patch("inspector.pseudo_multi_point_test.storage_service.load_path") as mock_load_path:
            mock_load_path.return_value = calibration_target
            with redirect_stderr(io.StringIO()):
                rounds, interrupt = runner.execute()

        self.assertIsNone(interrupt)
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0].replaySaveStatus, "pending")
        self.assertFalse(rounds[0].replayEvidenceReady)
        self.assertEqual(rounds[0].replaySaveStatusPath, "pending/replay-save-status.json")
        self.assertEqual(rounds[0].recognitionEffectiveSceneProfile, "day_visible_twilight")
        self.assertTrue(rounds[0].twilightProfileApplied)
        self.assertEqual(rounds[0].twilightProfileReason, "brightness_low_but_day_visible_signals_remain_strong")
        self.assertEqual(rounds[0].twilightBrightnessMean, 94.0)
        self.assertEqual(rounds[0].sampleQualityMaxRecoveriesConfigured, 4)
        self.assertEqual(
            rounds[0].sampleQualityRecoveryCountSemantics,
            "restart_attempts_including_budget_exhausting_restart",
        )
        self.assertIsNone(rounds[0].representativeFramePath)
        self.assertIsNone(rounds[0].debugImagePath)
        self.assertEqual(rounds[0].representativeFrameTargetPath, "pending/representative-frame.ppm")
        self.assertEqual(rounds[0].debugImageTargetPath, "pending/motion-debug.pgm")

    def test_round_result_surfaces_stream_startup_freshness_fields(self) -> None:
        calibration_target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 1, "height": 1},
        )
        result = RecognitionRunResult(
            executionResult="sample_quality_timeout",
            visualState="undetermined",
            sceneMode="day_visible",
            requestedSceneMode="day_visible",
            effectiveSceneMode="day_visible",
            sceneModeConfidence=1.0,
            sceneModeReason="test",
            sceneModeFallbackUsed=False,
            sceneModeDiagnostics=None,
            dayVisibleVisualState=None,
            nightIrVisualState=None,
            fallbackResolution="not_needed",
            streamStartupFreshness=StreamStartupFreshnessMetrics(
                enabled=True,
                consumedFrames=4,
                elapsedMs=380,
                jumpDetected=True,
                stableAfterJump=True,
                exitReason="jump_and_stable",
            ),
            visualReadinessPassed=True,
            visualReadinessReason="visual_ready",
            visualReadiness=None,
            sampleQualityPassed=False,
            sampleQualityReason="sample_quality_timeout",
            sampleQuality=SampleQualityMetrics(
                passed=False,
                reason="sample_quality_timeout",
                rejectSharpnessCount=2,
                rejectClearCellRatioCount=2,
                rejectStabilityCount=1,
                firstRejectedFrameIndex=6,
                firstRejectedElapsedMs=720,
                firstRejectedSharpness=44.0,
                firstRejectedClearCellRatio=0.22,
                firstRejectedStability=0.08,
                lastRejectedFrameIndex=19,
                lastRejectedElapsedMs=2120,
                lastRejectedSharpness=48.0,
                lastRejectedClearCellRatio=0.31,
                lastRejectedStability=0.21,
            ),
            scoreSummary=RecognitionScoreSummary(),
            evidencePaths=RecognitionEvidencePaths(
                calibrationPath="demo.json",
                streamStartupStartFramePath="pending/stream-startup-start.ppm",
                streamStartupSettledFramePath="pending/stream-startup-settled.ppm",
            ),
            replaySave=ReplaySaveState(
                status="pending",
                statusPath="pending/replay-save-status.json",
                message="scheduled",
            ),
            timing=RecognitionTiming(),
            algorithmVersion="test",
            configPath="demo.json",
            target=calibration_target,
            message="timeout",
        )
        runner = PseudoMultiPointRunner(
            PseudoMultiPointRuntimeConfig(
                configPath=Path("demo.json"),
                transitionPresetIndex=2,
                transitionSettleMs=0,
                rounds=1,
                expectedVisualState="no_splash",
                sceneModeOverride=None,
                transitionPresetTimeoutSeconds=5.0,
                roundTimeoutSeconds=25.0,
                outputRoot=Path("C:/Users/Maple_Rain/Documents/Items/splash_water/data/pseudo_multi_point_tests"),
            ),
            run_once_service=_FakeRunOnceService(result),
            transition_turner=_FakeTransitionTurner(),
        )

        with patch("inspector.pseudo_multi_point_test.storage_service.load_path") as mock_load_path:
            mock_load_path.return_value = calibration_target
            with redirect_stderr(io.StringIO()):
                rounds, interrupt = runner.execute()

        self.assertIsNone(interrupt)
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0].streamStartupFreshnessExitReason, "jump_and_stable")
        self.assertEqual(rounds[0].streamStartupFreshnessConsumedFrames, 4)
        self.assertEqual(rounds[0].streamStartupFreshnessElapsedMs, 380)
        self.assertTrue(rounds[0].streamStartupFreshnessJumpDetected)
        self.assertTrue(rounds[0].streamStartupFreshnessStableAfterJump)
        self.assertEqual(rounds[0].streamStartupStartFrameTargetPath, "pending/stream-startup-start.ppm")
        self.assertEqual(rounds[0].streamStartupSettledFrameTargetPath, "pending/stream-startup-settled.ppm")
        self.assertEqual(rounds[0].sampleQualityRejectSharpnessCount, 2)
        self.assertEqual(rounds[0].sampleQualityRejectClearCellRatioCount, 2)
        self.assertEqual(rounds[0].sampleQualityRejectStabilityCount, 1)
        self.assertEqual(rounds[0].sampleQualityFirstRejectedFrameIndex, 6)
        self.assertEqual(rounds[0].sampleQualityLastRejectedFrameIndex, 19)
        self.assertEqual(rounds[0].sampleQualityFirstRejectedSharpness, 44.0)
        self.assertEqual(rounds[0].sampleQualityLastRejectedStability, 0.21)

    def test_visual_not_ready_execution_maps_to_visual_readiness_failure_step(self) -> None:
        calibration_target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 1, "height": 1},
        )
        result = RecognitionRunResult(
            executionResult="visual_not_ready",
            visualState="undetermined",
            sceneMode="day_visible",
            requestedSceneMode="day_visible",
            effectiveSceneMode="day_visible",
            sceneModeConfidence=1.0,
            sceneModeReason="test",
            sceneModeFallbackUsed=False,
            sceneModeDiagnostics=None,
            dayVisibleVisualState=None,
            nightIrVisualState=None,
            fallbackResolution="not_needed",
            visualReadinessPassed=False,
            visualReadinessReason="visual_not_ready_blurry",
            visualReadiness=None,
            scoreSummary=RecognitionScoreSummary(),
            evidencePaths=RecognitionEvidencePaths(calibrationPath="demo.json"),
            replaySave=ReplaySaveState(status="disabled", message="not needed"),
            timing=RecognitionTiming(),
            algorithmVersion="test",
            configPath="demo.json",
            target=calibration_target,
            message="Visual readiness gate did not pass: visual_not_ready_blurry",
        )
        runner = PseudoMultiPointRunner(
            PseudoMultiPointRuntimeConfig(
                configPath=Path("demo.json"),
                transitionPresetIndex=2,
                transitionSettleMs=0,
                rounds=1,
                expectedVisualState="no_splash",
                sceneModeOverride=None,
                transitionPresetTimeoutSeconds=5.0,
                roundTimeoutSeconds=25.0,
                outputRoot=Path("C:/Users/Maple_Rain/Documents/Items/splash_water/data/pseudo_multi_point_tests"),
            ),
            run_once_service=_FakeRunOnceService(result),
            transition_turner=_FakeTransitionTurner(),
        )

        with patch("inspector.pseudo_multi_point_test.storage_service.load_path") as mock_load_path:
            mock_load_path.return_value = calibration_target
            with redirect_stderr(io.StringIO()):
                rounds, interrupt = runner.execute()

        self.assertIsNone(interrupt)
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0].failureStep, "visual_readiness")
        self.assertEqual(rounds[0].recognitionExecutionResult, "visual_not_ready")

    def test_visual_blurry_before_detection_maps_to_visual_readiness_failure_step(self) -> None:
        calibration_target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 1, "height": 1},
        )
        result = RecognitionRunResult(
            executionResult="visual_blurry_before_detection",
            visualState="undetermined",
            sceneMode="day_visible",
            requestedSceneMode="day_visible",
            effectiveSceneMode="day_visible",
            sceneModeConfidence=1.0,
            sceneModeReason="test",
            sceneModeFallbackUsed=False,
            sceneModeDiagnostics=None,
            dayVisibleVisualState=None,
            nightIrVisualState=None,
            fallbackResolution="not_needed",
            visualReadinessPassed=False,
            visualReadinessReason="visual_not_ready_blurry_and_unstable",
            visualReadiness=None,
            scoreSummary=RecognitionScoreSummary(),
            evidencePaths=RecognitionEvidencePaths(calibrationPath="demo.json"),
            replaySave=ReplaySaveState(status="disabled", message="not needed"),
            timing=RecognitionTiming(),
            algorithmVersion="test",
            configPath="demo.json",
            target=calibration_target,
            message="Visual readiness gate did not pass: visual_not_ready_blurry_and_unstable",
        )
        runner = PseudoMultiPointRunner(
            PseudoMultiPointRuntimeConfig(
                configPath=Path("demo.json"),
                transitionPresetIndex=2,
                transitionSettleMs=0,
                rounds=1,
                expectedVisualState="no_splash",
                sceneModeOverride=None,
                transitionPresetTimeoutSeconds=5.0,
                roundTimeoutSeconds=25.0,
                outputRoot=Path("C:/Users/Maple_Rain/Documents/Items/splash_water/data/pseudo_multi_point_tests"),
            ),
            run_once_service=_FakeRunOnceService(result),
            transition_turner=_FakeTransitionTurner(),
        )

        with patch("inspector.pseudo_multi_point_test.storage_service.load_path") as mock_load_path:
            mock_load_path.return_value = calibration_target
            with redirect_stderr(io.StringIO()):
                rounds, interrupt = runner.execute()

        self.assertIsNone(interrupt)
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0].failureStep, "visual_readiness")
        self.assertEqual(rounds[0].recognitionExecutionResult, "visual_blurry_before_detection")

    def test_scene_mode_transition_timeout_maps_to_scene_mode_transition_failure_step(self) -> None:
        calibration_target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 1, "height": 1},
        )
        result = RecognitionRunResult(
            executionResult="scene_mode_transition_timeout",
            visualState="undetermined",
            sceneMode="auto",
            requestedSceneMode="auto",
            effectiveSceneMode="night_ir",
            sceneModeConfidence=0.4,
            sceneModeReason="scene_mode_transition_timeout",
            sceneModeFallbackUsed=False,
            sceneModeDiagnostics=None,
            sceneModeStability=SceneModeStabilityMetrics(
                enabled=True,
                sceneModeInitial="day_visible",
                sceneModeFinal="night_ir",
                sceneModeStable=False,
                sceneModeStabilityElapsedMs=900,
                sceneModeStabilityWindowCount=2,
                sceneModeTransitionObserved=True,
                sceneModeRelockCount=0,
                sceneModeRelockReason=None,
                sceneModeTransitionTimeout=True,
            ),
            dayVisibleVisualState=None,
            nightIrVisualState=None,
            fallbackResolution="not_needed",
            scoreSummary=RecognitionScoreSummary(),
            evidencePaths=RecognitionEvidencePaths(calibrationPath="demo.json"),
            replaySave=ReplaySaveState(status="disabled", message="not needed"),
            timing=RecognitionTiming(),
            algorithmVersion="test",
            configPath="demo.json",
            target=calibration_target,
            message="Scene mode did not settle before visual readiness.",
        )
        runner = PseudoMultiPointRunner(
            PseudoMultiPointRuntimeConfig(
                configPath=Path("demo.json"),
                transitionPresetIndex=2,
                transitionSettleMs=0,
                rounds=1,
                expectedVisualState="no_splash",
                sceneModeOverride=None,
                transitionPresetTimeoutSeconds=5.0,
                roundTimeoutSeconds=25.0,
                outputRoot=Path("C:/Users/Maple_Rain/Documents/Items/splash_water/data/pseudo_multi_point_tests"),
            ),
            run_once_service=_FakeRunOnceService(result),
            transition_turner=_FakeTransitionTurner(),
        )

        with patch("inspector.pseudo_multi_point_test.storage_service.load_path") as mock_load_path:
            mock_load_path.return_value = calibration_target
            with redirect_stderr(io.StringIO()):
                rounds, interrupt = runner.execute()

        self.assertIsNone(interrupt)
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0].failureStep, "scene_mode_transition")
        self.assertTrue(rounds[0].sceneModeTransitionTimeout)

    def test_scene_mode_probe_incomplete_maps_to_scene_mode_transition_failure_step(self) -> None:
        calibration_target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 1, "height": 1},
        )
        result = RecognitionRunResult(
            executionResult="scene_mode_probe_incomplete",
            visualState="undetermined",
            sceneMode="auto",
            requestedSceneMode="auto",
            effectiveSceneMode="night_ir",
            sceneModeConfidence=0.4,
            sceneModeReason="scene_mode_probe_incomplete",
            sceneModeFallbackUsed=False,
            sceneModeDiagnostics=None,
            sceneModeStability=SceneModeStabilityMetrics(
                enabled=True,
                sceneModeInitial=None,
                sceneModeFinal="night_ir",
                sceneModeStable=False,
                sceneModeStabilityElapsedMs=220,
                sceneModeStabilityWindowCount=0,
                sceneModeTransitionObserved=False,
                sceneModeRelockCount=0,
                sceneModeRelockReason=None,
                sceneModeTransitionTimeout=False,
            ),
            dayVisibleVisualState=None,
            nightIrVisualState=None,
            fallbackResolution="not_needed",
            scoreSummary=RecognitionScoreSummary(),
            evidencePaths=RecognitionEvidencePaths(calibrationPath="demo.json"),
            replaySave=ReplaySaveState(status="disabled", message="not needed"),
            timing=RecognitionTiming(),
            algorithmVersion="test",
            configPath="demo.json",
            target=calibration_target,
            message="Scene mode probe ended before collecting a full stability window.",
        )
        runner = PseudoMultiPointRunner(
            PseudoMultiPointRuntimeConfig(
                configPath=Path("demo.json"),
                transitionPresetIndex=2,
                transitionSettleMs=0,
                rounds=1,
                expectedVisualState="no_splash",
                sceneModeOverride=None,
                transitionPresetTimeoutSeconds=5.0,
                roundTimeoutSeconds=25.0,
                outputRoot=Path("C:/Users/Maple_Rain/Documents/Items/splash_water/data/pseudo_multi_point_tests"),
            ),
            run_once_service=_FakeRunOnceService(result),
            transition_turner=_FakeTransitionTurner(),
        )

        with patch("inspector.pseudo_multi_point_test.storage_service.load_path") as mock_load_path:
            mock_load_path.return_value = calibration_target
            with redirect_stderr(io.StringIO()):
                rounds, interrupt = runner.execute()

        self.assertIsNone(interrupt)
        self.assertEqual(len(rounds), 1)
        self.assertEqual(rounds[0].failureStep, "scene_mode_transition")
        self.assertEqual(rounds[0].recognitionExecutionResult, "scene_mode_probe_incomplete")
        self.assertFalse(rounds[0].sceneModeTransitionTimeout)


class ReplayStoreTests(unittest.TestCase):
    def test_legacy_handoff_without_roi_tolerance_path_uses_run_directory(self) -> None:
        store = ReplayStore(RecognitionGlobalConfig())
        path_keys = (
            "sequencePath",
            "metadataPath",
            "statusPath",
            "streamStartupStartFramePath",
            "streamStartupSettledFramePath",
            "sceneModeStabilityStartFramePath",
            "sceneModeStabilitySettledFramePath",
            "representativeFramePath",
            "sceneProbeStartFramePath",
            "sceneProbeEndFramePath",
            "debugImagePath",
            "visualReadinessStartFramePath",
            "visualReadinessReadyFramePath",
            "visualReadinessConfirmFramePath",
            "sampleStartFramePath",
            "sampleQualityAttemptStartFramePath",
            "sampleQualityDegradedFramePath",
            "sampleQualityLastQualifiedFramePath",
            "sampleQualityAcceptedMiddleFramePath",
            "sampleQualityAcceptedEndFramePath",
            "configSnapshotPath",
            "rawFramesPath",
            "rawTimestampsPath",
            "rawReadinessKeyFramesPath",
        )

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            run_dir = Path(temp_dir)
            handoff_path = run_dir / "legacy-handoff.json"
            frames = np.zeros((1, 1, 3), dtype=np.uint8)
            timestamps = np.array([0], dtype=np.int32)
            raw_frames_path = run_dir / "rawFramesPath.tmp"
            raw_timestamps_path = run_dir / "rawTimestampsPath.tmp"
            frames.tofile(raw_frames_path)
            timestamps.tofile(raw_timestamps_path)
            handoff = {
                "paths": {
                    "runDir": str(run_dir),
                    "handoffPath": str(handoff_path),
                    **{key: str(run_dir / f"{key}.tmp") for key in path_keys},
                },
                "rawSequence": {
                    "frames": {"dtype": "uint8", "shape": [1, 1, 1, 3]},
                    "timestamps": {"dtype": "int32", "shape": [1]},
                },
                "target": {
                    "deviceId": "device",
                    "channelId": "0",
                    "presetIndex": 1,
                    "presetName": "preset",
                    "targetId": "target",
                    "targetName": "target",
                    "roi": {"x": 0, "y": 0, "width": 1, "height": 1},
                },
                "effectiveRecognitionConfig": RecognitionGlobalConfig().snapshot(),
                "sequenceMetadata": {
                    "streamType": "flv",
                    "streamUrl": "fake://stream",
                    "targetFrameCount": 1,
                    "sampledFrameCount": 1,
                    "configuredSampleFps": 10.0,
                    "actualSampleFps": 10.0,
                    "configuredSampleDurationMs": 100,
                    "actualSampleDurationMs": 100,
                    "frameWidth": 1,
                    "frameHeight": 1,
                },
                "configPath": "demo.json",
                "extraMetadata": {},
                "hasReadinessKeyFrames": False,
            }
            handoff["paths"]["rawFramesPath"] = str(raw_frames_path)
            handoff["paths"]["rawTimestampsPath"] = str(raw_timestamps_path)
            handoff_path.write_text(json.dumps(handoff), encoding="utf-8")

            with patch.object(store, "_persist_sync") as persist_sync:
                store.write_from_handoff(handoff_path)

            restored_paths = persist_sync.call_args.args[0]
            self.assertEqual(
                restored_paths["roiToleranceSelectedFramePath"],
                run_dir / "roi-tolerance-selected-frame.ppm",
            )
        self.assertFalse(handoff_path.exists())

    def test_focus_anchor_artifacts_use_focus_roi_while_detection_artifacts_keep_detection_roi(self) -> None:
        store = ReplayStore(RecognitionGlobalConfig())
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 6, "height": 6},
            focusAnchorRoi={"x": 8, "y": 8, "width": 4, "height": 4},
        )
        frame = np.zeros((16, 16, 3), dtype=np.uint8)
        sequence = SampledSequence(
            streamType="flv",
            streamUrl="fake://stream",
            frames=np.stack([frame, frame], axis=0),
            frameTimestampsMs=[0, 100],
            targetFrameCount=2,
            sampledFrameCount=2,
            configuredSampleFps=10,
            actualSampleFps=10.0,
            configuredSampleDurationMs=200,
            actualSampleDurationMs=200,
            frameWidth=16,
            frameHeight=16,
        )

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            run_dir = Path(temp_dir)
            paths = {
                "runDir": run_dir,
                "sequencePath": run_dir / "sequence.npz",
                "metadataPath": run_dir / "metadata.json",
                "statusPath": run_dir / "replay-status.json",
                "streamStartupStartFramePath": run_dir / "stream-startup-start.ppm",
                "streamStartupSettledFramePath": run_dir / "stream-startup-settled.ppm",
                "sceneModeStabilityStartFramePath": run_dir / "scene-mode-stability-start.ppm",
                "sceneModeStabilitySettledFramePath": run_dir / "scene-mode-stability-settled.ppm",
                "representativeFramePath": run_dir / "representative-frame.ppm",
                "roiToleranceSelectedFramePath": run_dir / "roi-tolerance-selected-frame.ppm",
                "sceneProbeStartFramePath": run_dir / "scene-probe-start.ppm",
                "sceneProbeEndFramePath": run_dir / "scene-probe-end.ppm",
                "visualReadinessStartFramePath": run_dir / "visual-readiness-start.ppm",
                "visualReadinessReadyFramePath": run_dir / "visual-readiness-ready.ppm",
                "visualReadinessConfirmFramePath": run_dir / "visual-readiness-confirm.ppm",
                "sampleStartFramePath": run_dir / "sample-start.ppm",
                "sampleQualityAttemptStartFramePath": run_dir / "sample-quality-attempt-start.ppm",
                "sampleQualityDegradedFramePath": run_dir / "sample-quality-degraded.ppm",
                "sampleQualityLastQualifiedFramePath": run_dir / "sample-quality-last-qualified.ppm",
                "sampleQualityAcceptedMiddleFramePath": run_dir / "sample-quality-accepted-middle.ppm",
                "sampleQualityAcceptedEndFramePath": run_dir / "sample-quality-accepted-end.ppm",
                "debugImagePath": run_dir / "motion-debug.pgm",
                "configSnapshotPath": run_dir / "recognition-config.snapshot.json",
                "handoffPath": run_dir / "replay-handoff.json",
                "rawFramesPath": run_dir / "replay-frames.bin",
                "rawTimestampsPath": run_dir / "replay-timestamps.bin",
                "rawReadinessKeyFramesPath": run_dir / "replay-readiness-keyframes.npz",
            }

            captured_rois: dict[str, dict[str, int]] = {}

            def _capture_roi(path: Path, _frame: np.ndarray, roi) -> None:  # noqa: ANN001
                captured_rois[path.name] = roi.model_dump()

            with patch("inspector.replay_store.write_representative_roi_ppm", side_effect=_capture_roi):
                with patch("inspector.replay_store.write_motion_debug_pgm", return_value=None):
                    with patch("inspector.replay_store.FullFrameAligner.align") as mock_align:
                        mock_align.return_value = AlignedSequence(
                            alignedFrames=sequence.frames,
                            globalShifts=[(0, 0), (0, 0)],
                            shiftMagnitudes=[0.0, 0.0],
                            appliedGlobalShifts=[(0, 0), (0, 0)],
                            appliedShiftMagnitudes=[0.0, 0.0],
                            overflowFlags=[False, False],
                            alignmentApplied=False,
                        )
                        store._write_debug_artifacts_best_effort(
                            paths=paths,
                            target=target,
                            sequence=sequence,
                            effective_config=RecognitionGlobalConfig(),
                            extra_metadata={
                                "visualReadinessStartFrameIndex": 0,
                                "visualReadinessReadyFrameIndex": 1,
                                "sampleStartFrameIndex": 0,
                                "representativeFrameIndex": 1,
                                "roiToleranceSelectedFrameIndex": 1,
                                "roiToleranceSelectedRoi": {"x": 2, "y": 2, "width": 5, "height": 5},
                            },
                            readiness_key_frames={
                                "visualReadinessStartFrame": frame.copy(),
                                "visualReadinessReadyFrame": frame.copy(),
                                "sampleQualityAttemptStartFrame": frame.copy(),
                            },
                        )

        self.assertEqual(captured_rois["visual-readiness-start.ppm"], target.focusAnchorRoi.model_dump())
        self.assertEqual(captured_rois["visual-readiness-ready.ppm"], target.focusAnchorRoi.model_dump())
        self.assertEqual(captured_rois["sample-quality-attempt-start.ppm"], target.focusAnchorRoi.model_dump())
        self.assertEqual(captured_rois["sample-start.ppm"], target.roi.model_dump())
        self.assertEqual(captured_rois["representative-frame.ppm"], target.roi.model_dump())
        self.assertEqual(captured_rois["roi-tolerance-selected-frame.ppm"], {"x": 2, "y": 2, "width": 5, "height": 5})

    def test_readiness_failure_still_writes_readiness_artifacts_without_representative_frame(self) -> None:
        store = ReplayStore(RecognitionGlobalConfig())
        target = RecognitionTarget(
            deviceId="device",
            channelId="0",
            presetIndex=1,
            presetName="preset",
            targetId="target",
            targetName="target",
            roi={"x": 0, "y": 0, "width": 12, "height": 12},
            focusAnchorRoi={"x": 4, "y": 4, "width": 6, "height": 6},
        )
        readiness_start = np.zeros((16, 16, 3), dtype=np.uint8)
        readiness_ready = readiness_start.copy()
        readiness_ready[2:10, 2:10, :] = 255
        readiness_confirm = readiness_ready.copy()
        readiness_confirm[4:12, 4:12, :] = 64
        sequence = SampledSequence(
            streamType="flv",
            streamUrl="fake://stream",
            frames=np.stack([readiness_start, readiness_ready, readiness_confirm], axis=0),
            frameTimestampsMs=[0, 100, 220],
            targetFrameCount=3,
            sampledFrameCount=3,
            configuredSampleFps=10,
            actualSampleFps=10.0,
            configuredSampleDurationMs=300,
            actualSampleDurationMs=300,
            frameWidth=16,
            frameHeight=16,
        )

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temp_dir:
            run_dir = Path(temp_dir)
            paths = {
                "runDir": run_dir,
                "sequencePath": run_dir / "sequence.npz",
                "metadataPath": run_dir / "metadata.json",
                "statusPath": run_dir / "replay-status.json",
                "streamStartupStartFramePath": run_dir / "stream-startup-start.ppm",
                "streamStartupSettledFramePath": run_dir / "stream-startup-settled.ppm",
                "sceneModeStabilityStartFramePath": run_dir / "scene-mode-stability-start.ppm",
                "sceneModeStabilitySettledFramePath": run_dir / "scene-mode-stability-settled.ppm",
                "representativeFramePath": run_dir / "representative-frame.ppm",
                "sceneProbeStartFramePath": run_dir / "scene-probe-start.ppm",
                "sceneProbeEndFramePath": run_dir / "scene-probe-end.ppm",
                "visualReadinessStartFramePath": run_dir / "visual-readiness-start.ppm",
                "visualReadinessReadyFramePath": run_dir / "visual-readiness-ready.ppm",
                "visualReadinessConfirmFramePath": run_dir / "visual-readiness-confirm.ppm",
                "sampleStartFramePath": run_dir / "sample-start.ppm",
                "sampleQualityAttemptStartFramePath": run_dir / "sample-quality-attempt-start.ppm",
                "sampleQualityDegradedFramePath": run_dir / "sample-quality-degraded.ppm",
                "sampleQualityLastQualifiedFramePath": run_dir / "sample-quality-last-qualified.ppm",
                "sampleQualityAcceptedMiddleFramePath": run_dir / "sample-quality-accepted-middle.ppm",
                "sampleQualityAcceptedEndFramePath": run_dir / "sample-quality-accepted-end.ppm",
                "debugImagePath": run_dir / "motion-debug.pgm",
                "configSnapshotPath": run_dir / "recognition-config.snapshot.json",
                "handoffPath": run_dir / "replay-handoff.json",
                "rawFramesPath": run_dir / "replay-frames.bin",
                "rawTimestampsPath": run_dir / "replay-timestamps.bin",
                "rawReadinessKeyFramesPath": run_dir / "replay-readiness-keyframes.npz",
            }

            store._write_debug_artifacts_best_effort(
                paths=paths,
                target=target,
                sequence=sequence,
                effective_config=RecognitionGlobalConfig(),
                extra_metadata={
                    "visualReadinessStartFrameIndex": 0,
                    "visualReadinessReadyFrameIndex": 1,
                    "visualReadinessConfirmFrameIndex": 2,
                    "sampleStartFrameIndex": None,
                },
                readiness_key_frames={
                    "streamStartupStartFrame": np.full((16, 16, 3), 8, dtype=np.uint8),
                    "streamStartupSettledFrame": np.full((16, 16, 3), 16, dtype=np.uint8),
                    "sceneModeStabilityStartFrame": np.full((16, 16, 3), 24, dtype=np.uint8),
                    "sceneModeStabilitySettledFrame": np.full((16, 16, 3), 28, dtype=np.uint8),
                    "sceneProbeStartFrame": np.full((16, 16, 3), 32, dtype=np.uint8),
                    "sceneProbeEndFrame": np.full((16, 16, 3), 96, dtype=np.uint8),
                    "visualReadinessStartFrame": readiness_start,
                    "visualReadinessReadyFrame": readiness_ready,
                    "visualReadinessConfirmFrame": readiness_confirm,
                },
            )

            self.assertTrue(paths["streamStartupStartFramePath"].exists())
            self.assertTrue(paths["streamStartupSettledFramePath"].exists())
            self.assertTrue(paths["sceneModeStabilityStartFramePath"].exists())
            self.assertTrue(paths["sceneModeStabilitySettledFramePath"].exists())
            self.assertTrue(paths["sceneProbeStartFramePath"].exists())
            self.assertTrue(paths["sceneProbeEndFramePath"].exists())
            self.assertTrue(paths["visualReadinessStartFramePath"].exists())
            self.assertTrue(paths["visualReadinessReadyFramePath"].exists())
            self.assertTrue(paths["visualReadinessConfirmFramePath"].exists())
            self.assertTrue(paths["debugImagePath"].exists())
            self.assertFalse(paths["representativeFramePath"].exists())
            self.assertFalse(paths["sampleStartFramePath"].exists())


if __name__ == "__main__":
    unittest.main()
