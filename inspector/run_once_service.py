from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from time import monotonic, perf_counter, sleep

import numpy as np

from app.config import settings
from app.schemas.calibration import RoiModel
from app.services.calibration_storage_service import storage_service
from app.utils.logging_utils import logger

from inspector.config import (
    RecognitionGlobalConfig,
    build_recognition_config,
    load_recognition_config,
    load_recognition_raw_config,
)
from inspector.flv_sampler import FlvSamplerError, FlvSequenceSampler
from inspector.frame_alignment import FullFrameAligner
from inspector.frame_features import FrameFeatureExtractor, mean_roi_motion
from inspector.frame_scoring import WeightedFrameScorer
from inspector.models import (
    AlignedSequence,
    EffectiveSceneProfile,
    ExecutionResult,
    FrameFeature,
    FrameScore,
    RecognitionEvidencePaths,
    ReplaySaveState,
    RecognitionRunResult,
    RecognitionScoreSummary,
    RecognitionTarget,
    RecognitionTiming,
    ResolvedSceneMode,
    SampleQualityMetrics,
    SampledSequence,
    SceneModeStabilityMetrics,
    StreamStartupFreshnessMetrics,
    VisualReadinessMetrics,
)
from inspector.roi_tolerance import (
    RoiToleranceCandidate,
    RoiToleranceSequenceMetrics,
    generate_night_roi_candidates,
    prefilter_night_roi_candidates,
    select_sequence_candidate,
)
from inspector.replay_store import ReplayStore
from inspector.scene_mode_resolver import SceneModeDecision, SceneModeResolver
from inspector.scene_mode_stability import SceneModeStabilityGuard, SceneModeStabilityResult
from inspector.temporal_voting import TemporalVoteDecision, TemporalVoteResolver
from inspector.visual_readiness import FrameQualityEvaluation, VisualReadinessChecker, VisualReadinessOutcome


@dataclass(slots=True)
class _DetectionPassResult:
    effectiveConfig: RecognitionGlobalConfig
    scoreSummary: RecognitionScoreSummary
    voteDecision: TemporalVoteDecision
    representativeIndex: int | None
    detectionRoi: RoiModel
    roiTolerance: "_RoiToleranceSelection | None" = None


@dataclass(slots=True)
class _RoiToleranceSelection:
    enabled: bool
    candidateCount: int
    evaluatedCandidateCount: int
    selectedCandidate: RoiToleranceCandidate
    baseFramePassCount: int
    selectedFramePassCount: int
    rescued: bool
    candidates: list[RoiToleranceCandidate]
    candidateMetrics: dict[str, RoiToleranceSequenceMetrics]


@dataclass(slots=True)
class _VisualReadinessContext:
    session: object
    effectiveConfig: RecognitionGlobalConfig | None
    sceneModeDecision: SceneModeDecision | None
    effectiveSceneMode: ResolvedSceneMode | None
    effectiveSceneProfile: EffectiveSceneProfile | None
    twilightProfileApplied: bool | None = None
    twilightProfileReason: str | None = None
    twilightBrightnessMean: float | None = None
    sceneModeStabilityResult: SceneModeStabilityResult | None = None


@dataclass(slots=True)
class _StreamStartupFreshnessResult:
    enabled: bool
    consumedFrames: int
    jumpDetected: bool
    stableAfterJump: bool
    startFrame: np.ndarray | None
    settledFrame: np.ndarray | None
    elapsedMs: int
    exitReason: str
    streamReadFailureReason: str | None = None
    streamReadFailureCount: int = 0
    streamReadCallElapsedMs: int = 0


@dataclass(slots=True)
class _SampleQualityGuardResult:
    passed: bool
    sequence: SampledSequence | None
    metrics: SampleQualityMetrics
    streamType: str | None = None
    streamUrl: str | None = None
    activeSession: object | None = None
    attemptStartFrame: np.ndarray | None = None
    degradedFrame: np.ndarray | None = None
    lastQualifiedFrame: np.ndarray | None = None
    acceptedMiddleFrame: np.ndarray | None = None
    acceptedEndFrame: np.ndarray | None = None
    observedFrames: list[np.ndarray] | None = None
    observedTimestampsMs: list[int] | None = None


@dataclass(slots=True)
class _SampleQualityWindowRejectDetails:
    candidateWindowMs: int
    maxAllowedWindowMs: int
    candidateFrameCount: int
    triggerSharpness: float
    triggerClearCellRatio: float
    triggerStability: float


@dataclass(slots=True)
class _SampleQualityRejectDiagnostics:
    rejectSharpnessCount: int = 0
    rejectClearCellRatioCount: int = 0
    rejectStabilityCount: int = 0
    firstRejectedFrameIndex: int | None = None
    firstRejectedElapsedMs: int | None = None
    firstRejectedSharpness: float | None = None
    firstRejectedClearCellRatio: float | None = None
    firstRejectedStability: float | None = None
    lastRejectedFrameIndex: int | None = None
    lastRejectedElapsedMs: int | None = None
    lastRejectedSharpness: float | None = None
    lastRejectedClearCellRatio: float | None = None
    lastRejectedStability: float | None = None


@dataclass(slots=True)
class _SceneProfileSelection:
    effectiveConfig: RecognitionGlobalConfig
    effectiveSceneProfile: EffectiveSceneProfile
    twilightProfileApplied: bool
    twilightProfileReason: str
    twilightBrightnessMean: float | None


class RunOnceService:
    def __init__(
        self,
        global_config: RecognitionGlobalConfig | None = None,
        raw_config: dict[str, object] | None = None,
    ) -> None:
        if global_config is None:
            self.raw_config = load_recognition_raw_config()
            self.global_config = load_recognition_config()
        else:
            self.global_config = global_config
            if raw_config is not None:
                self.raw_config = deepcopy(raw_config)
            else:
                # Keep day/night profile blocks available for auto fallback when callers
                # inject an already-resolved effective config from the local runtime config.
                self.raw_config = load_recognition_raw_config()
                if not self._has_scene_profiles(self.raw_config):
                    self.raw_config = self._synthesize_raw_config(global_config)
        self.sampler = FlvSequenceSampler(self.global_config)
        self.replay_store = ReplayStore(self.global_config)
        self.scene_mode_resolver = SceneModeResolver(self.global_config)
        self.scene_mode_stability_guard = SceneModeStabilityGuard(self.global_config, self.scene_mode_resolver)
        self.visual_readiness_checker = VisualReadinessChecker(self.global_config)

    def run(self, *, config_path: Path, requested_preset_index: int | None) -> RecognitionRunResult:
        started_at = perf_counter()
        timing = RecognitionTiming()
        calibration_load_started = perf_counter()

        try:
            record = storage_service.load_path(config_path)
        except Exception as error:
            return self._failure_result(
                config_path=config_path,
                requested_preset_index=requested_preset_index,
                execution_result="detect_error",
                message=f"Failed to load calibration config: {error}",
                timing=timing,
                started_at=started_at,
            )

        timing.configLoadMs = self._elapsed_ms(calibration_load_started)
        target = RecognitionTarget.from_calibration(record)
        if requested_preset_index is not None and requested_preset_index != record.presetIndex:
            return self._failure_result(
                config_path=config_path,
                requested_preset_index=requested_preset_index,
                execution_result="detect_error",
                message=(
                    f"Requested preset {requested_preset_index} does not match calibration presetIndex "
                    f"{record.presetIndex} in {config_path.name}"
                ),
                timing=timing,
                started_at=started_at,
                target=target,
                snapshot_path=record.snapshotPath,
                snapshot_url=record.snapshotUrl,
            )

        if not settings.is_dahua_configured:
            return self._failure_result(
                config_path=config_path,
                requested_preset_index=requested_preset_index,
                execution_result="detect_error",
                message="Dahua credentials are not configured. Cannot turn preset for run_once.",
                timing=timing,
                started_at=started_at,
                target=target,
                snapshot_path=record.snapshotPath,
                snapshot_url=record.snapshotUrl,
            )

        try:
            from app.services.dahua_preset_service import preset_service
            from app.utils.request_sign_adapter import DahuaApiError
        except Exception as error:
            return self._failure_result(
                config_path=config_path,
                requested_preset_index=requested_preset_index,
                execution_result="detect_error",
                message=f"Recognition runtime dependencies are unavailable: {error}",
                timing=timing,
                started_at=started_at,
                target=target,
                snapshot_path=record.snapshotPath,
                snapshot_url=record.snapshotUrl,
            )

        preset_turn_started = perf_counter()
        try:
            preset_service.turn_preset(record.deviceId, record.channelId, record.presetIndex)
        except DahuaApiError as error:
            return self._failure_result(
                config_path=config_path,
                requested_preset_index=requested_preset_index,
                execution_result="preset_failed",
                message=f"Preset turn failed: {error}",
                timing=timing,
                started_at=started_at,
                target=target,
                snapshot_path=record.snapshotPath,
                snapshot_url=record.snapshotUrl,
            )
        except Exception as error:
            return self._failure_result(
                config_path=config_path,
                requested_preset_index=requested_preset_index,
                execution_result="detect_error",
                message=f"Unexpected preset turn error: {error}",
                timing=timing,
                started_at=started_at,
                target=target,
                snapshot_path=record.snapshotPath,
                snapshot_url=record.snapshotUrl,
            )

        timing.presetTurnMs = self._elapsed_ms(preset_turn_started)
        settle_wait_started = perf_counter()
        settle_wait_ms = self.global_config.presetTurnSettleMs + self.global_config.streamCatchupMs
        if settle_wait_ms > 0:
            sleep(settle_wait_ms / 1000)
        timing.settleWaitMs = self._elapsed_ms(settle_wait_started)

        requested_scene_mode = self.global_config.sceneMode
        visual_readiness: VisualReadinessMetrics | None = None
        readiness_outcome: VisualReadinessOutcome | None = None
        sample_quality: SampleQualityMetrics | None = None
        sample_quality_guard_result: _SampleQualityGuardResult | None = None
        stream_startup_freshness_result: _StreamStartupFreshnessResult | None = None
        stream_startup_freshness: StreamStartupFreshnessMetrics | None = None
        readiness_scene_decision: SceneModeDecision | None = None
        readiness_effective_scene_mode: ResolvedSceneMode | None = None
        readiness_effective_scene_profile: EffectiveSceneProfile | None = None
        readiness_twilight_profile_applied: bool | None = None
        readiness_twilight_profile_reason: str | None = None
        readiness_twilight_brightness_mean: float | None = None
        scene_mode_stability_result: SceneModeStabilityResult | None = None
        scene_mode_stability: SceneModeStabilityMetrics | None = None
        focus_anchor_roi, focus_anchor_roi_source, focus_anchor_roi_fallback_used = self._focus_anchor_roi(target)
        replay_paths: dict[str, str] = {}
        replay_save = ReplaySaveState(status="disabled", message="Replay save not started")
        pre_readiness_session_reopened = False
        pre_readiness_stream_recovered = False
        pre_readiness_stream_retry_count = 0
        stream_read_failure_reason: str | None = None
        stream_read_failure_count = 0
        stream_read_call_elapsed_ms = 0

        try:
            session = self.sampler.open_session(device_id=record.deviceId, channel_id=record.channelId)
        except FlvSamplerError as error:
            return self._failure_result(
                config_path=config_path,
                requested_preset_index=requested_preset_index,
                execution_result=error.reason,
                message=str(error),
                timing=timing,
                started_at=started_at,
                target=target,
                snapshot_path=record.snapshotPath,
                snapshot_url=record.snapshotUrl,
            )
        except Exception as error:
            return self._failure_result(
                config_path=config_path,
                requested_preset_index=requested_preset_index,
                execution_result="stream_failed",
                message=f"Unexpected FLV open error: {error}",
                timing=timing,
                started_at=started_at,
                target=target,
                snapshot_path=record.snapshotPath,
                snapshot_url=record.snapshotUrl,
            )

        try:
            stream_startup_freshness_result = self._guard_stream_startup_freshness(session)
            stream_startup_freshness = self._stream_startup_freshness_metrics(stream_startup_freshness_result)
            stream_read_failure_reason = self._stream_read_failure_reason(session)
            stream_read_failure_count = self._stream_read_failure_count(session)
            stream_read_call_elapsed_ms = self._stream_read_call_elapsed_ms(session)
            if stream_read_failure_reason is not None:
                pre_readiness_session_reopened = True
                pre_readiness_stream_retry_count = 1
                reopened_session, reopen_message = self._reopen_pre_readiness_session(session, target)
                if reopened_session is None:
                    return self._failure_result(
                        config_path=config_path,
                        requested_preset_index=requested_preset_index,
                        execution_result=self._stream_failure_execution_result(stream_read_failure_reason),
                        message=reopen_message or "FLV stream failed before visual readiness.",
                        timing=timing,
                        started_at=started_at,
                        target=target,
                        snapshot_path=record.snapshotPath,
                        snapshot_url=record.snapshotUrl,
                        stream_startup_freshness=stream_startup_freshness,
                        pre_readiness_session_reopened=pre_readiness_session_reopened,
                        pre_readiness_stream_recovered=False,
                        pre_readiness_stream_retry_count=pre_readiness_stream_retry_count,
                        stream_read_failure_reason=stream_read_failure_reason,
                        stream_read_failure_count=stream_read_failure_count,
                        stream_read_call_elapsed_ms=stream_read_call_elapsed_ms,
                    )
                session = reopened_session
                stream_startup_freshness_result = self._guard_stream_startup_freshness(session)
                stream_startup_freshness = self._stream_startup_freshness_metrics(stream_startup_freshness_result)
                reopened_stream_failure_reason = self._stream_read_failure_reason(session)
                reopened_stream_failure_count = self._stream_read_failure_count(session)
                reopened_stream_read_call_elapsed_ms = self._stream_read_call_elapsed_ms(session)
                if reopened_stream_failure_reason is not None:
                    return self._failure_result(
                        config_path=config_path,
                        requested_preset_index=requested_preset_index,
                        execution_result=self._stream_failure_execution_result(reopened_stream_failure_reason),
                        message="FLV stream remained unavailable after the one pre-readiness reopen.",
                        timing=timing,
                        started_at=started_at,
                        target=target,
                        snapshot_path=record.snapshotPath,
                        snapshot_url=record.snapshotUrl,
                        stream_startup_freshness=stream_startup_freshness,
                        pre_readiness_session_reopened=pre_readiness_session_reopened,
                        pre_readiness_stream_recovered=False,
                        pre_readiness_stream_retry_count=pre_readiness_stream_retry_count,
                        stream_read_failure_reason=reopened_stream_failure_reason,
                        stream_read_failure_count=reopened_stream_failure_count,
                        stream_read_call_elapsed_ms=reopened_stream_read_call_elapsed_ms,
                    )
                pre_readiness_stream_recovered = True
            if self.global_config.visualReadinessEnabled:
                visual_readiness_started = perf_counter()
                readiness_context = self._resolve_visual_readiness_context(session)
                scene_mode_stability_result = readiness_context.sceneModeStabilityResult
                scene_mode_stability = self._scene_mode_stability_metrics(scene_mode_stability_result)
                readiness_scene_decision = readiness_context.sceneModeDecision
                readiness_effective_scene_mode = readiness_context.effectiveSceneMode
                readiness_effective_scene_profile = readiness_context.effectiveSceneProfile
                readiness_twilight_profile_applied = readiness_context.twilightProfileApplied
                readiness_twilight_profile_reason = readiness_context.twilightProfileReason
                readiness_twilight_brightness_mean = readiness_context.twilightBrightnessMean
                stream_read_failure_reason = self._stream_read_failure_reason(session)
                stream_read_failure_count = self._stream_read_failure_count(session)
                stream_read_call_elapsed_ms = self._stream_read_call_elapsed_ms(session)
                if stream_read_failure_reason is not None:
                    if pre_readiness_stream_retry_count >= 1:
                        return self._failure_result(
                            config_path=config_path,
                            requested_preset_index=requested_preset_index,
                            execution_result=self._stream_failure_execution_result(stream_read_failure_reason),
                            message="FLV stream failed during scene-mode preparation after the one pre-readiness reopen.",
                            timing=timing,
                            started_at=started_at,
                            target=target,
                            snapshot_path=record.snapshotPath,
                            snapshot_url=record.snapshotUrl,
                            stream_startup_freshness=stream_startup_freshness,
                            scene_mode_stability=scene_mode_stability,
                            pre_readiness_session_reopened=pre_readiness_session_reopened,
                            pre_readiness_stream_recovered=False,
                            pre_readiness_stream_retry_count=pre_readiness_stream_retry_count,
                            stream_read_failure_reason=stream_read_failure_reason,
                            stream_read_failure_count=stream_read_failure_count,
                            stream_read_call_elapsed_ms=stream_read_call_elapsed_ms,
                        )
                    pre_readiness_session_reopened = True
                    pre_readiness_stream_retry_count = 1
                    reopened_session, reopen_message = self._reopen_pre_readiness_session(session, target)
                    if reopened_session is None:
                        return self._failure_result(
                            config_path=config_path,
                            requested_preset_index=requested_preset_index,
                            execution_result=self._stream_failure_execution_result(stream_read_failure_reason),
                            message=reopen_message or "FLV stream failed during scene-mode preparation.",
                            timing=timing,
                            started_at=started_at,
                            target=target,
                            snapshot_path=record.snapshotPath,
                            snapshot_url=record.snapshotUrl,
                            stream_startup_freshness=stream_startup_freshness,
                            scene_mode_stability=scene_mode_stability,
                            pre_readiness_session_reopened=pre_readiness_session_reopened,
                            pre_readiness_stream_recovered=False,
                            pre_readiness_stream_retry_count=pre_readiness_stream_retry_count,
                            stream_read_failure_reason=stream_read_failure_reason,
                            stream_read_failure_count=stream_read_failure_count,
                            stream_read_call_elapsed_ms=stream_read_call_elapsed_ms,
                        )
                    session = reopened_session
                    stream_startup_freshness_result = self._guard_stream_startup_freshness(session)
                    stream_startup_freshness = self._stream_startup_freshness_metrics(stream_startup_freshness_result)
                    readiness_context = self._resolve_visual_readiness_context(session)
                    scene_mode_stability_result = readiness_context.sceneModeStabilityResult
                    scene_mode_stability = self._scene_mode_stability_metrics(scene_mode_stability_result)
                    readiness_scene_decision = readiness_context.sceneModeDecision
                    readiness_effective_scene_mode = readiness_context.effectiveSceneMode
                    readiness_effective_scene_profile = readiness_context.effectiveSceneProfile
                    readiness_twilight_profile_applied = readiness_context.twilightProfileApplied
                    readiness_twilight_profile_reason = readiness_context.twilightProfileReason
                    readiness_twilight_brightness_mean = readiness_context.twilightBrightnessMean
                    reopened_stream_failure_reason = self._stream_read_failure_reason(session)
                    reopened_stream_failure_count = self._stream_read_failure_count(session)
                    reopened_stream_read_call_elapsed_ms = self._stream_read_call_elapsed_ms(session)
                    if reopened_stream_failure_reason is not None:
                        return self._failure_result(
                            config_path=config_path,
                            requested_preset_index=requested_preset_index,
                            execution_result=self._stream_failure_execution_result(reopened_stream_failure_reason),
                            message="FLV stream remained unavailable after the one pre-readiness reopen.",
                            timing=timing,
                            started_at=started_at,
                            target=target,
                            snapshot_path=record.snapshotPath,
                            snapshot_url=record.snapshotUrl,
                            stream_startup_freshness=stream_startup_freshness,
                            scene_mode_stability=scene_mode_stability,
                            pre_readiness_session_reopened=pre_readiness_session_reopened,
                            pre_readiness_stream_recovered=False,
                            pre_readiness_stream_retry_count=pre_readiness_stream_retry_count,
                            stream_read_failure_reason=reopened_stream_failure_reason,
                            stream_read_failure_count=reopened_stream_failure_count,
                            stream_read_call_elapsed_ms=reopened_stream_read_call_elapsed_ms,
                        )
                    pre_readiness_stream_recovered = True
                if requested_scene_mode == "auto" and (
                    readiness_context.effectiveConfig is None
                    or (
                        scene_mode_stability_result is not None
                        and scene_mode_stability_result.enabled
                        and not scene_mode_stability_result.stable
                    )
                ):
                    timing.visualReadinessMs = self._elapsed_ms(visual_readiness_started)
                    replay_paths, replay_save = self._persist_scene_mode_transition_replay(
                        target=target,
                        stability_result=scene_mode_stability_result,
                        config_path=str(config_path),
                        effective_config=self.global_config,
                        requested_scene_mode=requested_scene_mode,
                        focus_anchor_roi_fallback_used=focus_anchor_roi_fallback_used,
                        focus_anchor_roi_source=focus_anchor_roi_source,
                        stream_startup_freshness=stream_startup_freshness,
                        stream_startup_freshness_result=stream_startup_freshness_result,
                    )
                    return self._failure_result(
                        config_path=config_path,
                        requested_preset_index=requested_preset_index,
                        execution_result=self._scene_mode_execution_result(
                            scene_mode_stability_result.reason if scene_mode_stability_result is not None else None
                        ),
                        message="Scene mode did not settle before visual readiness.",
                        timing=timing,
                        started_at=started_at,
                        target=target,
                        snapshot_path=record.snapshotPath,
                        snapshot_url=record.snapshotUrl,
                        visual_state="undetermined",
                        stream_startup_freshness=stream_startup_freshness,
                        scene_mode_stability=scene_mode_stability,
                        replay_paths=replay_paths,
                        replay_save=replay_save,
                        requested_scene_mode=requested_scene_mode,
                        effective_scene_mode=readiness_effective_scene_mode,
                        scene_mode_confidence=(
                            readiness_scene_decision.confidence if readiness_scene_decision is not None else None
                        ),
                        scene_mode_reason=(
                            scene_mode_stability_result.reason
                            if scene_mode_stability_result is not None
                            else "scene_mode_transition_timeout"
                        ),
                        scene_mode_diagnostics=(
                            readiness_scene_decision.diagnostics if readiness_scene_decision is not None else None
                        ),
                        focus_anchor_roi_fallback_used=focus_anchor_roi_fallback_used,
                        focus_anchor_roi_source=focus_anchor_roi_source,
                        effective_scene_profile=readiness_effective_scene_profile,
                        twilight_profile_applied=readiness_twilight_profile_applied,
                        twilight_profile_reason=readiness_twilight_profile_reason,
                        twilight_brightness_mean=readiness_twilight_brightness_mean,
                        effective_config=self.global_config,
                    )
                readiness_checker = VisualReadinessChecker(readiness_context.effectiveConfig)
                readiness_outcome = readiness_checker.wait_until_ready(readiness_context.session, roi=focus_anchor_roi)
                visual_readiness = readiness_outcome.metrics
                stream_read_failure_reason = self._stream_read_failure_reason(session)
                stream_read_failure_count = self._stream_read_failure_count(session)
                stream_read_call_elapsed_ms = self._stream_read_call_elapsed_ms(session)
                if stream_read_failure_reason is not None and not visual_readiness.ready:
                    if pre_readiness_stream_retry_count >= 1:
                        return self._failure_result(
                            config_path=config_path,
                            requested_preset_index=requested_preset_index,
                            execution_result=self._stream_failure_execution_result(stream_read_failure_reason),
                            message="FLV stream failed during visual readiness after the one pre-readiness reopen.",
                            timing=timing,
                            started_at=started_at,
                            target=target,
                            snapshot_path=record.snapshotPath,
                            snapshot_url=record.snapshotUrl,
                            stream_startup_freshness=stream_startup_freshness,
                            scene_mode_stability=scene_mode_stability,
                            visual_readiness=visual_readiness,
                            pre_readiness_session_reopened=pre_readiness_session_reopened,
                            pre_readiness_stream_recovered=False,
                            pre_readiness_stream_retry_count=pre_readiness_stream_retry_count,
                            stream_read_failure_reason=stream_read_failure_reason,
                            stream_read_failure_count=stream_read_failure_count,
                            stream_read_call_elapsed_ms=stream_read_call_elapsed_ms,
                        )
                    pre_readiness_session_reopened = True
                    pre_readiness_stream_retry_count = 1
                    reopened_session, reopen_message = self._reopen_pre_readiness_session(session, target)
                    if reopened_session is None:
                        return self._failure_result(
                            config_path=config_path,
                            requested_preset_index=requested_preset_index,
                            execution_result=self._stream_failure_execution_result(stream_read_failure_reason),
                            message=reopen_message or "FLV stream failed during visual readiness.",
                            timing=timing,
                            started_at=started_at,
                            target=target,
                            snapshot_path=record.snapshotPath,
                            snapshot_url=record.snapshotUrl,
                            stream_startup_freshness=stream_startup_freshness,
                            scene_mode_stability=scene_mode_stability,
                            visual_readiness=visual_readiness,
                            pre_readiness_session_reopened=pre_readiness_session_reopened,
                            pre_readiness_stream_recovered=False,
                            pre_readiness_stream_retry_count=pre_readiness_stream_retry_count,
                            stream_read_failure_reason=stream_read_failure_reason,
                            stream_read_failure_count=stream_read_failure_count,
                            stream_read_call_elapsed_ms=stream_read_call_elapsed_ms,
                        )
                    session = reopened_session
                    stream_startup_freshness_result = self._guard_stream_startup_freshness(session)
                    stream_startup_freshness = self._stream_startup_freshness_metrics(stream_startup_freshness_result)
                    readiness_context = self._resolve_visual_readiness_context(session)
                    scene_mode_stability_result = readiness_context.sceneModeStabilityResult
                    scene_mode_stability = self._scene_mode_stability_metrics(scene_mode_stability_result)
                    readiness_scene_decision = readiness_context.sceneModeDecision
                    readiness_effective_scene_mode = readiness_context.effectiveSceneMode
                    readiness_effective_scene_profile = readiness_context.effectiveSceneProfile
                    readiness_twilight_profile_applied = readiness_context.twilightProfileApplied
                    readiness_twilight_profile_reason = readiness_context.twilightProfileReason
                    readiness_twilight_brightness_mean = readiness_context.twilightBrightnessMean
                    reopened_stream_failure_reason = self._stream_read_failure_reason(session)
                    reopened_stream_failure_count = self._stream_read_failure_count(session)
                    reopened_stream_read_call_elapsed_ms = self._stream_read_call_elapsed_ms(session)
                    if reopened_stream_failure_reason is not None:
                        return self._failure_result(
                            config_path=config_path,
                            requested_preset_index=requested_preset_index,
                            execution_result=self._stream_failure_execution_result(reopened_stream_failure_reason),
                            message="FLV stream remained unavailable after the one pre-readiness reopen.",
                            timing=timing,
                            started_at=started_at,
                            target=target,
                            snapshot_path=record.snapshotPath,
                            snapshot_url=record.snapshotUrl,
                            stream_startup_freshness=stream_startup_freshness,
                            scene_mode_stability=scene_mode_stability,
                            pre_readiness_session_reopened=pre_readiness_session_reopened,
                            pre_readiness_stream_recovered=False,
                            pre_readiness_stream_retry_count=pre_readiness_stream_retry_count,
                            stream_read_failure_reason=reopened_stream_failure_reason,
                            stream_read_failure_count=reopened_stream_failure_count,
                            stream_read_call_elapsed_ms=reopened_stream_read_call_elapsed_ms,
                        )
                    if readiness_context.effectiveConfig is None:
                        return self._failure_result(
                            config_path=config_path,
                            requested_preset_index=requested_preset_index,
                            execution_result=self._scene_mode_execution_result(
                                scene_mode_stability_result.reason if scene_mode_stability_result is not None else None
                            ),
                            message="Scene mode did not settle after the pre-readiness stream reopen.",
                            timing=timing,
                            started_at=started_at,
                            target=target,
                            snapshot_path=record.snapshotPath,
                            snapshot_url=record.snapshotUrl,
                            stream_startup_freshness=stream_startup_freshness,
                            scene_mode_stability=scene_mode_stability,
                            pre_readiness_session_reopened=pre_readiness_session_reopened,
                            pre_readiness_stream_recovered=True,
                            pre_readiness_stream_retry_count=pre_readiness_stream_retry_count,
                        )
                    readiness_checker = VisualReadinessChecker(readiness_context.effectiveConfig)
                    readiness_outcome = readiness_checker.wait_until_ready(session, roi=focus_anchor_roi)
                    visual_readiness = readiness_outcome.metrics
                    reopened_stream_failure_reason = self._stream_read_failure_reason(session)
                    reopened_stream_failure_count = self._stream_read_failure_count(session)
                    reopened_stream_read_call_elapsed_ms = self._stream_read_call_elapsed_ms(session)
                    if reopened_stream_failure_reason is not None and not visual_readiness.ready:
                        return self._failure_result(
                            config_path=config_path,
                            requested_preset_index=requested_preset_index,
                            execution_result=self._stream_failure_execution_result(reopened_stream_failure_reason),
                            message="FLV stream failed again during visual readiness after the one pre-readiness reopen.",
                            timing=timing,
                            started_at=started_at,
                            target=target,
                            snapshot_path=record.snapshotPath,
                            snapshot_url=record.snapshotUrl,
                            stream_startup_freshness=stream_startup_freshness,
                            scene_mode_stability=scene_mode_stability,
                            visual_readiness=visual_readiness,
                            pre_readiness_session_reopened=pre_readiness_session_reopened,
                            pre_readiness_stream_recovered=False,
                            pre_readiness_stream_retry_count=pre_readiness_stream_retry_count,
                            stream_read_failure_reason=reopened_stream_failure_reason,
                            stream_read_failure_count=reopened_stream_failure_count,
                            stream_read_call_elapsed_ms=reopened_stream_read_call_elapsed_ms,
                        )
                    pre_readiness_stream_recovered = True
                if (
                    requested_scene_mode == "auto"
                    and visual_readiness.reason == "visual_not_ready_blurry"
                    and scene_mode_stability_result is not None
                    and scene_mode_stability_result.enabled
                    and scene_mode_stability_result.relockCount < self.global_config.sceneModeStabilityMaxRelocks
                ):
                    relocked_context = self._resolve_visual_readiness_context(
                        session,
                        relock_count=scene_mode_stability_result.relockCount + 1,
                        relock_reason=visual_readiness.reason,
                    )
                    relocked_stability_result = relocked_context.sceneModeStabilityResult
                    relocked_stability = self._scene_mode_stability_metrics(relocked_stability_result)
                    if relocked_stability_result is not None:
                        scene_mode_stability_result = relocked_stability_result
                        scene_mode_stability = relocked_stability
                    if relocked_stability_result is not None and not relocked_stability_result.stable:
                        readiness_scene_decision = relocked_context.sceneModeDecision
                        readiness_effective_scene_mode = relocked_context.effectiveSceneMode
                        timing.visualReadinessMs = self._elapsed_ms(visual_readiness_started)
                        replay_paths, replay_save = self._persist_scene_mode_transition_replay(
                            target=target,
                            stability_result=scene_mode_stability_result,
                            config_path=str(config_path),
                            effective_config=self.global_config,
                            requested_scene_mode=requested_scene_mode,
                            focus_anchor_roi_fallback_used=focus_anchor_roi_fallback_used,
                            focus_anchor_roi_source=focus_anchor_roi_source,
                            stream_startup_freshness=stream_startup_freshness,
                            stream_startup_freshness_result=stream_startup_freshness_result,
                            visual_readiness=visual_readiness,
                            readiness_outcome=readiness_outcome,
                        )
                        return self._failure_result(
                            config_path=config_path,
                            requested_preset_index=requested_preset_index,
                            execution_result=self._scene_mode_execution_result(
                                scene_mode_stability_result.reason if scene_mode_stability_result is not None else None
                            ),
                            message="Scene mode started transitioning during visual readiness relock.",
                            timing=timing,
                            started_at=started_at,
                            target=target,
                            snapshot_path=record.snapshotPath,
                            snapshot_url=record.snapshotUrl,
                            visual_state="undetermined",
                            stream_startup_freshness=stream_startup_freshness,
                            scene_mode_stability=scene_mode_stability,
                            visual_readiness=visual_readiness,
                            replay_paths=replay_paths,
                            replay_save=replay_save,
                            requested_scene_mode=requested_scene_mode,
                            effective_scene_mode=readiness_effective_scene_mode,
                            scene_mode_confidence=(
                                readiness_scene_decision.confidence if readiness_scene_decision is not None else None
                            ),
                            scene_mode_reason=scene_mode_stability_result.reason,
                            scene_mode_diagnostics=(
                                readiness_scene_decision.diagnostics if readiness_scene_decision is not None else None
                            ),
                            focus_anchor_roi_fallback_used=focus_anchor_roi_fallback_used,
                            focus_anchor_roi_source=focus_anchor_roi_source,
                            effective_scene_profile=readiness_effective_scene_profile,
                            twilight_profile_applied=readiness_twilight_profile_applied,
                            twilight_profile_reason=readiness_twilight_profile_reason,
                            twilight_brightness_mean=readiness_twilight_brightness_mean,
                            effective_config=self.global_config,
                        )
                    if (
                        relocked_context.effectiveConfig is not None
                        and relocked_context.effectiveSceneMode is not None
                        and (
                            relocked_context.effectiveSceneMode != readiness_effective_scene_mode
                            or relocked_context.effectiveSceneProfile != readiness_effective_scene_profile
                        )
                    ):
                        readiness_context = relocked_context
                        readiness_scene_decision = relocked_context.sceneModeDecision
                        readiness_effective_scene_mode = relocked_context.effectiveSceneMode
                        readiness_effective_scene_profile = relocked_context.effectiveSceneProfile
                        readiness_twilight_profile_applied = relocked_context.twilightProfileApplied
                        readiness_twilight_profile_reason = relocked_context.twilightProfileReason
                        readiness_twilight_brightness_mean = relocked_context.twilightBrightnessMean
                        readiness_checker = VisualReadinessChecker(relocked_context.effectiveConfig)
                        readiness_outcome = readiness_checker.wait_until_ready(
                            relocked_context.session,
                            roi=focus_anchor_roi,
                        )
                        visual_readiness = readiness_outcome.metrics
                timing.visualReadinessMs = self._elapsed_ms(visual_readiness_started)
                if not readiness_outcome.metrics.ready:
                    readiness_execution_result = self._visual_not_ready_execution_result(readiness_outcome.metrics.reason)
                    replay_paths, replay_save = self._persist_visual_readiness_replay(
                        target=target,
                        readiness_outcome=readiness_outcome,
                        config_path=str(config_path),
                        execution_result=readiness_execution_result,
                        effective_config=readiness_context.effectiveConfig,
                        requested_scene_mode=requested_scene_mode,
                        effective_scene_mode=readiness_effective_scene_mode,
                        scene_mode_decision=readiness_scene_decision,
                        effective_scene_profile=readiness_effective_scene_profile,
                        twilight_profile_applied=readiness_twilight_profile_applied,
                        twilight_profile_reason=readiness_twilight_profile_reason,
                        twilight_brightness_mean=readiness_twilight_brightness_mean,
                        focus_anchor_roi_fallback_used=focus_anchor_roi_fallback_used,
                        focus_anchor_roi_source=focus_anchor_roi_source,
                        stream_startup_freshness=stream_startup_freshness,
                        stream_startup_freshness_result=stream_startup_freshness_result,
                        scene_mode_stability=scene_mode_stability,
                        scene_mode_stability_result=scene_mode_stability_result,
                    )
                    return self._failure_result(
                        config_path=config_path,
                        requested_preset_index=requested_preset_index,
                        execution_result=readiness_execution_result,
                        message=f"Visual readiness gate did not pass: {readiness_outcome.metrics.reason}",
                        timing=timing,
                        started_at=started_at,
                        target=target,
                        snapshot_path=record.snapshotPath,
                        snapshot_url=record.snapshotUrl,
                        visual_state="undetermined",
                        stream_startup_freshness=stream_startup_freshness,
                        scene_mode_stability=scene_mode_stability,
                        visual_readiness=readiness_outcome.metrics,
                        replay_paths=replay_paths,
                        replay_save=replay_save,
                        requested_scene_mode=requested_scene_mode,
                        effective_scene_mode=readiness_effective_scene_mode,
                        scene_mode_confidence=(
                            readiness_scene_decision.confidence if readiness_scene_decision is not None else None
                        ),
                        scene_mode_reason=(
                            readiness_scene_decision.reason if readiness_scene_decision is not None else None
                        ),
                        scene_mode_diagnostics=(
                            readiness_scene_decision.diagnostics if readiness_scene_decision is not None else None
                        ),
                        focus_anchor_roi_fallback_used=focus_anchor_roi_fallback_used,
                        focus_anchor_roi_source=focus_anchor_roi_source,
                        effective_scene_profile=readiness_effective_scene_profile,
                        twilight_profile_applied=readiness_twilight_profile_applied,
                        twilight_profile_reason=readiness_twilight_profile_reason,
                        twilight_brightness_mean=readiness_twilight_brightness_mean,
                        effective_config=readiness_context.effectiveConfig,
                    )
            sample_started = perf_counter()
            try:
                effective_sampling_config = (
                    readiness_context.effectiveConfig if self.global_config.visualReadinessEnabled else self.global_config
                )
                sequence, sample_quality_guard_result = self._sample_with_quality_guard(
                    session=session,
                    effective_config=effective_sampling_config,
                    target=target,
                    readiness_outcome=readiness_outcome,
                    focus_anchor_roi=focus_anchor_roi,
                )
                if sample_quality_guard_result.activeSession is not None:
                    session = sample_quality_guard_result.activeSession
                sample_quality = sample_quality_guard_result.metrics
                if not sample_quality_guard_result.passed or sequence is None:
                    sample_quality_execution_result = self._sample_quality_execution_result(sample_quality.reason)
                    replay_paths, replay_save = self._persist_sample_quality_replay(
                        target=target,
                        guard_result=sample_quality_guard_result,
                        config_path=str(config_path),
                        execution_result=sample_quality_execution_result,
                        effective_config=effective_sampling_config,
                        requested_scene_mode=requested_scene_mode,
                        effective_scene_mode=readiness_effective_scene_mode,
                        scene_mode_decision=readiness_scene_decision,
                        effective_scene_profile=readiness_effective_scene_profile,
                        twilight_profile_applied=readiness_twilight_profile_applied,
                        twilight_profile_reason=readiness_twilight_profile_reason,
                        twilight_brightness_mean=readiness_twilight_brightness_mean,
                        focus_anchor_roi_fallback_used=focus_anchor_roi_fallback_used,
                        focus_anchor_roi_source=focus_anchor_roi_source,
                        stream_startup_freshness=stream_startup_freshness,
                        stream_startup_freshness_result=stream_startup_freshness_result,
                        scene_mode_stability=scene_mode_stability,
                        scene_mode_stability_result=scene_mode_stability_result,
                        visual_readiness=visual_readiness,
                        readiness_outcome=readiness_outcome,
                    )
                    timing.sampleMs = self._elapsed_ms(sample_started)
                    return self._failure_result(
                        config_path=config_path,
                        requested_preset_index=requested_preset_index,
                        execution_result=sample_quality_execution_result,
                        message=f"Sample quality guard did not pass: {sample_quality.reason}",
                        timing=timing,
                        started_at=started_at,
                        target=target,
                        snapshot_path=record.snapshotPath,
                        snapshot_url=record.snapshotUrl,
                        visual_state="undetermined",
                        stream_startup_freshness=stream_startup_freshness,
                        scene_mode_stability=scene_mode_stability,
                        visual_readiness=visual_readiness,
                        sample_quality=sample_quality,
                        replay_paths=replay_paths,
                        replay_save=replay_save,
                        requested_scene_mode=requested_scene_mode,
                        effective_scene_mode=readiness_effective_scene_mode,
                        scene_mode_confidence=(
                            readiness_scene_decision.confidence if readiness_scene_decision is not None else None
                        ),
                        scene_mode_reason=(
                            readiness_scene_decision.reason if readiness_scene_decision is not None else None
                        ),
                        scene_mode_diagnostics=(
                            readiness_scene_decision.diagnostics if readiness_scene_decision is not None else None
                        ),
                        focus_anchor_roi_fallback_used=focus_anchor_roi_fallback_used,
                        focus_anchor_roi_source=focus_anchor_roi_source,
                        effective_scene_profile=readiness_effective_scene_profile,
                        twilight_profile_applied=readiness_twilight_profile_applied,
                        twilight_profile_reason=readiness_twilight_profile_reason,
                        twilight_brightness_mean=readiness_twilight_brightness_mean,
                        effective_config=effective_sampling_config,
                    )
            except FlvSamplerError as error:
                timing.sampleMs = self._elapsed_ms(sample_started)
                return self._failure_result(
                    config_path=config_path,
                    requested_preset_index=requested_preset_index,
                    execution_result=error.reason,
                    message=str(error),
                    timing=timing,
                    started_at=started_at,
                    target=target,
                    snapshot_path=record.snapshotPath,
                    snapshot_url=record.snapshotUrl,
                    visual_state="undetermined" if visual_readiness is not None else None,
                    stream_startup_freshness=stream_startup_freshness,
                    scene_mode_stability=scene_mode_stability,
                    visual_readiness=visual_readiness,
                    sample_quality=sample_quality,
                    requested_scene_mode=requested_scene_mode,
                    effective_scene_mode=readiness_effective_scene_mode,
                    scene_mode_confidence=(
                        readiness_scene_decision.confidence if readiness_scene_decision is not None else None
                    ),
                    scene_mode_reason=readiness_scene_decision.reason if readiness_scene_decision is not None else None,
                    scene_mode_diagnostics=(
                        readiness_scene_decision.diagnostics if readiness_scene_decision is not None else None
                    ),
                    focus_anchor_roi_fallback_used=focus_anchor_roi_fallback_used,
                    focus_anchor_roi_source=focus_anchor_roi_source,
                    effective_scene_profile=readiness_effective_scene_profile,
                    twilight_profile_applied=readiness_twilight_profile_applied,
                    twilight_profile_reason=readiness_twilight_profile_reason,
                    twilight_brightness_mean=readiness_twilight_brightness_mean,
                    effective_config=(
                        self._config_for_scene_mode(readiness_effective_scene_mode)
                        if readiness_effective_scene_mode is not None
                        else None
                    ),
                )
            except Exception as error:
                timing.sampleMs = self._elapsed_ms(sample_started)
                return self._failure_result(
                    config_path=config_path,
                    requested_preset_index=requested_preset_index,
                    execution_result="stream_failed",
                    message=f"Unexpected FLV sampling error: {error}",
                    timing=timing,
                    started_at=started_at,
                    target=target,
                    snapshot_path=record.snapshotPath,
                    snapshot_url=record.snapshotUrl,
                    visual_state="undetermined" if visual_readiness is not None else None,
                    stream_startup_freshness=stream_startup_freshness,
                    scene_mode_stability=scene_mode_stability,
                    visual_readiness=visual_readiness,
                    sample_quality=sample_quality,
                    requested_scene_mode=requested_scene_mode,
                    effective_scene_mode=readiness_effective_scene_mode,
                    scene_mode_confidence=(
                        readiness_scene_decision.confidence if readiness_scene_decision is not None else None
                    ),
                    scene_mode_reason=readiness_scene_decision.reason if readiness_scene_decision is not None else None,
                    scene_mode_diagnostics=(
                        readiness_scene_decision.diagnostics if readiness_scene_decision is not None else None
                    ),
                    focus_anchor_roi_fallback_used=focus_anchor_roi_fallback_used,
                    focus_anchor_roi_source=focus_anchor_roi_source,
                    effective_scene_profile=readiness_effective_scene_profile,
                    twilight_profile_applied=readiness_twilight_profile_applied,
                    twilight_profile_reason=readiness_twilight_profile_reason,
                    twilight_brightness_mean=readiness_twilight_brightness_mean,
                    effective_config=(
                        self._config_for_scene_mode(readiness_effective_scene_mode)
                        if readiness_effective_scene_mode is not None
                        else None
                    ),
                )
        finally:
            session.release()
        timing.sampleMs = self._elapsed_ms(sample_started)

        detect_started = perf_counter()
        scene_mode_decision: SceneModeDecision | None = None
        effective_scene_mode: ResolvedSceneMode | None = None
        effective_scene_profile: EffectiveSceneProfile | None = None
        effective_pass: _DetectionPassResult | None = None
        fallback_used = False
        fallback_resolution = "not_needed"
        day_visual_state = None
        night_visual_state = None
        final_visual_state = None
        scene_mode_reason = "manual_scene_mode_override"
        scene_mode_confidence: float | None = 1.0 if requested_scene_mode != "auto" else None
        twilight_profile_applied: bool | None = None
        twilight_profile_reason: str | None = None
        twilight_brightness_mean: float | None = None

        if requested_scene_mode == "auto":
            scene_mode_decision = self.scene_mode_resolver.resolve(sequence)
            scene_mode_confidence = scene_mode_decision.confidence
            scene_mode_reason = scene_mode_decision.reason
            if scene_mode_decision.classification != "ambiguous":
                effective_scene_mode = scene_mode_decision.classification
                profile_selection = self._resolve_scene_profile(effective_scene_mode, scene_mode_decision)
                effective_scene_profile = profile_selection.effectiveSceneProfile
                twilight_profile_applied = profile_selection.twilightProfileApplied
                twilight_profile_reason = profile_selection.twilightProfileReason
                twilight_brightness_mean = profile_selection.twilightBrightnessMean
                effective_pass = self._run_detection_pass(
                    sequence=sequence,
                    target=target,
                    effective_config=profile_selection.effectiveConfig,
                )
                final_visual_state = effective_pass.voteDecision.visualState
            elif self.global_config.sceneAutoUseDualPathFallback:
                fallback_used = True
                fallback_resolution = "agreed"
                day_profile_selection = self._resolve_scene_profile("day_visible", scene_mode_decision)
                day_pass = self._run_detection_pass(
                    sequence=sequence,
                    target=target,
                    effective_config=day_profile_selection.effectiveConfig,
                )
                night_pass = self._run_detection_pass(
                    sequence=sequence,
                    target=target,
                    effective_config=self._config_for_scene_mode("night_ir"),
                )
                day_visual_state = day_pass.voteDecision.visualState
                night_visual_state = night_pass.voteDecision.visualState
                effective_scene_mode = scene_mode_decision.suggestedMode
                effective_pass = day_pass if effective_scene_mode == "day_visible" else night_pass
                if effective_scene_mode == "day_visible":
                    effective_scene_profile = day_profile_selection.effectiveSceneProfile
                    twilight_profile_applied = day_profile_selection.twilightProfileApplied
                    twilight_profile_reason = day_profile_selection.twilightProfileReason
                    twilight_brightness_mean = day_profile_selection.twilightBrightnessMean
                else:
                    effective_scene_profile = "night_ir"
                    twilight_profile_applied = False
                    twilight_profile_reason = "effective_scene_mode_is_night_ir"
                    twilight_brightness_mean = (
                        scene_mode_decision.diagnostics.brightnessMean if scene_mode_decision is not None else None
                    )
                if day_visual_state == night_visual_state:
                    final_visual_state = day_visual_state
                    scene_mode_reason = f"{scene_mode_reason}; dual_path_agreed"
                else:
                    fallback_resolution = "conflict"
                    final_visual_state = "undetermined"
                    scene_mode_reason = f"{scene_mode_reason}; dual_path_conflict"
            else:
                fallback_resolution = "disabled"
                effective_scene_mode = scene_mode_decision.suggestedMode
                profile_selection = self._resolve_scene_profile(effective_scene_mode, scene_mode_decision)
                effective_scene_profile = profile_selection.effectiveSceneProfile
                twilight_profile_applied = profile_selection.twilightProfileApplied
                twilight_profile_reason = profile_selection.twilightProfileReason
                twilight_brightness_mean = profile_selection.twilightBrightnessMean
                effective_pass = self._run_detection_pass(
                    sequence=sequence,
                    target=target,
                    effective_config=profile_selection.effectiveConfig,
                )
                final_visual_state = effective_pass.voteDecision.visualState
                scene_mode_reason = f"{scene_mode_reason}; dual_path_fallback_disabled"
        else:
            effective_scene_mode = requested_scene_mode
            profile_selection = self._resolve_scene_profile(effective_scene_mode, None)
            effective_scene_profile = profile_selection.effectiveSceneProfile
            twilight_profile_applied = profile_selection.twilightProfileApplied
            twilight_profile_reason = profile_selection.twilightProfileReason
            twilight_brightness_mean = profile_selection.twilightBrightnessMean
            effective_pass = self._run_detection_pass(
                sequence=sequence,
                target=target,
                effective_config=profile_selection.effectiveConfig,
            )
            final_visual_state = effective_pass.voteDecision.visualState

        if effective_pass is None or effective_scene_mode is None or final_visual_state is None:
            return self._failure_result(
                config_path=config_path,
                requested_preset_index=requested_preset_index,
                execution_result="detect_error",
                message="Scene mode resolution did not produce an executable recognition path.",
                timing=timing,
                started_at=started_at,
                target=target,
                snapshot_path=record.snapshotPath,
                snapshot_url=record.snapshotUrl,
                scene_mode_stability=scene_mode_stability,
                effective_scene_profile=effective_scene_profile,
                twilight_profile_applied=twilight_profile_applied,
                twilight_profile_reason=twilight_profile_reason,
                twilight_brightness_mean=twilight_brightness_mean,
            )

        timing.detectMs = self._elapsed_ms(detect_started)
        logger.info(
            (
                "Temporal vote resolved %s for %s/%s requestedSceneMode=%s effectiveSceneMode=%s "
                "effectiveSceneProfile=%s "
                "confidence=%.3f fallback=%s resolution=%s passRatio=%.3f anyHardGatePassed=%s "
                "hardGatePassRatio=%.3f largestBrightComponentRatio=%.3f centerBrightCoverage=%.3f "
                "brightThresholdMean=%.2f roiBrightnessQ99Mean=%.2f threshold=%.3f "
                "globalMotionExceeded=%s visualReadiness=%s visualReadinessReason=%s "
                "staticBrightInterferenceSuppressed=%s reason=%s dayVisibleVisualState=%s nightIrVisualState=%s"
            ),
            final_visual_state,
            target.deviceId,
            target.presetIndex,
            requested_scene_mode,
            effective_scene_mode,
            effective_scene_profile,
            scene_mode_confidence or 0.0,
            fallback_used,
            fallback_resolution,
            effective_pass.voteDecision.passRatio,
            effective_pass.scoreSummary.anyHardGatePassed,
            effective_pass.scoreSummary.hardGatePassRatio or 0.0,
            effective_pass.scoreSummary.largestBrightComponentRatio or 0.0,
            effective_pass.scoreSummary.centerBrightCoverage or 0.0,
            effective_pass.scoreSummary.brightThresholdMean or 0.0,
            effective_pass.scoreSummary.roiBrightnessQ99Mean or 0.0,
            effective_pass.effectiveConfig.sequenceVoteThreshold,
            effective_pass.scoreSummary.globalMotionExceeded,
            visual_readiness.ready if visual_readiness is not None else None,
            visual_readiness.reason if visual_readiness is not None else None,
            effective_pass.voteDecision.staticBrightInterferenceSuppressed,
            scene_mode_reason,
            day_visual_state,
            night_visual_state,
        )

        roi_tolerance = effective_pass.roiTolerance

        replay_paths, replay_save = self.replay_store.persist_async(
            target=target,
            sequence=sequence,
            config_path=str(config_path),
            effective_config=effective_pass.effectiveConfig,
            extra_metadata={
                "algorithmVersion": effective_pass.effectiveConfig.algorithmVersion,
                "requestedSceneMode": requested_scene_mode,
                "effectiveSceneMode": effective_scene_mode,
                "effectiveSceneProfile": effective_scene_profile,
                "sceneMode": effective_pass.effectiveConfig.sceneMode,
                "sceneModeConfidence": scene_mode_confidence,
                "sceneModeReason": scene_mode_reason,
                "sceneModeFallbackUsed": fallback_used,
                "fallbackResolution": fallback_resolution,
                "sceneModeDiagnostics": (
                    scene_mode_decision.diagnostics.model_dump() if scene_mode_decision is not None else None
                ),
                "visualReadinessPassed": visual_readiness.ready if visual_readiness is not None else None,
                "visualReadinessReason": visual_readiness.reason if visual_readiness is not None else None,
                "visualReadiness": visual_readiness.model_dump() if visual_readiness is not None else None,
                "visualReadinessReadyFrameIndex": (
                    readiness_outcome.readyFrameIndex if readiness_outcome is not None else None
                ),
                "visualReadinessConfirmFrameIndex": (
                    readiness_outcome.confirmFrameIndex if readiness_outcome is not None else None
                ),
                "visualReadinessStartFrameIndex": 0 if readiness_outcome is not None and readiness_outcome.frames else None,
                "streamStartupFreshness": (
                    stream_startup_freshness.model_dump() if stream_startup_freshness is not None else None
                ),
                "preReadinessSessionReopened": pre_readiness_session_reopened,
                "preReadinessStreamRecovered": pre_readiness_stream_recovered,
                "preReadinessStreamRetryCount": pre_readiness_stream_retry_count,
                "streamReadFailureReason": stream_read_failure_reason,
                "streamReadFailureCount": stream_read_failure_count,
                "streamReadCallElapsedMs": stream_read_call_elapsed_ms,
                "sceneModeStability": (
                    scene_mode_stability.model_dump() if scene_mode_stability is not None else None
                ),
                **self._scene_mode_stability_metadata(scene_mode_stability),
                "focusAnchorRoi": target.focusAnchorRoi.model_dump() if target.focusAnchorRoi is not None else None,
                "focusAnchorRoiSource": focus_anchor_roi_source,
                "focusAnchorRoiFallbackUsed": focus_anchor_roi_fallback_used,
                "roiToleranceEnabled": roi_tolerance.enabled if roi_tolerance is not None else False,
                "roiToleranceCandidateCount": roi_tolerance.candidateCount if roi_tolerance is not None else 0,
                "roiToleranceEvaluatedCandidateCount": (
                    roi_tolerance.evaluatedCandidateCount if roi_tolerance is not None else 0
                ),
                "roiToleranceSelectedRoi": (
                    roi_tolerance.selectedCandidate.roi.model_dump()
                    if roi_tolerance is not None and roi_tolerance.selectedCandidate.roi is not None
                    else target.roi.model_dump()
                ),
                "roiToleranceSelectedOffsetXRatio": (
                    roi_tolerance.selectedCandidate.offsetXRatio if roi_tolerance is not None else 0.0
                ),
                "roiToleranceSelectedOffsetYRatio": (
                    roi_tolerance.selectedCandidate.offsetYRatio if roi_tolerance is not None else 0.0
                ),
                "roiToleranceSelectedScale": roi_tolerance.selectedCandidate.scale if roi_tolerance is not None else 1.0,
                "roiToleranceBaseFramePassCount": roi_tolerance.baseFramePassCount if roi_tolerance is not None else None,
                "roiToleranceSelectedFramePassCount": (
                    roi_tolerance.selectedFramePassCount if roi_tolerance is not None else None
                ),
                "roiToleranceRescued": roi_tolerance.rescued if roi_tolerance is not None else False,
                "roiToleranceCandidateMetrics": (
                    {
                        key: {
                            "framePassCount": metrics.framePassCount,
                            "hardGatePassCount": metrics.hardGatePassCount,
                            "weightedFrameScoreMean": metrics.weightedFrameScoreMean,
                            "dynamicEvidencePassCount": metrics.dynamicEvidencePassCount,
                        }
                        for key, metrics in roi_tolerance.candidateMetrics.items()
                    }
                    if roi_tolerance is not None
                    else None
                ),
                "roiToleranceCandidates": (
                    [
                        {
                            "key": candidate.key,
                            "roi": candidate.roi.model_dump() if candidate.roi is not None else None,
                            "offsetXRatio": candidate.offsetXRatio,
                            "offsetYRatio": candidate.offsetYRatio,
                            "scale": candidate.scale,
                            "isBase": candidate.isBase,
                            "skipReason": candidate.skipReason,
                        }
                        for candidate in roi_tolerance.candidates
                    ]
                    if roi_tolerance is not None
                    else None
                ),
                "roiToleranceSelectedFrameIndex": effective_pass.representativeIndex,
                "twilightProfileApplied": twilight_profile_applied,
                "twilightProfileReason": twilight_profile_reason,
                "twilightBrightnessMean": twilight_brightness_mean,
                "sampleStartFrameIndex": 0,
                "sampleQualityPassed": sample_quality.passed if sample_quality is not None else None,
                "sampleQualityReason": sample_quality.reason if sample_quality is not None else None,
                "sampleQuality": sample_quality.model_dump() if sample_quality is not None else None,
                "sampleQualityMaxRecoveriesConfigured": effective_pass.effectiveConfig.sampleQualityMaxRecoveries,
                "sampleQualityRecoveryCountSemantics": self._sample_quality_recovery_count_semantics(),
                "sampleQualityRejectSharpnessCount": (
                    sample_quality.rejectSharpnessCount if sample_quality is not None else None
                ),
                "sampleQualityRejectClearCellRatioCount": (
                    sample_quality.rejectClearCellRatioCount if sample_quality is not None else None
                ),
                "sampleQualityRejectStabilityCount": (
                    sample_quality.rejectStabilityCount if sample_quality is not None else None
                ),
                "sampleQualityFirstRejectedFrameIndex": (
                    sample_quality.firstRejectedFrameIndex if sample_quality is not None else None
                ),
                "sampleQualityFirstRejectedElapsedMs": (
                    sample_quality.firstRejectedElapsedMs if sample_quality is not None else None
                ),
                "sampleQualityFirstRejectedSharpness": (
                    sample_quality.firstRejectedSharpness if sample_quality is not None else None
                ),
                "sampleQualityFirstRejectedClearCellRatio": (
                    sample_quality.firstRejectedClearCellRatio if sample_quality is not None else None
                ),
                "sampleQualityFirstRejectedStability": (
                    sample_quality.firstRejectedStability if sample_quality is not None else None
                ),
                "sampleQualityLastRejectedFrameIndex": (
                    sample_quality.lastRejectedFrameIndex if sample_quality is not None else None
                ),
                "sampleQualityLastRejectedElapsedMs": (
                    sample_quality.lastRejectedElapsedMs if sample_quality is not None else None
                ),
                "sampleQualityLastRejectedSharpness": (
                    sample_quality.lastRejectedSharpness if sample_quality is not None else None
                ),
                "sampleQualityLastRejectedClearCellRatio": (
                    sample_quality.lastRejectedClearCellRatio if sample_quality is not None else None
                ),
                "sampleQualityLastRejectedStability": (
                    sample_quality.lastRejectedStability if sample_quality is not None else None
                ),
                "streamReadFailureReason": (
                    sample_quality.streamReadFailureReason if sample_quality is not None else None
                ),
                "streamReadFailureCount": (
                    sample_quality.streamReadFailureCount if sample_quality is not None else None
                ),
                "sampleQualityStreamRecovered": (
                    sample_quality.sampleQualityStreamRecovered if sample_quality is not None else None
                ),
                "sampleQualitySessionReopened": (
                    sample_quality.sampleQualitySessionReopened if sample_quality is not None else None
                ),
                "sampleQualityStreamRetryCount": (
                    sample_quality.sampleQualityStreamRetryCount if sample_quality is not None else None
                ),
                "sampleQualityAttemptRestarted": (
                    sample_quality.restartedDuringSampling if sample_quality is not None else None
                ),
                "sampleQualityRecoveryCount": sample_quality.recoveryCount if sample_quality is not None else None,
                "sampleQualityAcceptedMiddleFrameIndex": (
                    max(0, sequence.sampledFrameCount // 2) if sequence.sampledFrameCount > 0 else None
                ),
                "sampleQualityAcceptedEndFrameIndex": (
                    sequence.sampledFrameCount - 1 if sequence.sampledFrameCount > 0 else None
                ),
                "dayVisibleVisualState": day_visual_state,
                "nightIrVisualState": night_visual_state,
                "brightThresholdMean": effective_pass.scoreSummary.brightThresholdMean,
                "roiBrightnessQ99Mean": effective_pass.scoreSummary.roiBrightnessQ99Mean,
                "roiBrightnessMaxMean": effective_pass.scoreSummary.roiBrightnessMaxMean,
                "snapshotPath": record.snapshotPath,
                "snapshotUrl": record.snapshotUrl,
                "alignmentApplied": effective_pass.scoreSummary.alignmentApplied,
                "globalMotionExceeded": effective_pass.scoreSummary.globalMotionExceeded,
                "anyHardGatePassed": effective_pass.scoreSummary.anyHardGatePassed,
                "hardGatePassed": effective_pass.scoreSummary.hardGatePassed,
                "hardGatePassCount": effective_pass.scoreSummary.hardGatePassCount,
                "hardGatePassRatio": effective_pass.scoreSummary.hardGatePassRatio,
                "dynamicEvidencePassCount": effective_pass.scoreSummary.dynamicEvidencePassCount,
                "largestBrightComponentPassCount": effective_pass.scoreSummary.largestBrightComponentPassCount,
                "centerBrightCoveragePassCount": effective_pass.scoreSummary.centerBrightCoveragePassCount,
                "verticalSpreadPassCount": effective_pass.scoreSummary.verticalSpreadPassCount,
                "continuousBrightPassCount": effective_pass.scoreSummary.continuousBrightPassCount,
                "gapFillPassCount": effective_pass.scoreSummary.gapFillPassCount,
                "hardGateMinGapFillRatioConfigured": effective_pass.scoreSummary.hardGateMinGapFillRatioConfigured,
                "framePassCount": effective_pass.scoreSummary.framePassCount,
                "framePassRatio": effective_pass.voteDecision.passRatio,
                "largestBrightComponentRatio": effective_pass.scoreSummary.largestBrightComponentRatio,
                "brightComponentCount": effective_pass.scoreSummary.brightComponentCount,
                "fragmentationScore": effective_pass.scoreSummary.fragmentationScore,
                "centerBrightCoverage": effective_pass.scoreSummary.centerBrightCoverage,
                "upperHalfBrightRatio": effective_pass.scoreSummary.upperHalfBrightRatio,
                "lowerHalfBrightRatio": effective_pass.scoreSummary.lowerHalfBrightRatio,
                "verticalSpreadRatio": effective_pass.scoreSummary.verticalSpreadRatio,
                "gapFillRatio": effective_pass.scoreSummary.gapFillRatio,
                "temporalAreaVariance": effective_pass.scoreSummary.temporalAreaVariance,
                "temporalShapeVariance": effective_pass.scoreSummary.temporalShapeVariance,
                "sequenceVoteThreshold": effective_pass.effectiveConfig.sequenceVoteThreshold,
                "overflowFrameRatio": effective_pass.voteDecision.overflowFrameRatio,
                "motionReductionRatio": effective_pass.voteDecision.motionReductionRatio,
                "temporalVoteReason": effective_pass.voteDecision.reason,
                "staticBrightInterferenceSuppressed": effective_pass.voteDecision.staticBrightInterferenceSuppressed,
                "visualState": final_visual_state,
                "representativeFrameIndex": effective_pass.representativeIndex,
            },
            readiness_key_frames=self._combined_debug_key_frames(
                stream_startup_freshness_result=stream_startup_freshness_result,
                scene_mode_stability_result=scene_mode_stability_result,
                readiness_outcome=readiness_outcome,
                sample_quality_guard_result=sample_quality_guard_result,
            ),
        )

        timing.totalMs = self._elapsed_ms(started_at)
        return RecognitionRunResult(
            executionResult="success",
            visualState=final_visual_state,
            sceneMode=effective_scene_mode,
            requestedSceneMode=requested_scene_mode,
            effectiveSceneMode=effective_scene_mode,
            effectiveSceneProfile=effective_scene_profile,
            sceneModeConfidence=scene_mode_confidence,
            sceneModeReason=scene_mode_reason,
            sceneModeFallbackUsed=fallback_used,
            sceneModeDiagnostics=scene_mode_decision.diagnostics if scene_mode_decision is not None else None,
            twilightProfileApplied=twilight_profile_applied,
            twilightProfileReason=twilight_profile_reason,
            twilightBrightnessMean=twilight_brightness_mean,
            focusAnchorRoiFallbackUsed=focus_anchor_roi_fallback_used,
            focusAnchorRoiSource=focus_anchor_roi_source,
            dayVisibleVisualState=day_visual_state,
            nightIrVisualState=night_visual_state,
            fallbackResolution=fallback_resolution,
            streamStartupFreshness=stream_startup_freshness,
            sceneModeStability=scene_mode_stability,
            visualReadinessPassed=visual_readiness.ready if visual_readiness is not None else None,
            visualReadinessReason=visual_readiness.reason if visual_readiness is not None else None,
            visualReadiness=visual_readiness,
            sampleQualityPassed=sample_quality.passed if sample_quality is not None else None,
            sampleQualityReason=sample_quality.reason if sample_quality is not None else None,
            sampleQualityMaxRecoveriesConfigured=effective_pass.effectiveConfig.sampleQualityMaxRecoveries,
            sampleQualityRecoveryCountSemantics=self._sample_quality_recovery_count_semantics(),
            sampleQuality=sample_quality,
            preReadinessSessionReopened=pre_readiness_session_reopened,
            preReadinessStreamRecovered=pre_readiness_stream_recovered,
            preReadinessStreamRetryCount=pre_readiness_stream_retry_count,
            streamReadFailureReason=stream_read_failure_reason,
            streamReadFailureCount=stream_read_failure_count,
            streamReadCallElapsedMs=stream_read_call_elapsed_ms,
            roiToleranceEnabled=roi_tolerance.enabled if roi_tolerance is not None else False,
            roiToleranceCandidateCount=roi_tolerance.candidateCount if roi_tolerance is not None else 0,
            roiToleranceEvaluatedCandidateCount=(
                roi_tolerance.evaluatedCandidateCount if roi_tolerance is not None else 0
            ),
            roiToleranceSelectedRoi=(
                roi_tolerance.selectedCandidate.roi if roi_tolerance is not None else target.roi
            ),
            roiToleranceSelectedOffsetXRatio=(
                roi_tolerance.selectedCandidate.offsetXRatio if roi_tolerance is not None else 0.0
            ),
            roiToleranceSelectedOffsetYRatio=(
                roi_tolerance.selectedCandidate.offsetYRatio if roi_tolerance is not None else 0.0
            ),
            roiToleranceSelectedScale=(roi_tolerance.selectedCandidate.scale if roi_tolerance is not None else 1.0),
            roiToleranceBaseFramePassCount=(roi_tolerance.baseFramePassCount if roi_tolerance is not None else None),
            roiToleranceSelectedFramePassCount=(
                roi_tolerance.selectedFramePassCount if roi_tolerance is not None else None
            ),
            roiToleranceRescued=(roi_tolerance.rescued if roi_tolerance is not None else False),
            scoreSummary=effective_pass.scoreSummary,
            evidencePaths=RecognitionEvidencePaths(
                calibrationPath=str(config_path),
                snapshotPath=record.snapshotPath,
                snapshotUrl=record.snapshotUrl,
                streamStartupStartFramePath=replay_paths.get("streamStartupStartFramePath"),
                streamStartupSettledFramePath=replay_paths.get("streamStartupSettledFramePath"),
                sceneModeStabilityStartFramePath=replay_paths.get("sceneModeStabilityStartFramePath"),
                sceneModeStabilitySettledFramePath=replay_paths.get("sceneModeStabilitySettledFramePath"),
                sceneProbeStartFramePath=replay_paths.get("sceneProbeStartFramePath"),
                sceneProbeEndFramePath=replay_paths.get("sceneProbeEndFramePath"),
                representativeFramePath=replay_paths.get("representativeFramePath"),
                roiToleranceSelectedFramePath=replay_paths.get("roiToleranceSelectedFramePath"),
                visualReadinessStartFramePath=replay_paths.get("visualReadinessStartFramePath"),
                visualReadinessReadyFramePath=replay_paths.get("visualReadinessReadyFramePath"),
                visualReadinessConfirmFramePath=replay_paths.get("visualReadinessConfirmFramePath"),
                sampleStartFramePath=replay_paths.get("sampleStartFramePath"),
                sampleQualityAttemptStartFramePath=replay_paths.get("sampleQualityAttemptStartFramePath"),
                sampleQualityDegradedFramePath=replay_paths.get("sampleQualityDegradedFramePath"),
                sampleQualityLastQualifiedFramePath=replay_paths.get("sampleQualityLastQualifiedFramePath"),
                sampleQualityAcceptedMiddleFramePath=replay_paths.get("sampleQualityAcceptedMiddleFramePath"),
                sampleQualityAcceptedEndFramePath=replay_paths.get("sampleQualityAcceptedEndFramePath"),
                replaySequencePath=replay_paths.get("sequencePath"),
                replayMetadataPath=replay_paths.get("metadataPath"),
                debugImagePath=replay_paths.get("debugImagePath"),
                recognitionConfigSnapshotPath=replay_paths.get("configSnapshotPath"),
            ),
            replaySave=replay_save,
            timing=timing,
            algorithmVersion=effective_pass.effectiveConfig.algorithmVersion,
            configPath=str(config_path),
            target=target,
            message=(
                f"Temporal vote resolved {final_visual_state}: "
                f"{sequence.sampledFrameCount}/{sequence.targetFrameCount} frames, "
                f"passRatio={effective_pass.voteDecision.passRatio:.3f}, "
                f"visualReadiness={visual_readiness.reason if visual_readiness is not None else 'not_checked'}, "
                f"requestedSceneMode={requested_scene_mode}, "
                f"effectiveSceneMode={effective_scene_mode}, "
                f"effectiveSceneProfile={effective_scene_profile}, "
                f"fallbackResolution={fallback_resolution}"
            ),
        )

    def _sample_with_quality_guard(
        self,
        *,
        session: object,
        effective_config: RecognitionGlobalConfig,
        target: RecognitionTarget,
        readiness_outcome: VisualReadinessOutcome | None,
        focus_anchor_roi: RoiModel | None = None,
    ) -> tuple[SampledSequence | None, _SampleQualityGuardResult]:
        checker = VisualReadinessChecker(effective_config)
        if focus_anchor_roi is None:
            focus_anchor_roi, _, _ = self._focus_anchor_roi(target)
        frame_interval_s = 1.0 / float(effective_config.sampleFps)
        target_frame_count = effective_config.sequenceFrameCount
        started_at = monotonic()
        deadline = started_at + effective_config.sampleQualityTimeoutMs / 1000

        seed_frames = self._sample_quality_seed_frames(readiness_outcome)
        seed_index = 0
        candidate_frames: list[np.ndarray] = []
        candidate_capture_times: list[float] = []
        observed_frames: list[np.ndarray] = []
        observed_timestamps_ms: list[int] = []
        previous_sample_gray: np.ndarray | None = None
        next_sample_at: float | None = None
        recovery_count = 0
        rejected_frames = 0
        restarted_during_sampling = False
        reused_readiness_frames = 0
        max_qualified_frames = 0
        best_window_ms = 0
        max_allowed_window_ms = self._sample_quality_max_allowed_window_ms(effective_config)
        attempt_start_frame: np.ndarray | None = None
        degraded_frame: np.ndarray | None = None
        last_qualified_frame: np.ndarray | None = None
        latest_failure_reason: str | None = None
        stream_read_failure_reason: str | None = None
        stream_read_failure_count = 0
        sample_quality_session_reopened = False
        sample_quality_stream_retry_count = 0
        window_reject_details: _SampleQualityWindowRejectDetails | None = None
        reject_diagnostics = _SampleQualityRejectDiagnostics()

        while monotonic() < deadline:
            frame_result: tuple[np.ndarray, float] | None
            from_seed = False
            if seed_index < len(seed_frames):
                frame_result = seed_frames[seed_index]
                seed_index += 1
                from_seed = True
            else:
                frame_result = session.read_frame_until(deadline)
            if frame_result is None:
                session_failure_reason = self._stream_read_failure_reason(session)
                session_failure_count = self._stream_read_failure_count(session)
                if self._is_stream_read_failure_reason(session_failure_reason):
                    stream_read_failure_reason = session_failure_reason
                    stream_read_failure_count = max(stream_read_failure_count, session_failure_count)
                    latest_failure_reason = self._sample_quality_stream_failure_reason(session_failure_reason)
                    if sample_quality_stream_retry_count < 1:
                        logger.warning(
                            "Sample quality stream failure %s for %s/%s; reopening FLV session once",
                            session_failure_reason,
                            target.deviceId,
                            target.channelId,
                        )
                        reopened_session = self._reopen_sample_quality_session(
                            session=session,
                            target=target,
                        )
                        sample_quality_stream_retry_count += 1
                        sample_quality_session_reopened = True
                        if reopened_session is not None:
                            session = reopened_session
                            candidate_frames = []
                            candidate_capture_times = []
                            next_sample_at = None
                            reused_readiness_frames = 0
                            previous_sample_gray = None
                            restarted_during_sampling = True
                            continue
                break

            frame, captured_at = frame_result
            if next_sample_at is not None and captured_at + 0.002 < next_sample_at:
                continue

            frame_stats, previous_sample_gray = checker.evaluate_frame_quality(
                frame,
                frame_index=len(observed_frames),
                captured_at=captured_at,
                roi=focus_anchor_roi,
                previous_gray=previous_sample_gray,
                use_roi_for_stability=False,
            )
            observed_frames.append(frame.copy())
            observed_timestamps_ms.append(int(round((captured_at - started_at) * 1000)))

            if self._sample_quality_frame_passed(frame_stats, effective_config):
                if not candidate_frames:
                    if attempt_start_frame is None:
                        attempt_start_frame = frame.copy()
                    candidate_frames = [frame.copy()]
                    candidate_capture_times = [captured_at]
                    next_sample_at = captured_at + frame_interval_s
                    reused_readiness_frames = 1 if from_seed else 0
                else:
                    candidate_frames.append(frame.copy())
                    candidate_capture_times.append(captured_at)
                    next_sample_at = candidate_capture_times[0] + (len(candidate_frames) * frame_interval_s)
                    if from_seed:
                        reused_readiness_frames += 1

                max_qualified_frames = max(max_qualified_frames, len(candidate_frames))
                last_qualified_frame = frame.copy()
                if len(candidate_capture_times) > 1:
                    candidate_window_ms = int(
                        round((candidate_capture_times[-1] - candidate_capture_times[0]) * 1000)
                    )
                    best_window_ms = max(best_window_ms, candidate_window_ms)
                    if self._sample_quality_window_too_long(candidate_window_ms, effective_config):
                        latest_failure_reason = "sample_quality_window_too_long"
                        window_reject_details = _SampleQualityWindowRejectDetails(
                            candidateWindowMs=candidate_window_ms,
                            maxAllowedWindowMs=max_allowed_window_ms,
                            candidateFrameCount=len(candidate_frames),
                            triggerSharpness=frame_stats.sharpness,
                            triggerClearCellRatio=frame_stats.clearCellRatio,
                            triggerStability=frame_stats.stability,
                        )
                        rejected_frames += 1
                        if degraded_frame is None:
                            degraded_frame = frame.copy()
                        recovery_count += 1
                        restarted_during_sampling = True
                        if recovery_count > effective_config.sampleQualityMaxRecoveries:
                            break
                        candidate_frames = []
                        candidate_capture_times = []
                        next_sample_at = None
                        reused_readiness_frames = 0
                        previous_sample_gray = None
                        continue
                if len(candidate_frames) >= target_frame_count:
                    sampled_timestamps = [
                        int(round((captured - candidate_capture_times[0]) * 1000))
                        for captured in candidate_capture_times
                    ]
                    sequence = self.sampler.build_sequence_from_frames(
                        stream_type=session.streamType,
                        stream_url=session.streamUrl,
                        frames=candidate_frames,
                        timestamps_ms=sampled_timestamps,
                        configured_sample_fps=float(effective_config.sampleFps),
                        configured_duration_ms=effective_config.sampleDurationMs,
                        target_frame_count=target_frame_count,
                    )
                    elapsed_ms = max(1, int(round((captured_at - started_at) * 1000)))
                    metrics = SampleQualityMetrics(
                        passed=True,
                        reason=(
                            "sample_quality_recovered_and_passed"
                            if restarted_during_sampling
                            else "sample_quality_passed"
                        ),
                        recoveryCount=recovery_count,
                        restartCount=recovery_count,
                        qualifiedFramesCollected=max_qualified_frames,
                        acceptedFrameCount=sequence.sampledFrameCount,
                        rejectedFrames=rejected_frames,
                        reusedReadinessFrames=min(reused_readiness_frames, sequence.sampledFrameCount),
                        restartedDuringSampling=restarted_during_sampling,
                        elapsedMs=elapsed_ms,
                        sampleWindowMsActual=sampled_timestamps[-1] if sampled_timestamps else 0,
                        sampleWindowMaxAllowedMs=max_allowed_window_ms,
                        rejectSharpnessCount=reject_diagnostics.rejectSharpnessCount,
                        rejectClearCellRatioCount=reject_diagnostics.rejectClearCellRatioCount,
                        rejectStabilityCount=reject_diagnostics.rejectStabilityCount,
                        firstRejectedFrameIndex=reject_diagnostics.firstRejectedFrameIndex,
                        firstRejectedElapsedMs=reject_diagnostics.firstRejectedElapsedMs,
                        firstRejectedSharpness=reject_diagnostics.firstRejectedSharpness,
                        firstRejectedClearCellRatio=reject_diagnostics.firstRejectedClearCellRatio,
                        firstRejectedStability=reject_diagnostics.firstRejectedStability,
                        lastRejectedFrameIndex=reject_diagnostics.lastRejectedFrameIndex,
                        lastRejectedElapsedMs=reject_diagnostics.lastRejectedElapsedMs,
                        lastRejectedSharpness=reject_diagnostics.lastRejectedSharpness,
                        lastRejectedClearCellRatio=reject_diagnostics.lastRejectedClearCellRatio,
                        lastRejectedStability=reject_diagnostics.lastRejectedStability,
                        streamReadFailureReason=stream_read_failure_reason,
                        streamReadFailureCount=stream_read_failure_count,
                        sampleQualityStreamRecovered=sample_quality_stream_retry_count > 0,
                        sampleQualitySessionReopened=sample_quality_session_reopened,
                        sampleQualityStreamRetryCount=sample_quality_stream_retry_count,
                    )
                    accepted_middle_frame = (
                        candidate_frames[len(candidate_frames) // 2].copy() if candidate_frames else None
                    )
                    accepted_end_frame = candidate_frames[-1].copy() if candidate_frames else None
                    return sequence, _SampleQualityGuardResult(
                        passed=True,
                        sequence=sequence,
                        metrics=metrics,
                        streamType=session.streamType,
                        streamUrl=session.streamUrl,
                        activeSession=session,
                        attemptStartFrame=attempt_start_frame,
                        degradedFrame=degraded_frame,
                        lastQualifiedFrame=last_qualified_frame,
                        acceptedMiddleFrame=accepted_middle_frame,
                        acceptedEndFrame=accepted_end_frame,
                        observedFrames=observed_frames,
                        observedTimestampsMs=observed_timestamps_ms,
                    )
                continue

            self._record_sample_quality_reject_diagnostics(
                diagnostics=reject_diagnostics,
                frame_stats=frame_stats,
                effective_config=effective_config,
                started_at=started_at,
            )
            rejected_frames += 1
            if degraded_frame is None:
                degraded_frame = frame.copy()
            if candidate_frames:
                latest_failure_reason = self._sample_quality_failure_reason(
                    current_candidate_length=len(candidate_frames),
                    target_frame_count=target_frame_count,
                    recovery_count=recovery_count,
                    max_recoveries=effective_config.sampleQualityMaxRecoveries,
                    timed_out=False,
                )
                recovery_count += 1
                restarted_during_sampling = True
                if recovery_count > effective_config.sampleQualityMaxRecoveries:
                    break
                candidate_frames = []
                candidate_capture_times = []
                next_sample_at = None
                reused_readiness_frames = 0
                previous_sample_gray = None

        elapsed_ms = max(1, int(round((monotonic() - started_at) * 1000)))
        reason = self._sample_quality_failure_reason(
            current_candidate_length=len(candidate_frames),
            target_frame_count=target_frame_count,
            recovery_count=recovery_count,
            max_recoveries=effective_config.sampleQualityMaxRecoveries,
            timed_out=True,
            latest_failure_reason=latest_failure_reason,
        )
        metrics = SampleQualityMetrics(
            passed=False,
            reason=reason,
            recoveryCount=recovery_count,
            restartCount=recovery_count,
            qualifiedFramesCollected=max(max_qualified_frames, len(candidate_frames)),
            acceptedFrameCount=0,
            rejectedFrames=rejected_frames,
            reusedReadinessFrames=reused_readiness_frames,
            restartedDuringSampling=restarted_during_sampling,
            elapsedMs=elapsed_ms,
            sampleWindowMsActual=max(
                best_window_ms,
                int(round((candidate_capture_times[-1] - candidate_capture_times[0]) * 1000))
                if len(candidate_capture_times) > 1
                else 0,
            ),
            sampleWindowMaxAllowedMs=max_allowed_window_ms,
            lastFailureReason=latest_failure_reason,
            rejectSharpnessCount=reject_diagnostics.rejectSharpnessCount,
            rejectClearCellRatioCount=reject_diagnostics.rejectClearCellRatioCount,
            rejectStabilityCount=reject_diagnostics.rejectStabilityCount,
            firstRejectedFrameIndex=reject_diagnostics.firstRejectedFrameIndex,
            firstRejectedElapsedMs=reject_diagnostics.firstRejectedElapsedMs,
            firstRejectedSharpness=reject_diagnostics.firstRejectedSharpness,
            firstRejectedClearCellRatio=reject_diagnostics.firstRejectedClearCellRatio,
            firstRejectedStability=reject_diagnostics.firstRejectedStability,
            lastRejectedFrameIndex=reject_diagnostics.lastRejectedFrameIndex,
            lastRejectedElapsedMs=reject_diagnostics.lastRejectedElapsedMs,
            lastRejectedSharpness=reject_diagnostics.lastRejectedSharpness,
            lastRejectedClearCellRatio=reject_diagnostics.lastRejectedClearCellRatio,
            lastRejectedStability=reject_diagnostics.lastRejectedStability,
            windowTooLongRejected=window_reject_details is not None,
            windowTooLongCandidateFrameCount=(
                window_reject_details.candidateFrameCount if window_reject_details is not None else 0
            ),
            windowTooLongTriggerSharpness=(
                window_reject_details.triggerSharpness if window_reject_details is not None else None
            ),
            windowTooLongTriggerClearCellRatio=(
                window_reject_details.triggerClearCellRatio if window_reject_details is not None else None
            ),
            windowTooLongTriggerStability=(
                window_reject_details.triggerStability if window_reject_details is not None else None
            ),
            streamReadFailureReason=stream_read_failure_reason,
            streamReadFailureCount=stream_read_failure_count,
            sampleQualityStreamRecovered=False,
            sampleQualitySessionReopened=sample_quality_session_reopened,
            sampleQualityStreamRetryCount=sample_quality_stream_retry_count,
        )
        return None, _SampleQualityGuardResult(
            passed=False,
            sequence=None,
            metrics=metrics,
            streamType=session.streamType,
            streamUrl=session.streamUrl,
            activeSession=session,
            attemptStartFrame=attempt_start_frame,
            degradedFrame=degraded_frame,
            lastQualifiedFrame=last_qualified_frame,
            acceptedMiddleFrame=None,
            acceptedEndFrame=None,
            observedFrames=observed_frames,
            observedTimestampsMs=observed_timestamps_ms,
        )

    @staticmethod
    def _sample_quality_frame_passed(
        frame_stats: FrameQualityEvaluation,
        effective_config: RecognitionGlobalConfig,
    ) -> bool:
        stability_threshold = RunOnceService._sample_quality_stability_threshold(effective_config)
        return (
            frame_stats.sharpness >= effective_config.visualReadinessMinSharpness
            and frame_stats.clearCellRatio >= effective_config.visualReadinessMinSharpCellRatio
            and frame_stats.stability <= stability_threshold
        )

    @staticmethod
    def _sample_quality_stability_threshold(effective_config: RecognitionGlobalConfig) -> float:
        return max(effective_config.visualReadinessMaxStabilityScore * 1.5, 0.18)

    @classmethod
    def _record_sample_quality_reject_diagnostics(
        cls,
        *,
        diagnostics: _SampleQualityRejectDiagnostics,
        frame_stats: FrameQualityEvaluation,
        effective_config: RecognitionGlobalConfig,
        started_at: float,
    ) -> None:
        sharpness_failed = frame_stats.sharpness < effective_config.visualReadinessMinSharpness
        clear_cell_failed = frame_stats.clearCellRatio < effective_config.visualReadinessMinSharpCellRatio
        stability_failed = frame_stats.stability > cls._sample_quality_stability_threshold(effective_config)

        if sharpness_failed:
            diagnostics.rejectSharpnessCount += 1
        if clear_cell_failed:
            diagnostics.rejectClearCellRatioCount += 1
        if stability_failed:
            diagnostics.rejectStabilityCount += 1

        if not (sharpness_failed or clear_cell_failed or stability_failed):
            return

        elapsed_ms = max(0, int(round((frame_stats.capturedAt - started_at) * 1000)))
        if diagnostics.firstRejectedFrameIndex is None:
            diagnostics.firstRejectedFrameIndex = frame_stats.frameIndex
            diagnostics.firstRejectedElapsedMs = elapsed_ms
            diagnostics.firstRejectedSharpness = frame_stats.sharpness
            diagnostics.firstRejectedClearCellRatio = frame_stats.clearCellRatio
            diagnostics.firstRejectedStability = frame_stats.stability

        diagnostics.lastRejectedFrameIndex = frame_stats.frameIndex
        diagnostics.lastRejectedElapsedMs = elapsed_ms
        diagnostics.lastRejectedSharpness = frame_stats.sharpness
        diagnostics.lastRejectedClearCellRatio = frame_stats.clearCellRatio
        diagnostics.lastRejectedStability = frame_stats.stability

    @staticmethod
    def _sample_quality_max_allowed_window_ms(effective_config: RecognitionGlobalConfig) -> int:
        frame_interval_ms = max(1, int(round(1000 / float(effective_config.sampleFps))))
        return effective_config.sampleDurationMs + max(40, frame_interval_ms // 2)

    @classmethod
    def _sample_quality_window_too_long(
        cls,
        candidate_window_ms: int,
        effective_config: RecognitionGlobalConfig,
    ) -> bool:
        return candidate_window_ms > cls._sample_quality_max_allowed_window_ms(effective_config)

    @staticmethod
    def _sample_quality_seed_frames(
        readiness_outcome: VisualReadinessOutcome | None,
    ) -> list[tuple[np.ndarray, float]]:
        if readiness_outcome is None or not readiness_outcome.frames or not readiness_outcome.frameCapturedAts:
            return []
        start_index = readiness_outcome.readyFrameIndex
        if start_index is None:
            return []
        end_index = readiness_outcome.confirmFrameIndex
        if end_index is None or end_index < start_index:
            end_index = start_index
        seed_frames: list[tuple[np.ndarray, float]] = []
        for index in range(start_index, min(end_index + 1, len(readiness_outcome.frames))):
            if index >= len(readiness_outcome.frameCapturedAts):
                break
            seed_frames.append((readiness_outcome.frames[index].copy(), readiness_outcome.frameCapturedAts[index]))
        return seed_frames

    @staticmethod
    def _sample_quality_execution_result(reason: str) -> ExecutionResult:
        if reason == "sample_quality_recovery_budget_exhausted":
            return "sample_quality_degraded"
        return "sample_quality_timeout"

    @staticmethod
    def _is_stream_read_failure_reason(reason: str | None) -> bool:
        return reason in {"stream_read_timeout", "stream_eof", "stream_read_failed"}

    @staticmethod
    def _sample_quality_stream_failure_reason(stream_read_failure_reason: str) -> str:
        if stream_read_failure_reason == "stream_read_timeout":
            return "sample_quality_stream_read_timeout"
        return "sample_quality_stream_interrupted"

    @staticmethod
    def _stream_read_failure_reason(session: object) -> str | None:
        return getattr(session, "lastReadFailureReason", None)

    @staticmethod
    def _stream_read_failure_count(session: object) -> int:
        count = getattr(session, "lastReadFailureCount", 0)
        return int(count) if isinstance(count, (int, float)) else 0

    @staticmethod
    def _sample_quality_failure_reason(
        *,
        current_candidate_length: int,
        target_frame_count: int,
        recovery_count: int,
        max_recoveries: int,
        timed_out: bool,
        latest_failure_reason: str | None = None,
    ) -> str:
        if latest_failure_reason in {
            "sample_quality_stream_interrupted",
            "sample_quality_stream_read_timeout",
            "sample_quality_window_too_long",
        }:
            return latest_failure_reason
        if recovery_count > max_recoveries:
            return "sample_quality_recovery_budget_exhausted"
        if current_candidate_length >= max(1, target_frame_count - 1):
            return "sample_quality_near_complete_but_broken"
        if current_candidate_length >= max(2, target_frame_count // 2):
            return "sample_quality_focus_regressed"
        if latest_failure_reason is not None and timed_out:
            return latest_failure_reason
        return "sample_quality_blurry_after_ready"

    def _reopen_sample_quality_session(
        self,
        *,
        session: object,
        target: RecognitionTarget,
    ) -> object | None:
        try:
            session.release()
        except Exception:
            logger.debug("Ignoring FLV session release failure during sample-quality reopen", exc_info=True)

        try:
            return self.sampler.open_session(device_id=target.deviceId, channel_id=target.channelId)
        except FlvSamplerError as error:
            logger.warning(
                "Sample quality reopen failed for %s/%s: %s (%s)",
                target.deviceId,
                target.channelId,
                error,
                error.reason,
            )
            return None

    def _persist_scene_mode_transition_replay(
        self,
        *,
        target: RecognitionTarget,
        stability_result: SceneModeStabilityResult | None,
        config_path: str,
        effective_config: RecognitionGlobalConfig,
        requested_scene_mode: str,
        focus_anchor_roi_fallback_used: bool | None,
        focus_anchor_roi_source: str | None,
        stream_startup_freshness: StreamStartupFreshnessMetrics | None,
        stream_startup_freshness_result: _StreamStartupFreshnessResult | None,
        visual_readiness: VisualReadinessMetrics | None = None,
        readiness_outcome: VisualReadinessOutcome | None = None,
    ) -> tuple[dict[str, str], ReplaySaveState]:
        if stability_result is None or not stability_result.observedFrames:
            return {}, ReplaySaveState(status="disabled", message="No scene-mode-stability frames captured")

        execution_result = self._scene_mode_execution_result(stability_result.reason)
        sequence = self.sampler.build_sequence_from_frames(
            stream_type=stability_result.streamType,
            stream_url=stability_result.streamUrl,
            frames=stability_result.observedFrames,
            timestamps_ms=stability_result.frameTimestampsMs,
            configured_sample_fps=float(self.global_config.sampleFps),
            configured_duration_ms=self.global_config.sceneModeStabilityTimeoutMs,
            target_frame_count=len(stability_result.observedFrames),
        )
        scene_mode_stability = self._scene_mode_stability_metrics(stability_result)
        return self.replay_store.persist_async(
            target=target,
            sequence=sequence,
            config_path=config_path,
            effective_config=effective_config,
            extra_metadata={
                "executionResult": execution_result,
                "visualState": "undetermined",
                "requestedSceneMode": requested_scene_mode,
                "effectiveSceneMode": stability_result.finalMode,
                "sceneMode": effective_config.sceneMode,
                "sceneModeStability": (
                    scene_mode_stability.model_dump() if scene_mode_stability is not None else None
                ),
                **self._scene_mode_stability_metadata(scene_mode_stability),
                "visualReadinessPassed": visual_readiness.ready if visual_readiness is not None else None,
                "visualReadinessReason": visual_readiness.reason if visual_readiness is not None else None,
                "visualReadiness": visual_readiness.model_dump() if visual_readiness is not None else None,
                "visualReadinessReadyFrameIndex": (
                    readiness_outcome.readyFrameIndex if readiness_outcome is not None else None
                ),
                "visualReadinessConfirmFrameIndex": (
                    readiness_outcome.confirmFrameIndex if readiness_outcome is not None else None
                ),
                "visualReadinessStartFrameIndex": 0 if readiness_outcome is not None and readiness_outcome.frames else None,
                "focusAnchorRoi": target.focusAnchorRoi.model_dump() if target.focusAnchorRoi is not None else None,
                "focusAnchorRoiSource": focus_anchor_roi_source,
                "focusAnchorRoiFallbackUsed": focus_anchor_roi_fallback_used,
                "streamStartupFreshness": (
                    stream_startup_freshness.model_dump() if stream_startup_freshness is not None else None
                ),
                "sampleQualityMaxRecoveriesConfigured": effective_config.sampleQualityMaxRecoveries,
                "sampleQualityRecoveryCountSemantics": self._sample_quality_recovery_count_semantics(),
            },
            readiness_key_frames=self._combined_debug_key_frames(
                stream_startup_freshness_result=stream_startup_freshness_result,
                scene_mode_stability_result=stability_result,
                readiness_outcome=readiness_outcome,
            ),
        )

    def _persist_sample_quality_replay(
        self,
        *,
        target: RecognitionTarget,
        guard_result: _SampleQualityGuardResult,
        config_path: str,
        execution_result: ExecutionResult,
        effective_config: RecognitionGlobalConfig,
        requested_scene_mode: str,
        effective_scene_mode: ResolvedSceneMode | None,
        scene_mode_decision: SceneModeDecision | None,
        effective_scene_profile: EffectiveSceneProfile | None,
        twilight_profile_applied: bool | None,
        twilight_profile_reason: str | None,
        twilight_brightness_mean: float | None,
        focus_anchor_roi_fallback_used: bool | None,
        focus_anchor_roi_source: str | None,
        stream_startup_freshness: StreamStartupFreshnessMetrics | None,
        stream_startup_freshness_result: _StreamStartupFreshnessResult | None,
        scene_mode_stability: SceneModeStabilityMetrics | None,
        scene_mode_stability_result: SceneModeStabilityResult | None,
        visual_readiness: VisualReadinessMetrics | None,
        readiness_outcome: VisualReadinessOutcome | None,
    ) -> tuple[dict[str, str], ReplaySaveState]:
        if not guard_result.observedFrames:
            return {}, ReplaySaveState(status="disabled", message="No sample-quality frames captured")

        sample_quality_sequence = self.sampler.build_sequence_from_frames(
            stream_type=guard_result.streamType or (readiness_outcome.streamType if readiness_outcome is not None else "flv"),
            stream_url=guard_result.streamUrl or (readiness_outcome.streamUrl if readiness_outcome is not None else ""),
            frames=guard_result.observedFrames,
            timestamps_ms=guard_result.observedTimestampsMs or [],
            configured_sample_fps=float(effective_config.sampleFps),
            configured_duration_ms=effective_config.sampleQualityTimeoutMs,
            target_frame_count=len(guard_result.observedFrames),
        )
        return self.replay_store.persist_async(
            target=target,
            sequence=sample_quality_sequence,
            config_path=config_path,
            effective_config=effective_config,
            extra_metadata={
                "executionResult": execution_result,
                "visualState": "undetermined",
                "visualReadinessPassed": visual_readiness.ready if visual_readiness is not None else None,
                "visualReadinessReason": visual_readiness.reason if visual_readiness is not None else None,
                "visualReadiness": visual_readiness.model_dump() if visual_readiness is not None else None,
                "streamStartupFreshness": (
                    stream_startup_freshness.model_dump() if stream_startup_freshness is not None else None
                ),
                "sceneModeStability": (
                    scene_mode_stability.model_dump() if scene_mode_stability is not None else None
                ),
                **self._scene_mode_stability_metadata(scene_mode_stability),
                "sampleQualityPassed": False,
                "sampleQualityReason": guard_result.metrics.reason,
                "sampleQuality": guard_result.metrics.model_dump(),
                "sampleQualityRejectSharpnessCount": guard_result.metrics.rejectSharpnessCount,
                "sampleQualityRejectClearCellRatioCount": guard_result.metrics.rejectClearCellRatioCount,
                "sampleQualityRejectStabilityCount": guard_result.metrics.rejectStabilityCount,
                "sampleQualityFirstRejectedFrameIndex": guard_result.metrics.firstRejectedFrameIndex,
                "sampleQualityFirstRejectedElapsedMs": guard_result.metrics.firstRejectedElapsedMs,
                "sampleQualityFirstRejectedSharpness": guard_result.metrics.firstRejectedSharpness,
                "sampleQualityFirstRejectedClearCellRatio": guard_result.metrics.firstRejectedClearCellRatio,
                "sampleQualityFirstRejectedStability": guard_result.metrics.firstRejectedStability,
                "sampleQualityLastRejectedFrameIndex": guard_result.metrics.lastRejectedFrameIndex,
                "sampleQualityLastRejectedElapsedMs": guard_result.metrics.lastRejectedElapsedMs,
                "sampleQualityLastRejectedSharpness": guard_result.metrics.lastRejectedSharpness,
                "sampleQualityLastRejectedClearCellRatio": guard_result.metrics.lastRejectedClearCellRatio,
                "sampleQualityLastRejectedStability": guard_result.metrics.lastRejectedStability,
                "sampleQualityWindowMsActual": guard_result.metrics.sampleWindowMsActual,
                "sampleQualityWindowMaxAllowedMs": guard_result.metrics.sampleWindowMaxAllowedMs,
                "sampleQualityWindowTooLongRejected": guard_result.metrics.windowTooLongRejected,
                "sampleQualityWindowTooLongCandidateFrameCount": (
                    guard_result.metrics.windowTooLongCandidateFrameCount
                ),
                "sampleQualityWindowTooLongTriggerSharpness": guard_result.metrics.windowTooLongTriggerSharpness,
                "sampleQualityWindowTooLongTriggerClearCellRatio": (
                    guard_result.metrics.windowTooLongTriggerClearCellRatio
                ),
                "sampleQualityWindowTooLongTriggerStability": guard_result.metrics.windowTooLongTriggerStability,
                "streamReadFailureReason": guard_result.metrics.streamReadFailureReason,
                "streamReadFailureCount": guard_result.metrics.streamReadFailureCount,
                "sampleQualityStreamRecovered": guard_result.metrics.sampleQualityStreamRecovered,
                "sampleQualitySessionReopened": guard_result.metrics.sampleQualitySessionReopened,
                "sampleQualityStreamRetryCount": guard_result.metrics.sampleQualityStreamRetryCount,
                "visualReadinessReadyFrameIndex": (
                    readiness_outcome.readyFrameIndex if readiness_outcome is not None else None
                ),
                "visualReadinessConfirmFrameIndex": (
                    readiness_outcome.confirmFrameIndex if readiness_outcome is not None else None
                ),
                "visualReadinessStartFrameIndex": 0 if readiness_outcome is not None and readiness_outcome.frames else None,
                "sampleStartFrameIndex": None,
                "requestedSceneMode": requested_scene_mode,
                "effectiveSceneMode": effective_scene_mode,
                "effectiveSceneProfile": effective_scene_profile,
                "focusAnchorRoi": target.focusAnchorRoi.model_dump() if target.focusAnchorRoi is not None else None,
                "focusAnchorRoiSource": focus_anchor_roi_source,
                "focusAnchorRoiFallbackUsed": focus_anchor_roi_fallback_used,
                "sceneMode": effective_config.sceneMode,
                "sceneModeConfidence": scene_mode_decision.confidence if scene_mode_decision is not None else None,
                "sceneModeReason": scene_mode_decision.reason if scene_mode_decision is not None else None,
                "sceneModeDiagnostics": (
                    scene_mode_decision.diagnostics.model_dump() if scene_mode_decision is not None else None
                ),
                "twilightProfileApplied": twilight_profile_applied,
                "twilightProfileReason": twilight_profile_reason,
                "twilightBrightnessMean": twilight_brightness_mean,
                "sampleQualityMaxRecoveriesConfigured": effective_config.sampleQualityMaxRecoveries,
                "sampleQualityRecoveryCountSemantics": self._sample_quality_recovery_count_semantics(),
            },
            readiness_key_frames=self._combined_debug_key_frames(
                stream_startup_freshness_result=stream_startup_freshness_result,
                scene_mode_stability_result=scene_mode_stability_result,
                readiness_outcome=readiness_outcome,
                sample_quality_guard_result=guard_result,
            ),
        )

    def _combined_debug_key_frames(
        self,
        *,
        stream_startup_freshness_result: _StreamStartupFreshnessResult | None = None,
        scene_mode_stability_result: SceneModeStabilityResult | None = None,
        readiness_outcome: VisualReadinessOutcome | None,
        sample_quality_guard_result: _SampleQualityGuardResult | None = None,
    ) -> dict[str, np.ndarray] | None:
        key_frames = self._stream_startup_key_frames(stream_startup_freshness_result) or {}
        key_frames.update(self._scene_mode_stability_key_frames(scene_mode_stability_result) or {})
        key_frames.update(self._readiness_key_frames(readiness_outcome) or {})
        if sample_quality_guard_result is not None:
            key_frames.update(self._sample_quality_key_frames(sample_quality_guard_result))
        return key_frames or None

    @staticmethod
    def _stream_startup_key_frames(
        freshness_result: _StreamStartupFreshnessResult | None,
    ) -> dict[str, np.ndarray] | None:
        if freshness_result is None:
            return None
        key_frames: dict[str, np.ndarray] = {}
        if freshness_result.startFrame is not None:
            key_frames["streamStartupStartFrame"] = freshness_result.startFrame
        if freshness_result.settledFrame is not None:
            key_frames["streamStartupSettledFrame"] = freshness_result.settledFrame
        return key_frames or None

    @staticmethod
    def _scene_mode_stability_key_frames(
        stability_result: SceneModeStabilityResult | None,
    ) -> dict[str, np.ndarray] | None:
        if stability_result is None:
            return None
        key_frames: dict[str, np.ndarray] = {}
        if stability_result.startFrame is not None:
            key_frames["sceneModeStabilityStartFrame"] = stability_result.startFrame
        if stability_result.settledFrame is not None:
            key_frames["sceneModeStabilitySettledFrame"] = stability_result.settledFrame
        return key_frames or None

    @staticmethod
    def _sample_quality_key_frames(
        guard_result: _SampleQualityGuardResult | None,
    ) -> dict[str, np.ndarray]:
        if guard_result is None:
            return {}
        key_frames: dict[str, np.ndarray] = {}
        if guard_result.attemptStartFrame is not None:
            key_frames["sampleQualityAttemptStartFrame"] = guard_result.attemptStartFrame
        if guard_result.degradedFrame is not None:
            key_frames["sampleQualityDegradedFrame"] = guard_result.degradedFrame
        if guard_result.lastQualifiedFrame is not None:
            key_frames["sampleQualityLastQualifiedFrame"] = guard_result.lastQualifiedFrame
        if guard_result.acceptedMiddleFrame is not None:
            key_frames["sampleQualityAcceptedMiddleFrame"] = guard_result.acceptedMiddleFrame
        if guard_result.acceptedEndFrame is not None:
            key_frames["sampleQualityAcceptedEndFrame"] = guard_result.acceptedEndFrame
        return key_frames

    @staticmethod
    def _stream_startup_freshness_metrics(
        freshness_result: _StreamStartupFreshnessResult | None,
    ) -> StreamStartupFreshnessMetrics | None:
        if freshness_result is None:
            return None
        return StreamStartupFreshnessMetrics(
            enabled=freshness_result.enabled,
            consumedFrames=freshness_result.consumedFrames,
            elapsedMs=freshness_result.elapsedMs,
            jumpDetected=freshness_result.jumpDetected,
            stableAfterJump=freshness_result.stableAfterJump,
            exitReason=freshness_result.exitReason,
            streamReadFailureReason=freshness_result.streamReadFailureReason,
            streamReadFailureCount=freshness_result.streamReadFailureCount,
            streamReadCallElapsedMs=freshness_result.streamReadCallElapsedMs,
        )

    def _guard_stream_startup_freshness(self, session: object) -> _StreamStartupFreshnessResult:
        if not self.global_config.streamStartupFreshnessEnabled:
            return _StreamStartupFreshnessResult(
                enabled=False,
                consumedFrames=0,
                jumpDetected=False,
                stableAfterJump=False,
                startFrame=None,
                settledFrame=None,
                elapsedMs=0,
                exitReason="disabled",
            )

        checker = self.visual_readiness_checker
        started_at = monotonic()
        deadline = started_at + self.global_config.streamStartupFreshnessTimeoutMs / 1000
        previous_gray: np.ndarray | None = None
        start_frame: np.ndarray | None = None
        settled_frame: np.ndarray | None = None
        consumed_frames = 0
        jump_detected = False
        stable_after_jump = False
        stable_count = 0

        while monotonic() < deadline:
            frame_result = session.read_frame_until(deadline)
            if frame_result is None:
                break
            frame, _ = frame_result
            grayscale = 0.114 * frame[..., 0] + 0.587 * frame[..., 1] + 0.299 * frame[..., 2]
            cropped = checker._center_crop(grayscale, self.global_config.streamStartupFreshnessCropRatio)
            processed = checker._downsample(cropped, self.global_config.streamStartupFreshnessDownsampleWidth)
            if start_frame is None:
                start_frame = frame.copy()
            settled_frame = frame.copy()
            consumed_frames += 1

            if previous_gray is None:
                previous_gray = processed
                continue

            delta = checker._frame_delta(previous_gray, processed)
            previous_gray = processed
            if not jump_detected:
                if delta >= self.global_config.streamStartupFreshnessJumpThreshold:
                    jump_detected = True
                    stable_count = 0
            else:
                if delta <= self.global_config.streamStartupFreshnessStableThreshold:
                    stable_count += 1
                    if stable_count >= self.global_config.streamStartupFreshnessStableFrames:
                        stable_after_jump = True
                        break
                else:
                    stable_count = 0

        elapsed_ms = max(1, int(round((monotonic() - started_at) * 1000))) if consumed_frames > 0 else 0
        if consumed_frames == 0:
            exit_reason = "no_frames"
        elif jump_detected and stable_after_jump:
            exit_reason = "jump_and_stable"
        elif jump_detected:
            exit_reason = "timeout_after_jump_no_stable"
        else:
            exit_reason = "timeout_no_jump"

        logger.info(
            (
                "Stream startup freshness guard consumed=%s elapsedMs=%s jumpDetected=%s "
                "stableAfterJump=%s exitReason=%s timeoutMs=%s"
            ),
            consumed_frames,
            elapsed_ms,
            jump_detected,
            stable_after_jump,
            exit_reason,
            self.global_config.streamStartupFreshnessTimeoutMs,
        )
        return _StreamStartupFreshnessResult(
            enabled=True,
            consumedFrames=consumed_frames,
            jumpDetected=jump_detected,
            stableAfterJump=stable_after_jump,
            startFrame=start_frame,
            settledFrame=settled_frame,
            elapsedMs=elapsed_ms,
            exitReason=exit_reason,
            streamReadFailureReason=self._stream_read_failure_reason(session),
            streamReadFailureCount=self._stream_read_failure_count(session),
            streamReadCallElapsedMs=self._stream_read_call_elapsed_ms(session),
        )

    def _reopen_pre_readiness_session(
        self,
        session: object,
        target: RecognitionTarget,
    ) -> tuple[object | None, str | None]:
        try:
            session.release()
        except Exception:
            logger.debug("Ignoring FLV session release failure during pre-readiness reopen", exc_info=True)
        try:
            reopened = self.sampler.open_session(device_id=target.deviceId, channel_id=target.channelId)
        except FlvSamplerError as error:
            logger.warning("Pre-readiness FLV reopen failed for %s/%s: %s", target.deviceId, target.channelId, error)
            return None, str(error)
        except Exception as error:
            logger.warning("Unexpected pre-readiness FLV reopen failure for %s/%s: %s", target.deviceId, target.channelId, error)
            return None, f"Unexpected FLV reopen error: {error}"
        logger.info("Pre-readiness FLV session reopened for %s/%s", target.deviceId, target.channelId)
        return reopened, None

    @staticmethod
    def _stream_read_failure_reason(session: object) -> str | None:
        reason = getattr(session, "lastReadFailureReason", None)
        return reason if reason in {"stream_read_timeout", "stream_eof", "stream_read_failed"} else None

    @staticmethod
    def _stream_read_failure_count(session: object) -> int:
        return max(0, int(getattr(session, "lastReadFailureCount", 0) or 0))

    @staticmethod
    def _stream_read_call_elapsed_ms(session: object) -> int:
        return max(0, int(getattr(session, "lastReadCallElapsedMs", 0) or 0))

    @staticmethod
    def _stream_failure_execution_result(reason: str | None) -> ExecutionResult:
        return "stream_read_timeout" if reason == "stream_read_timeout" else "stream_failed"

    def _config_for_scene_mode(self, scene_mode: ResolvedSceneMode) -> RecognitionGlobalConfig:
        return build_recognition_config(self.raw_config, scene_mode)

    @staticmethod
    def _scene_mode_stability_metrics(
        stability_result: SceneModeStabilityResult | None,
    ) -> SceneModeStabilityMetrics | None:
        if stability_result is None:
            return None
        return SceneModeStabilityMetrics(
            enabled=stability_result.enabled,
            sceneModeInitial=stability_result.initialMode,
            sceneModeFinal=stability_result.finalMode,
            sceneModeStable=stability_result.stable,
            sceneModeStabilityElapsedMs=stability_result.elapsedMs,
            sceneModeStabilityWindowCount=stability_result.windowCount,
            sceneModeTransitionObserved=stability_result.transitionObserved,
            sceneModeRelockCount=stability_result.relockCount,
            sceneModeRelockReason=stability_result.relockReason,
            sceneModeTransitionTimeout=stability_result.transitionTimeout,
        )

    @staticmethod
    def _scene_mode_stability_metadata(
        scene_mode_stability: SceneModeStabilityMetrics | None,
    ) -> dict[str, object]:
        if scene_mode_stability is None:
            return {
                "sceneModeInitial": None,
                "sceneModeFinal": None,
                "sceneModeStable": None,
                "sceneModeStabilityElapsedMs": None,
                "sceneModeStabilityWindowCount": None,
                "sceneModeTransitionObserved": None,
                "sceneModeRelockCount": None,
                "sceneModeRelockReason": None,
                "sceneModeTransitionTimeout": None,
            }
        return scene_mode_stability.model_dump()

    def _resolve_visual_readiness_context(
        self,
        session: object,
        *,
        relock_count: int = 0,
        relock_reason: str | None = None,
    ) -> _VisualReadinessContext:
        requested_scene_mode = self.global_config.sceneMode
        resolved_scene_mode = self._resolved_scene_mode_or_none(requested_scene_mode)
        if resolved_scene_mode is not None:
            profile_selection = self._resolve_scene_profile(resolved_scene_mode, None)
            return _VisualReadinessContext(
                session=session,
                effectiveConfig=profile_selection.effectiveConfig,
                sceneModeDecision=None,
                effectiveSceneMode=resolved_scene_mode,
                effectiveSceneProfile=profile_selection.effectiveSceneProfile,
                twilightProfileApplied=profile_selection.twilightProfileApplied,
                twilightProfileReason=profile_selection.twilightProfileReason,
                twilightBrightnessMean=profile_selection.twilightBrightnessMean,
                sceneModeStabilityResult=None,
            )

        stability_result = self.scene_mode_stability_guard.observe(
            session,
            relock_count=relock_count,
            relock_reason=relock_reason,
        )
        if not stability_result.enabled:
            scene_mode_decision = self._probe_scene_mode_once(session)
            if scene_mode_decision is None:
                logger.warning("Scene mode stability disabled and single scene probe produced no frames before readiness.")
                return _VisualReadinessContext(
                    session=session,
                    effectiveConfig=None,
                    sceneModeDecision=None,
                    effectiveSceneMode=None,
                    effectiveSceneProfile=None,
                    twilightProfileApplied=None,
                    twilightProfileReason="scene_mode_probe_empty",
                    twilightBrightnessMean=None,
                    sceneModeStabilityResult=stability_result,
                )
            effective_scene_mode = scene_mode_decision.suggestedMode
            profile_selection = self._resolve_scene_profile(effective_scene_mode, scene_mode_decision)
            logger.info(
                (
                    "Scene mode stability disabled; falling back to single scene probe mode=%s "
                    "classification=%s confidence=%.3f reason=%s effectiveSceneProfile=%s"
                ),
                effective_scene_mode,
                scene_mode_decision.classification,
                scene_mode_decision.confidence,
                scene_mode_decision.reason,
                profile_selection.effectiveSceneProfile,
            )
            return _VisualReadinessContext(
                session=session,
                effectiveConfig=profile_selection.effectiveConfig,
                sceneModeDecision=scene_mode_decision,
                effectiveSceneMode=effective_scene_mode,
                effectiveSceneProfile=profile_selection.effectiveSceneProfile,
                twilightProfileApplied=profile_selection.twilightProfileApplied,
                twilightProfileReason=profile_selection.twilightProfileReason,
                twilightBrightnessMean=profile_selection.twilightBrightnessMean,
                sceneModeStabilityResult=stability_result,
            )
        if not stability_result.stable or stability_result.finalDecision is None or stability_result.finalMode is None:
            logger.warning(
                (
                    "Scene mode stability did not settle before readiness: stable=%s reason=%s "
                    "initial=%s final=%s windows=%s elapsedMs=%s"
                ),
                stability_result.stable,
                stability_result.reason,
                stability_result.initialMode,
                stability_result.finalMode,
                stability_result.windowCount,
                stability_result.elapsedMs,
            )
            return _VisualReadinessContext(
                session=session,
                effectiveConfig=None,
                sceneModeDecision=stability_result.finalDecision,
                effectiveSceneMode=stability_result.finalMode,
                effectiveSceneProfile=None,
                twilightProfileApplied=None,
                twilightProfileReason=stability_result.reason,
                twilightBrightnessMean=None,
                sceneModeStabilityResult=stability_result,
            )

        scene_mode_decision = stability_result.finalDecision
        effective_scene_mode = stability_result.finalMode
        profile_selection = self._resolve_scene_profile(effective_scene_mode, scene_mode_decision)
        logger.info(
            (
                "Scene mode stability settled on %s classification=%s confidence=%.3f "
                "reason=%s windows=%s elapsedMs=%s effectiveSceneProfile=%s twilightApplied=%s twilightReason=%s"
            ),
            effective_scene_mode,
            scene_mode_decision.classification,
            scene_mode_decision.confidence,
            scene_mode_decision.reason,
            stability_result.windowCount,
            stability_result.elapsedMs,
            profile_selection.effectiveSceneProfile,
            profile_selection.twilightProfileApplied,
            profile_selection.twilightProfileReason,
        )
        return _VisualReadinessContext(
            session=session,
            effectiveConfig=profile_selection.effectiveConfig,
            sceneModeDecision=scene_mode_decision,
            effectiveSceneMode=effective_scene_mode,
            effectiveSceneProfile=profile_selection.effectiveSceneProfile,
            twilightProfileApplied=profile_selection.twilightProfileApplied,
            twilightProfileReason=profile_selection.twilightProfileReason,
            twilightBrightnessMean=profile_selection.twilightBrightnessMean,
            sceneModeStabilityResult=stability_result,
        )

    def _probe_scene_mode_once(self, session: object) -> SceneModeDecision | None:
        deadline = monotonic() + (self.global_config.sceneModeStabilityTimeoutMs / 1000.0)
        probe_frames: list[np.ndarray] = []
        while len(probe_frames) < self.global_config.sceneAutoFrameCount and monotonic() < deadline:
            frame_result = session.read_frame_until(deadline)
            if frame_result is None:
                break
            frame, _ = frame_result
            probe_frames.append(frame.copy())
        if not probe_frames:
            return None
        return self.scene_mode_resolver.resolve_frames(probe_frames)

    def _resolve_scene_profile(
        self,
        scene_mode: ResolvedSceneMode,
        scene_mode_decision: SceneModeDecision | None,
    ) -> _SceneProfileSelection:
        base_config = self._config_for_scene_mode(scene_mode)
        diagnostics = scene_mode_decision.diagnostics if scene_mode_decision is not None else None
        brightness_mean = diagnostics.brightnessMean if diagnostics is not None else None
        if scene_mode != "day_visible":
            return _SceneProfileSelection(
                effectiveConfig=base_config,
                effectiveSceneProfile="night_ir",
                twilightProfileApplied=False,
                twilightProfileReason="effective_scene_mode_is_night_ir",
                twilightBrightnessMean=brightness_mean,
            )

        twilight_applied, twilight_reason = self._should_apply_day_visible_twilight(diagnostics)
        if not twilight_applied:
            return _SceneProfileSelection(
                effectiveConfig=base_config,
                effectiveSceneProfile="day_visible_normal",
                twilightProfileApplied=False,
                twilightProfileReason=twilight_reason,
                twilightBrightnessMean=brightness_mean,
            )

        return _SceneProfileSelection(
            effectiveConfig=self._apply_day_visible_twilight_overrides(base_config),
            effectiveSceneProfile="day_visible_twilight",
            twilightProfileApplied=True,
            twilightProfileReason=twilight_reason,
            twilightBrightnessMean=brightness_mean,
        )

    def _apply_day_visible_twilight_overrides(self, base_config: RecognitionGlobalConfig) -> RecognitionGlobalConfig:
        twilight_overrides = self._day_visible_twilight_overrides()
        if not twilight_overrides:
            return base_config
        payload = base_config.snapshot()
        payload.update(twilight_overrides)
        return RecognitionGlobalConfig.model_validate(payload)

    def _day_visible_twilight_overrides(self) -> dict[str, object]:
        day_visible_profile = self.raw_config.get("dayVisible")
        if not isinstance(day_visible_profile, dict):
            return {}
        twilight_profile = day_visible_profile.get("twilight")
        if not isinstance(twilight_profile, dict):
            return {}
        return deepcopy(twilight_profile)

    def _should_apply_day_visible_twilight(
        self,
        diagnostics: object | None,
    ) -> tuple[bool, str]:
        if diagnostics is None:
            return False, "scene_mode_diagnostics_unavailable"

        brightness_mean = float(diagnostics.brightnessMean)
        colorfulness_mean = float(diagnostics.colorfulnessMean)
        saturation_p90 = float(diagnostics.saturationP90)
        day_visible_score = float(diagnostics.dayVisibleScore)
        night_ir_score = float(diagnostics.nightIrScore)
        score_margin = float(diagnostics.scoreMargin)

        max_twilight_brightness = 105.0
        min_twilight_colorfulness = max(self.global_config.sceneAutoMinColorfulness * 0.8, 8.0)
        min_twilight_saturation = max(self.global_config.sceneAutoMinSaturationP90 * 0.85, 0.05)
        min_day_visible_score = max(self.global_config.sceneAutoConfidenceThreshold + 0.08, 0.78)
        max_night_ir_score = 0.42
        min_score_margin = 0.14

        if brightness_mean > max_twilight_brightness:
            return False, "brightness_above_twilight_band"
        if colorfulness_mean < min_twilight_colorfulness:
            return False, "colorfulness_too_low_for_day_visible_twilight"
        if saturation_p90 < min_twilight_saturation:
            return False, "saturation_too_low_for_day_visible_twilight"
        if day_visible_score < min_day_visible_score:
            return False, "day_visible_score_not_high_enough_for_twilight"
        if night_ir_score > max_night_ir_score:
            return False, "night_ir_score_too_high_for_twilight"
        if score_margin < min_score_margin:
            return False, "scene_score_margin_too_small_for_twilight"
        return True, "brightness_low_but_day_visible_signals_remain_strong"

    @staticmethod
    def _has_scene_profiles(raw_config: dict[str, object]) -> bool:
        return isinstance(raw_config.get("dayVisible"), dict) and isinstance(raw_config.get("nightIr"), dict)

    @staticmethod
    def _synthesize_raw_config(global_config: RecognitionGlobalConfig) -> dict[str, object]:
        snapshot = global_config.snapshot()
        return {
            **snapshot,
            "dayVisible": {},
            "nightIr": {},
        }

    def _run_detection_pass(
        self,
        *,
        sequence: SampledSequence,
        target: RecognitionTarget,
        effective_config: RecognitionGlobalConfig,
    ) -> _DetectionPassResult:
        aligner = FullFrameAligner(effective_config)
        aligned_sequence = aligner.align(sequence)
        base_pass = self._evaluate_detection_roi(
            sequence=sequence,
            aligned_sequence=aligned_sequence,
            roi=target.roi,
            effective_config=effective_config,
        )
        if effective_config.sceneMode != "night_ir" or not effective_config.nightRoiToleranceEnabled:
            return base_pass

        candidates = generate_night_roi_candidates(
            target.roi,
            frame_width=sequence.frameWidth,
            frame_height=sequence.frameHeight,
            offset_ratios=(
                -effective_config.nightRoiToleranceOffsetRatio,
                0.0,
                effective_config.nightRoiToleranceOffsetRatio,
            ),
            scales=(1.0, effective_config.nightRoiToleranceExpandedScale),
        )
        evaluated_passes: dict[str, _DetectionPassResult] = {}
        candidate_metrics: dict[str, RoiToleranceSequenceMetrics] = {}
        base_candidate = next(candidate for candidate in candidates if candidate.isBase)
        evaluated_candidates = [base_candidate]
        if base_pass.voteDecision.visualState != "has_splash":
            evaluated_candidates, _ = prefilter_night_roi_candidates(
                candidates,
                aligned_sequence.alignedFrames,
                effective_config,
                max_full_candidates=effective_config.nightRoiToleranceMaxFullCandidates,
            )
        for candidate in evaluated_candidates:
            candidate_pass = (
                base_pass
                if candidate.isBase
                else self._evaluate_detection_roi(
                    sequence=sequence,
                    aligned_sequence=aligned_sequence,
                    roi=candidate.roi,
                    effective_config=effective_config,
                )
            )
            evaluated_passes[candidate.key] = candidate_pass
            candidate_metrics[candidate.key] = RoiToleranceSequenceMetrics(
                framePassCount=candidate_pass.scoreSummary.framePassCount or 0,
                hardGatePassCount=candidate_pass.scoreSummary.hardGatePassCount or 0,
                weightedFrameScoreMean=candidate_pass.scoreSummary.weightedFrameScoreMean or 0.0,
                dynamicEvidencePassCount=candidate_pass.scoreSummary.dynamicEvidencePassCount or 0,
            )

        selected_candidate = select_sequence_candidate(candidates, candidate_metrics)
        selected_pass = evaluated_passes[selected_candidate.key]
        base_metrics = candidate_metrics[base_candidate.key]
        selected_metrics = candidate_metrics[selected_candidate.key]
        selected_pass.roiTolerance = _RoiToleranceSelection(
            enabled=True,
            candidateCount=len(candidates),
            evaluatedCandidateCount=len(evaluated_candidates),
            selectedCandidate=selected_candidate,
            baseFramePassCount=base_metrics.framePassCount,
            selectedFramePassCount=selected_metrics.framePassCount,
            rescued=(
                not selected_candidate.isBase
                and selected_pass.voteDecision.visualState == "has_splash"
                and base_pass.voteDecision.visualState != "has_splash"
            ),
            candidates=candidates,
            candidateMetrics=candidate_metrics,
        )
        return selected_pass

    def _evaluate_detection_roi(
        self,
        *,
        sequence: SampledSequence,
        aligned_sequence: AlignedSequence,
        roi: RoiModel,
        effective_config: RecognitionGlobalConfig,
    ) -> _DetectionPassResult:
        feature_extractor = FrameFeatureExtractor(effective_config)
        frame_scorer = WeightedFrameScorer(effective_config)
        vote_resolver = TemporalVoteResolver(effective_config)
        frame_features = feature_extractor.extract(aligned_sequence.alignedFrames, roi)
        frame_scores = frame_scorer.score(frame_features)
        pre_alignment_roi_motion = mean_roi_motion(sequence.frames, roi)
        post_alignment_roi_motion = mean_roi_motion(aligned_sequence.alignedFrames, roi)
        representative_index = max(frame_scores, key=lambda item: item.weightedScore).frameIndex if frame_scores else None
        score_summary = self._score_summary(
            effective_config=effective_config,
            sequence=sequence,
            aligned_sequence=aligned_sequence,
            frame_features=frame_features,
            frame_scores=frame_scores,
            pre_alignment_roi_motion=pre_alignment_roi_motion,
            post_alignment_roi_motion=post_alignment_roi_motion,
        )
        vote_decision = vote_resolver.resolve(score_summary)
        score_summary.framePassRatio = vote_decision.passRatio
        score_summary.overflowFrameRatio = vote_decision.overflowFrameRatio
        score_summary.motionReductionRatio = vote_decision.motionReductionRatio
        score_summary.reliabilityGateTriggered = vote_decision.reliabilityGateTriggered
        score_summary.temporalVoteReason = vote_decision.reason
        score_summary.staticBrightInterferenceSuppressed = vote_decision.staticBrightInterferenceSuppressed
        return _DetectionPassResult(
            effectiveConfig=effective_config,
            scoreSummary=score_summary,
            voteDecision=vote_decision,
            representativeIndex=representative_index,
            detectionRoi=roi,
        )

    def _score_summary(
        self,
        effective_config: RecognitionGlobalConfig | None = None,
        sequence: SampledSequence | None = None,
        aligned_sequence: AlignedSequence | None = None,
        frame_features: list[FrameFeature] | None = None,
        frame_scores: list[FrameScore] | None = None,
        pre_alignment_roi_motion: float | None = None,
        post_alignment_roi_motion: float | None = None,
    ) -> RecognitionScoreSummary:
        effective_config = effective_config or self.global_config
        frame_features = frame_features or []
        frame_scores = frame_scores or []
        score_scene_mode = self._resolved_scene_mode_or_none(effective_config.sceneMode)
        raw_shifts = aligned_sequence.globalShifts if aligned_sequence else []
        shift_x_values = [item[0] for item in raw_shifts]
        shift_y_values = [item[1] for item in raw_shifts]
        overflow_flags = aligned_sequence.overflowFlags if aligned_sequence else []
        hard_gate_pass_count = sum(1 for item in frame_scores if item.hardGatePassed) if frame_scores else None
        hard_gate_pass_ratio = (hard_gate_pass_count / len(frame_scores)) if frame_scores else None
        any_hard_gate_passed = (hard_gate_pass_count > 0) if hard_gate_pass_count is not None else None
        dynamic_evidence_pass_count = sum(1 for item in frame_scores if item.dynamicEvidencePassed) if frame_scores else None
        largest_bright_component_pass_count = (
            sum(
                1
                for item in frame_features
                if item.largestBrightComponentRatio >= effective_config.hardGateMinLargestBrightComponentRatio
            )
            if frame_features
            else None
        )
        center_bright_coverage_pass_count = (
            sum(
                1
                for item in frame_features
                if item.centerBrightCoverage >= effective_config.hardGateMinCenterBrightCoverage
            )
            if frame_features
            else None
        )
        vertical_spread_pass_count = (
            sum(
                1
                for item in frame_features
                if item.verticalSpreadRatio >= effective_config.hardGateMinVerticalSpreadRatio
            )
            if frame_features
            else None
        )
        continuous_bright_pass_count = (
            sum(
                1
                for item in frame_features
                if max(0.0, 1.0 - item.fragmentationScore) >= effective_config.hardGateMinContinuousBrightRatio
            )
            if frame_features
            else None
        )
        gap_fill_pass_count = (
            sum(1 for item in frame_features if item.gapFillRatio >= effective_config.hardGateMinGapFillRatio)
            if frame_features
            else None
        )
        sequence_hard_gate_passed = (
            hard_gate_pass_ratio >= effective_config.sequenceVoteThreshold
            if hard_gate_pass_ratio is not None
            else None
        )
        return RecognitionScoreSummary(
            sceneMode=score_scene_mode,
            brightThresholdMean=mean([item.brightThreshold for item in frame_features]) if frame_features else None,
            roiBrightnessQ99Mean=mean([item.roiBrightnessQ99 for item in frame_features]) if frame_features else None,
            roiBrightnessMaxMean=mean([item.roiBrightnessMax for item in frame_features]) if frame_features else None,
            localMotionScore=mean([item.localMotionComponent for item in frame_scores]) if frame_scores else None,
            dynamicAreaScore=mean([item.dynamicAreaComponent for item in frame_scores]) if frame_scores else None,
            dynamicAreaRatio=mean([item.dynamicAreaRatio for item in frame_features]) if frame_features else None,
            highlightMotionScore=mean([item.highlightMotionComponent for item in frame_scores]) if frame_scores else None,
            largestBrightComponentRatio=(
                mean([item.largestBrightComponentRatio for item in frame_features]) if frame_features else None
            ),
            brightComponentCount=mean([item.brightComponentCount for item in frame_features]) if frame_features else None,
            fragmentationScore=mean([item.fragmentationScore for item in frame_features]) if frame_features else None,
            centerBrightCoverage=mean([item.centerBrightCoverage for item in frame_features]) if frame_features else None,
            upperHalfBrightRatio=mean([item.upperHalfBrightRatio for item in frame_features]) if frame_features else None,
            lowerHalfBrightRatio=mean([item.lowerHalfBrightRatio for item in frame_features]) if frame_features else None,
            verticalSpreadRatio=mean([item.verticalSpreadRatio for item in frame_features]) if frame_features else None,
            gapFillRatio=mean([item.gapFillRatio for item in frame_features]) if frame_features else None,
            temporalAreaVariance=mean([item.temporalAreaVariance for item in frame_features]) if frame_features else None,
            temporalShapeVariance=(
                mean([item.temporalShapeVariance for item in frame_features]) if frame_features else None
            ),
            anyHardGatePassed=any_hard_gate_passed,
            hardGatePassed=sequence_hard_gate_passed,
            hardGatePassRatio=hard_gate_pass_ratio,
            hardGatePassCount=hard_gate_pass_count,
            dynamicEvidencePassCount=dynamic_evidence_pass_count,
            largestBrightComponentPassCount=largest_bright_component_pass_count,
            centerBrightCoveragePassCount=center_bright_coverage_pass_count,
            verticalSpreadPassCount=vertical_spread_pass_count,
            continuousBrightPassCount=continuous_bright_pass_count,
            gapFillPassCount=gap_fill_pass_count,
            hardGateMinGapFillRatioConfigured=effective_config.hardGateMinGapFillRatio,
            framePassRatio=(sum(1 for item in frame_scores if item.framePass) / len(frame_scores)) if frame_scores else None,
            framePassCount=sum(1 for item in frame_scores if item.framePass) if frame_scores else None,
            sampledFrameCount=sequence.sampledFrameCount if sequence else None,
            targetFrameCount=sequence.targetFrameCount if sequence else self.global_config.sequenceFrameCount,
            configuredSampleFps=sequence.configuredSampleFps if sequence else float(self.global_config.sampleFps),
            actualSampleFps=sequence.actualSampleFps if sequence else None,
            configuredSampleDurationMs=sequence.configuredSampleDurationMs if sequence else self.global_config.sampleDurationMs,
            actualSampleDurationMs=sequence.actualSampleDurationMs if sequence else None,
            streamType=sequence.streamType if sequence else "flv",
            alignmentApplied=aligned_sequence.alignmentApplied if aligned_sequence else None,
            globalMotionExceeded=any(overflow_flags) if overflow_flags else False,
            overflowFrameCount=sum(1 for flag in overflow_flags if flag) if overflow_flags else 0,
            meanGlobalShiftX=mean(shift_x_values) if shift_x_values else None,
            meanGlobalShiftY=mean(shift_y_values) if shift_y_values else None,
            maxGlobalShiftMagnitude=max(aligned_sequence.shiftMagnitudes) if aligned_sequence and aligned_sequence.shiftMagnitudes else None,
            maxAppliedShiftMagnitude=max(aligned_sequence.appliedShiftMagnitudes) if aligned_sequence and aligned_sequence.appliedShiftMagnitudes else None,
            preAlignmentRoiMotionMean=pre_alignment_roi_motion,
            postAlignmentRoiMotionMean=post_alignment_roi_motion,
            localMotionMean=mean([item.localResidualMotion for item in frame_features]) if frame_features else None,
            localMotionMax=max([item.localResidualMotion for item in frame_features]) if frame_features else None,
            dynamicAreaMean=mean([item.dynamicAreaRatio for item in frame_features]) if frame_features else None,
            dynamicAreaMax=max([item.dynamicAreaRatio for item in frame_features]) if frame_features else None,
            highlightMotionMean=mean([item.highlightDisturbance for item in frame_features]) if frame_features else None,
            highlightMotionMax=max([item.highlightDisturbance for item in frame_features]) if frame_features else None,
            weightedFrameScoreMean=mean([item.weightedScore for item in frame_scores]) if frame_scores else None,
            weightedFrameScoreMax=max([item.weightedScore for item in frame_scores]) if frame_scores else None,
            configuredFrameCount=effective_config.sequenceFrameCount,
            framePassThreshold=effective_config.framePassThreshold,
            sequenceVoteThreshold=effective_config.sequenceVoteThreshold,
        )

    def _persist_visual_readiness_replay(
        self,
        *,
        target: RecognitionTarget,
        readiness_outcome: VisualReadinessOutcome,
        config_path: str,
        execution_result: ExecutionResult,
        effective_config: RecognitionGlobalConfig,
        requested_scene_mode: str,
        effective_scene_mode: ResolvedSceneMode | None,
        scene_mode_decision: SceneModeDecision | None = None,
        effective_scene_profile: EffectiveSceneProfile | None = None,
        twilight_profile_applied: bool | None = None,
        twilight_profile_reason: str | None = None,
        twilight_brightness_mean: float | None = None,
        focus_anchor_roi_fallback_used: bool | None = None,
        focus_anchor_roi_source: str | None = None,
        stream_startup_freshness: StreamStartupFreshnessMetrics | None = None,
        stream_startup_freshness_result: _StreamStartupFreshnessResult | None = None,
        scene_mode_stability: SceneModeStabilityMetrics | None = None,
        scene_mode_stability_result: SceneModeStabilityResult | None = None,
    ) -> tuple[dict[str, str], ReplaySaveState]:
        if len(readiness_outcome.frames) == 0:
            return {}, ReplaySaveState(status="disabled", message="No visual-readiness frames captured")

        readiness_sequence = self.sampler.build_sequence_from_frames(
            stream_type=readiness_outcome.streamType,
            stream_url=readiness_outcome.streamUrl,
            frames=readiness_outcome.frames,
            timestamps_ms=readiness_outcome.frameTimestampsMs,
            configured_sample_fps=float(effective_config.sampleFps),
            configured_duration_ms=effective_config.visualReadinessTimeoutMs,
            target_frame_count=len(readiness_outcome.frames),
        )
        return self.replay_store.persist_async(
            target=target,
            sequence=readiness_sequence,
            config_path=config_path,
            effective_config=effective_config,
            extra_metadata={
                "executionResult": execution_result,
                "visualState": "undetermined",
                "visualReadinessPassed": False,
                "visualReadinessReason": readiness_outcome.metrics.reason,
                "visualReadiness": readiness_outcome.metrics.model_dump(),
                "streamStartupFreshness": (
                    stream_startup_freshness.model_dump() if stream_startup_freshness is not None else None
                ),
                "sceneModeStability": (
                    scene_mode_stability.model_dump() if scene_mode_stability is not None else None
                ),
                **self._scene_mode_stability_metadata(scene_mode_stability),
                "visualReadinessReadyFrameIndex": readiness_outcome.readyFrameIndex,
                "visualReadinessConfirmFrameIndex": readiness_outcome.confirmFrameIndex,
                "visualReadinessStartFrameIndex": 0 if readiness_outcome.frames else None,
                "sampleStartFrameIndex": None,
                "requestedSceneMode": requested_scene_mode,
                "effectiveSceneMode": effective_scene_mode,
                "effectiveSceneProfile": effective_scene_profile,
                "focusAnchorRoi": target.focusAnchorRoi.model_dump() if target.focusAnchorRoi is not None else None,
                "focusAnchorRoiSource": focus_anchor_roi_source,
                "focusAnchorRoiFallbackUsed": focus_anchor_roi_fallback_used,
                "sceneMode": effective_config.sceneMode,
                "sceneModeConfidence": scene_mode_decision.confidence if scene_mode_decision is not None else None,
                "sceneModeReason": scene_mode_decision.reason if scene_mode_decision is not None else None,
                "sceneModeDiagnostics": (
                    scene_mode_decision.diagnostics.model_dump() if scene_mode_decision is not None else None
                ),
                "twilightProfileApplied": twilight_profile_applied,
                "twilightProfileReason": twilight_profile_reason,
                "twilightBrightnessMean": twilight_brightness_mean,
                "sampleQualityMaxRecoveriesConfigured": effective_config.sampleQualityMaxRecoveries,
                "sampleQualityRecoveryCountSemantics": self._sample_quality_recovery_count_semantics(),
            },
            readiness_key_frames=self._combined_debug_key_frames(
                stream_startup_freshness_result=stream_startup_freshness_result,
                scene_mode_stability_result=scene_mode_stability_result,
                readiness_outcome=readiness_outcome,
            ),
        )

    @staticmethod
    def _visual_not_ready_execution_result(reason: str) -> ExecutionResult:
        if reason in {
            "visual_not_ready_timeout",
            "visual_not_ready_ready_window_short",
            "visual_not_ready_min_elapsed",
            "visual_not_ready_min_observe",
            "visual_post_ready_recheck_timeout",
        }:
            return "visual_not_ready_timeout"
        if reason in {"visual_not_ready_blurry", "visual_not_ready_blurry_and_unstable"}:
            return "visual_blurry_before_detection"
        return "visual_not_ready"

    @staticmethod
    def _scene_mode_execution_result(reason: str | None) -> ExecutionResult:
        if reason == "scene_mode_probe_incomplete":
            return "scene_mode_probe_incomplete"
        return "scene_mode_transition_timeout"

    def _failure_result(
        self,
        *,
        config_path: Path,
        requested_preset_index: int | None,
        execution_result: ExecutionResult,
        message: str,
        timing: RecognitionTiming,
        started_at: float,
        target: RecognitionTarget | None = None,
        snapshot_path: str | None = None,
        snapshot_url: str | None = None,
        visual_state: str | None = None,
        stream_startup_freshness: StreamStartupFreshnessMetrics | None = None,
        scene_mode_stability: SceneModeStabilityMetrics | None = None,
        visual_readiness: VisualReadinessMetrics | None = None,
        sample_quality: SampleQualityMetrics | None = None,
        replay_paths: dict[str, str] | None = None,
        replay_save: ReplaySaveState | None = None,
        requested_scene_mode: str | None = None,
        effective_scene_mode: ResolvedSceneMode | None = None,
        scene_mode_confidence: float | None = None,
        scene_mode_reason: str | None = None,
        scene_mode_diagnostics: object | None = None,
        effective_scene_profile: EffectiveSceneProfile | None = None,
        twilight_profile_applied: bool | None = None,
        twilight_profile_reason: str | None = None,
        twilight_brightness_mean: float | None = None,
        focus_anchor_roi_fallback_used: bool | None = None,
        focus_anchor_roi_source: str | None = None,
        effective_config: RecognitionGlobalConfig | None = None,
        pre_readiness_session_reopened: bool = False,
        pre_readiness_stream_recovered: bool = False,
        pre_readiness_stream_retry_count: int = 0,
        stream_read_failure_reason: str | None = None,
        stream_read_failure_count: int = 0,
        stream_read_call_elapsed_ms: int = 0,
    ) -> RecognitionRunResult:
        timing.totalMs = self._elapsed_ms(started_at)
        fallback_target = target or RecognitionTarget(
            deviceId="",
            channelId="",
            presetIndex=requested_preset_index if requested_preset_index is not None else -1,
            presetName="",
            targetId="",
            targetName="",
            roi={"x": 0, "y": 0, "width": 1, "height": 1},
            focusAnchorRoi=None,
        )
        replay_paths = replay_paths or {}
        replay_save = replay_save or ReplaySaveState(status="disabled", message="Replay save not started")
        requested_scene_mode = requested_scene_mode or self.global_config.sceneMode
        default_effective_scene_mode = self._resolved_scene_mode_or_none(requested_scene_mode)
        effective_config = effective_config or self.global_config
        return RecognitionRunResult(
            executionResult=execution_result,
            visualState=visual_state,
            sceneMode=requested_scene_mode,
            requestedSceneMode=requested_scene_mode,
            effectiveSceneMode=effective_scene_mode if effective_scene_mode is not None else default_effective_scene_mode,
            effectiveSceneProfile=effective_scene_profile,
            sceneModeConfidence=(
                scene_mode_confidence
                if scene_mode_confidence is not None
                else (None if requested_scene_mode == "auto" else 1.0)
            ),
            sceneModeReason=scene_mode_reason or "scene_mode_not_resolved_due_to_execution_failure",
            sceneModeFallbackUsed=False,
            sceneModeDiagnostics=scene_mode_diagnostics,
            twilightProfileApplied=twilight_profile_applied,
            twilightProfileReason=twilight_profile_reason,
            twilightBrightnessMean=twilight_brightness_mean,
            focusAnchorRoiFallbackUsed=focus_anchor_roi_fallback_used,
            focusAnchorRoiSource=focus_anchor_roi_source,
            dayVisibleVisualState=None,
            nightIrVisualState=None,
            fallbackResolution="not_needed",
            streamStartupFreshness=stream_startup_freshness,
            sceneModeStability=scene_mode_stability,
            visualReadinessPassed=visual_readiness.ready if visual_readiness is not None else None,
            visualReadinessReason=visual_readiness.reason if visual_readiness is not None else None,
            visualReadiness=visual_readiness,
            sampleQualityPassed=sample_quality.passed if sample_quality is not None else None,
            sampleQualityReason=sample_quality.reason if sample_quality is not None else None,
            sampleQualityMaxRecoveriesConfigured=effective_config.sampleQualityMaxRecoveries,
            sampleQualityRecoveryCountSemantics=self._sample_quality_recovery_count_semantics(),
            sampleQuality=sample_quality,
            preReadinessSessionReopened=pre_readiness_session_reopened,
            preReadinessStreamRecovered=pre_readiness_stream_recovered,
            preReadinessStreamRetryCount=pre_readiness_stream_retry_count,
            streamReadFailureReason=stream_read_failure_reason,
            streamReadFailureCount=stream_read_failure_count,
            streamReadCallElapsedMs=stream_read_call_elapsed_ms,
            scoreSummary=self._score_summary(effective_config=effective_config),
            evidencePaths=RecognitionEvidencePaths(
                calibrationPath=str(config_path),
                snapshotPath=snapshot_path,
                snapshotUrl=snapshot_url,
                streamStartupStartFramePath=replay_paths.get("streamStartupStartFramePath"),
                streamStartupSettledFramePath=replay_paths.get("streamStartupSettledFramePath"),
                sceneModeStabilityStartFramePath=replay_paths.get("sceneModeStabilityStartFramePath"),
                sceneModeStabilitySettledFramePath=replay_paths.get("sceneModeStabilitySettledFramePath"),
                sceneProbeStartFramePath=replay_paths.get("sceneProbeStartFramePath"),
                sceneProbeEndFramePath=replay_paths.get("sceneProbeEndFramePath"),
                representativeFramePath=replay_paths.get("representativeFramePath"),
                visualReadinessStartFramePath=replay_paths.get("visualReadinessStartFramePath"),
                visualReadinessReadyFramePath=replay_paths.get("visualReadinessReadyFramePath"),
                visualReadinessConfirmFramePath=replay_paths.get("visualReadinessConfirmFramePath"),
                sampleStartFramePath=replay_paths.get("sampleStartFramePath"),
                sampleQualityAttemptStartFramePath=replay_paths.get("sampleQualityAttemptStartFramePath"),
                sampleQualityDegradedFramePath=replay_paths.get("sampleQualityDegradedFramePath"),
                sampleQualityLastQualifiedFramePath=replay_paths.get("sampleQualityLastQualifiedFramePath"),
                sampleQualityAcceptedMiddleFramePath=replay_paths.get("sampleQualityAcceptedMiddleFramePath"),
                sampleQualityAcceptedEndFramePath=replay_paths.get("sampleQualityAcceptedEndFramePath"),
                replaySequencePath=replay_paths.get("sequencePath"),
                replayMetadataPath=replay_paths.get("metadataPath"),
                debugImagePath=replay_paths.get("debugImagePath"),
                recognitionConfigSnapshotPath=replay_paths.get("configSnapshotPath"),
            ),
            replaySave=replay_save,
            timing=timing,
            algorithmVersion=effective_config.algorithmVersion,
            configPath=str(config_path),
            target=fallback_target,
            message=message,
        )

    @staticmethod
    def _resolved_scene_mode_or_none(scene_mode: str) -> ResolvedSceneMode | None:
        if scene_mode in {"day_visible", "night_ir"}:
            return scene_mode
        return None

    @staticmethod
    def _sample_quality_recovery_count_semantics() -> str:
        return "restart_attempts_including_budget_exhausting_restart"

    @staticmethod
    def _focus_anchor_roi(target: RecognitionTarget) -> tuple[RoiModel, str, bool]:
        if target.focusAnchorRoi is not None:
            return target.focusAnchorRoi, "focus_anchor_roi", False
        return target.roi, "roi_fallback", True

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return max(1, int(round((perf_counter() - started_at) * 1000)))

    @staticmethod
    def _readiness_key_frames(readiness_outcome: VisualReadinessOutcome | None) -> dict[str, "object"] | None:
        if readiness_outcome is None or not readiness_outcome.frames:
            return None
        readiness_start_frame = readiness_outcome.frames[0]
        readiness_ready_index = (
            readiness_outcome.readyFrameIndex
            if readiness_outcome.readyFrameIndex is not None and readiness_outcome.readyFrameIndex < len(readiness_outcome.frames)
            else None
        )
        readiness_ready_frame = (
            readiness_outcome.frames[readiness_ready_index] if readiness_ready_index is not None else None
        )
        readiness_confirm_frame = (
            readiness_outcome.frames[readiness_outcome.confirmFrameIndex]
            if readiness_outcome.confirmFrameIndex is not None
            and readiness_outcome.confirmFrameIndex < len(readiness_outcome.frames)
            else None
        )
        return {
            "visualReadinessStartFrame": readiness_start_frame,
            "visualReadinessReadyFrame": readiness_ready_frame,
            "visualReadinessConfirmFrame": readiness_confirm_frame,
        }
