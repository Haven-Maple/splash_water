from __future__ import annotations

import argparse
import json
import sys
import traceback
from collections.abc import Callable
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter, sleep
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import settings
from app.services.calibration_storage_service import storage_service
from app.utils.logging_utils import setup_logging
from inspector.config import build_recognition_config, load_recognition_raw_config, resolve_config_path
from inspector.models import RecognitionRunResult
from inspector.run_once_service import RunOnceService


VisualStateLiteral = Literal["has_splash", "no_splash", "undetermined"]
SceneModeOverrideLiteral = Literal["auto", "day_visible", "night_ir"]
RoundStatusLiteral = Literal["success", "failed"]
RunStatusLiteral = Literal["completed", "interrupted", "aborted"]
RoundTimeoutPolicy = Literal["warn_only", "fail"]


class TransitionPresetStepResult(BaseModel):
    accepted: bool = False
    presetIndex: int
    elapsedMs: int
    timedOut: bool = False
    timeoutSeconds: float
    message: str | None = None
    raw: Any = None
    attemptCount: int = 1
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    failureCategory: str | None = None
    unknownStateRetrySucceeded: bool = False


class PseudoMultiPointRoundResult(BaseModel):
    roundIndex: int
    startedAt: str
    finishedAt: str
    status: RoundStatusLiteral
    expectedVisualState: VisualStateLiteral
    expectedVisualStateMatched: bool | None = None
    actualVisualState: VisualStateLiteral | None = None
    failureStep: str | None = None
    failureReason: str | None = None
    roundElapsedMs: int
    roundTimedOut: bool = False
    roundTimeoutSeconds: float
    roundTimeoutPolicy: RoundTimeoutPolicy = "warn_only"
    timingSloExceeded: bool = False
    timingSloReason: str | None = None
    strictTimeoutFailed: bool = False
    transitionSettleMsConfigured: int
    transitionSettleWaitMsActual: int
    transitionPreset: TransitionPresetStepResult
    transitionPresetAttemptCount: int | None = None
    transitionPresetAttempts: list[dict[str, Any]] = Field(default_factory=list)
    transitionPresetFailureCategory: str | None = None
    transitionPresetUnknownStateRetrySucceeded: bool | None = None
    recognitionExecutionResult: str | None = None
    recognitionEffectiveSceneMode: str | None = None
    recognitionEffectiveSceneProfile: str | None = None
    sceneModeInitial: str | None = None
    sceneModeFinal: str | None = None
    sceneModeStable: bool | None = None
    sceneModeStabilityElapsedMs: int | None = None
    sceneModeStabilityWindowCount: int | None = None
    sceneModeTransitionObserved: bool | None = None
    sceneModeRelockCount: int | None = None
    sceneModeRelockReason: str | None = None
    sceneModeTransitionTimeout: bool | None = None
    focusAnchorRoiFallbackUsed: bool | None = None
    focusAnchorRoiSource: str | None = None
    recognitionTemporalVoteReason: str | None = None
    scoreHardGatePassCount: int | None = None
    scoreDynamicEvidencePassCount: int | None = None
    scoreLargestBrightComponentPassCount: int | None = None
    scoreCenterBrightCoveragePassCount: int | None = None
    scoreVerticalSpreadPassCount: int | None = None
    scoreContinuousBrightPassCount: int | None = None
    scoreGapFillPassCount: int | None = None
    scoreHardGateMinGapFillRatioConfigured: float | None = None
    scoreGapFillRatioMean: float | None = None
    roiToleranceEnabled: bool | None = None
    roiToleranceCandidateCount: int | None = None
    roiToleranceEvaluatedCandidateCount: int | None = None
    roiToleranceSelectedRoi: dict[str, int] | None = None
    roiToleranceSelectedOffsetXRatio: float | None = None
    roiToleranceSelectedOffsetYRatio: float | None = None
    roiToleranceSelectedScale: float | None = None
    roiToleranceBaseFramePassCount: int | None = None
    roiToleranceSelectedFramePassCount: int | None = None
    roiToleranceRescued: bool | None = None
    twilightProfileApplied: bool | None = None
    twilightProfileReason: str | None = None
    twilightBrightnessMean: float | None = None
    recognitionPresetTurnMs: int | None = None
    recognitionSettleWaitMs: int | None = None
    streamStartupFreshnessExitReason: str | None = None
    streamStartupFreshnessConsumedFrames: int | None = None
    streamStartupFreshnessElapsedMs: int | None = None
    streamStartupFreshnessJumpDetected: bool | None = None
    streamStartupFreshnessStableAfterJump: bool | None = None
    streamStartupReadFailureReason: str | None = None
    streamStartupReadFailureCount: int | None = None
    streamStartupReadCallElapsedMs: int | None = None
    preReadinessSessionReopened: bool | None = None
    preReadinessStreamRecovered: bool | None = None
    preReadinessStreamRetryCount: int | None = None
    streamReadFailureReason: str | None = None
    streamReadFailureCount: int | None = None
    streamReadCallElapsedMs: int | None = None
    streamRecovery: dict[str, Any] | None = None
    visualReadinessPassed: bool | None = None
    visualReadinessReason: str | None = None
    visualReadinessMs: int | None = None
    visualReadinessSharpnessMean: float | None = None
    visualReadinessSharpnessMin: float | None = None
    visualReadinessSharpnessRobustScore: float | None = None
    visualReadinessSharpnessGridMedian: float | None = None
    visualReadinessSharpnessGridLowerQuantile: float | None = None
    visualReadinessSharpCellRatio: float | None = None
    visualReadinessSharpCellCount: int | None = None
    visualReadinessTotalCellCount: int | None = None
    visualReadinessStabilityScore: float | None = None
    visualReadinessSharpnessTrend: float | None = None
    visualReadinessSharpnessImprovementRatio: float | None = None
    visualReadinessReadyWindowMsActual: int | None = None
    visualReadinessMinElapsedGatePassed: bool | None = None
    visualReadinessMinObserveGatePassed: bool | None = None
    visualReadinessMinReadyWindowGatePassed: bool | None = None
    visualReadinessStableBlurRejected: bool | None = None
    visualReadinessContinuedAfterCandidateReject: bool | None = None
    visualReadinessPostReadyRecheckPassed: bool | None = None
    visualReadinessPostReadyRecheckReason: str | None = None
    visualReadinessPostReadyRecheckFramesChecked: int | None = None
    visualReadinessPostReadyRecheckWindowMsActual: int | None = None
    sampleQualityPassed: bool | None = None
    sampleQualityReason: str | None = None
    sampleQualityRecoveryCount: int | None = None
    sampleQualityMaxRecoveriesConfigured: int | None = None
    sampleQualityRecoveryCountSemantics: str | None = None
    sampleQualityQualifiedFramesCollected: int | None = None
    sampleQualityAcceptedFrameCount: int | None = None
    sampleQualityRejectedFrames: int | None = None
    sampleQualityElapsedMs: int | None = None
    sampleQualityWindowMsActual: int | None = None
    sampleQualityWindowMaxAllowedMs: int | None = None
    sampleQualityReusedReadinessFrames: int | None = None
    sampleQualityRestartedDuringSampling: bool | None = None
    sampleQualityRejectSharpnessCount: int | None = None
    sampleQualityRejectClearCellRatioCount: int | None = None
    sampleQualityRejectStabilityCount: int | None = None
    sampleQualityFirstRejectedFrameIndex: int | None = None
    sampleQualityFirstRejectedElapsedMs: int | None = None
    sampleQualityFirstRejectedSharpness: float | None = None
    sampleQualityFirstRejectedClearCellRatio: float | None = None
    sampleQualityFirstRejectedStability: float | None = None
    sampleQualityLastRejectedFrameIndex: int | None = None
    sampleQualityLastRejectedElapsedMs: int | None = None
    sampleQualityLastRejectedSharpness: float | None = None
    sampleQualityLastRejectedClearCellRatio: float | None = None
    sampleQualityLastRejectedStability: float | None = None
    sampleQualityWindowTooLongRejected: bool | None = None
    sampleQualityWindowTooLongCandidateFrameCount: int | None = None
    sampleQualityWindowTooLongTriggerSharpness: float | None = None
    sampleQualityWindowTooLongTriggerClearCellRatio: float | None = None
    sampleQualityWindowTooLongTriggerStability: float | None = None
    sampleQualityStreamReadFailureReason: str | None = None
    sampleQualityStreamReadFailureCount: int | None = None
    sampleQualityStreamRecovered: bool | None = None
    sampleQualitySessionReopened: bool | None = None
    sampleQualityStreamRetryCount: int | None = None
    staticBrightInterferenceSuppressed: bool | None = None
    recognitionSampleMs: int | None = None
    recognitionDetectMs: int | None = None
    replaySaveStatus: str | None = None
    replaySaveStatusPath: str | None = None
    replaySaveMessage: str | None = None
    replayEvidenceReady: bool | None = None
    streamStartupStartFramePath: str | None = None
    streamStartupSettledFramePath: str | None = None
    sceneModeStabilityStartFramePath: str | None = None
    sceneModeStabilitySettledFramePath: str | None = None
    sceneProbeStartFramePath: str | None = None
    sceneProbeEndFramePath: str | None = None
    representativeFramePath: str | None = None
    roiToleranceSelectedFramePath: str | None = None
    debugImagePath: str | None = None
    visualReadinessStartFramePath: str | None = None
    visualReadinessReadyFramePath: str | None = None
    visualReadinessConfirmFramePath: str | None = None
    sampleStartFramePath: str | None = None
    sampleQualityAttemptStartFramePath: str | None = None
    sampleQualityDegradedFramePath: str | None = None
    sampleQualityLastQualifiedFramePath: str | None = None
    sampleQualityAcceptedMiddleFramePath: str | None = None
    sampleQualityAcceptedEndFramePath: str | None = None
    representativeFrameTargetPath: str | None = None
    roiToleranceSelectedFrameTargetPath: str | None = None
    debugImageTargetPath: str | None = None
    visualReadinessStartFrameTargetPath: str | None = None
    visualReadinessReadyFrameTargetPath: str | None = None
    visualReadinessConfirmFrameTargetPath: str | None = None
    sampleStartFrameTargetPath: str | None = None
    streamStartupStartFrameTargetPath: str | None = None
    streamStartupSettledFrameTargetPath: str | None = None
    sceneModeStabilityStartFrameTargetPath: str | None = None
    sceneModeStabilitySettledFrameTargetPath: str | None = None
    sceneProbeStartFrameTargetPath: str | None = None
    sceneProbeEndFrameTargetPath: str | None = None
    sampleQualityAttemptStartFrameTargetPath: str | None = None
    sampleQualityDegradedFrameTargetPath: str | None = None
    sampleQualityLastQualifiedFrameTargetPath: str | None = None
    sampleQualityAcceptedMiddleFrameTargetPath: str | None = None
    sampleQualityAcceptedEndFrameTargetPath: str | None = None
    recognitionResult: RecognitionRunResult | None = None


class PseudoMultiPointSummary(BaseModel):
    runStatus: RunStatusLiteral
    runId: str
    runDirectory: str
    startedAt: str
    finishedAt: str
    totalElapsedMs: int
    configuredRounds: int
    attemptedRounds: int
    successfulRounds: int
    failedRounds: int
    expectedVisualState: VisualStateLiteral
    configPath: str
    recognitionPresetIndex: int
    transitionPresetIndex: int
    requestedSceneModeOverride: SceneModeOverrideLiteral | None = None
    effectiveRequestedSceneMode: SceneModeOverrideLiteral
    transitionPresetTimeoutSeconds: float
    transitionSettleMs: int
    roundTimeoutSeconds: float
    roundTimeoutPolicy: RoundTimeoutPolicy = "warn_only"
    timeoutSemantics: str
    averageRoundElapsedMs: float | None = None
    minRoundElapsedMs: int | None = None
    maxRoundElapsedMs: int | None = None
    matchedExpectedRounds: int = 0
    timingSloExceededRounds: int = 0
    strictTimeoutFailedRounds: int = 0
    visualReadinessFailedRounds: int = 0
    visualBlurryBeforeDetectionRounds: int = 0
    visualNotReadyTimeoutRounds: int = 0
    staticBrightInterferenceSuppressedRounds: int = 0
    recognitionExecutionBreakdown: dict[str, int] = Field(default_factory=dict)
    visualReadinessFailureReasons: dict[str, int] = Field(default_factory=dict)
    sampleQualityFailureReasons: dict[str, int] = Field(default_factory=dict)
    failureBreakdownByStep: dict[str, int] = Field(default_factory=dict)
    roundResultFiles: list[str] = Field(default_factory=list)
    failureReviewPriority: str | None = None
    message: str | None = None


@dataclass(slots=True)
class PseudoMultiPointRuntimeConfig:
    configPath: Path
    transitionPresetIndex: int
    transitionSettleMs: int
    rounds: int
    expectedVisualState: VisualStateLiteral
    sceneModeOverride: SceneModeOverrideLiteral | None
    transitionPresetTimeoutSeconds: float
    roundTimeoutSeconds: float
    outputRoot: Path
    roundTimeoutPolicy: RoundTimeoutPolicy = "warn_only"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a phase-2 pseudo multi-point validation loop by alternating a transition preset and single-point recognition."
    )
    parser.add_argument("--config", required=True, help="Path to the preset-1 calibration JSON file.")
    parser.add_argument(
        "--transition-preset",
        type=int,
        required=True,
        help="Existing preset index used only as the transition point before each recognition round.",
    )
    parser.add_argument("--rounds", type=int, default=10, help="Number of repeated rounds to execute.")
    parser.add_argument(
        "--expected-visual-state",
        choices=("has_splash", "no_splash", "undetermined"),
        required=True,
        help="Expected recognition result for preset 1. Used only for validation, not for recognition logic.",
    )
    parser.add_argument(
        "--scene-mode",
        choices=("auto", "day_visible", "night_ir"),
        default=None,
        help="Optional override for recognition_v1.sceneMode. Default behavior inherits backend/local_config.json.",
    )
    parser.add_argument(
        "--transition-timeout-seconds",
        type=float,
        default=5.0,
        help="Soft timeout marker for the transition-preset API step.",
    )
    parser.add_argument(
        "--transition-settle-ms",
        type=int,
        default=1800,
        help="Extra wait after transition preset accepts before starting recognition.",
    )
    parser.add_argument(
        "--round-timeout-seconds",
        type=float,
        default=25.0,
        help="Soft timeout marker for a whole round. Does not forcibly cancel the inner recognition chain.",
    )
    parser.add_argument(
        "--round-timeout-policy",
        choices=("warn_only", "fail"),
        default="warn_only",
        help="warn_only preserves correct recognition with a timing warning; fail enforces the round timeout.",
    )
    parser.add_argument(
        "--output-root",
        default="data/pseudo_multi_point_tests",
        help="Directory under which this run writes one dedicated output folder.",
    )
    return parser


class PseudoMultiPointRunner:
    def __init__(
        self,
        runtime_config: PseudoMultiPointRuntimeConfig,
        *,
        run_once_service: RunOnceService,
        transition_turner: Any,
    ) -> None:
        self.runtime_config = runtime_config
        self.run_once_service = run_once_service
        self.transition_turner = transition_turner

    def execute(
        self,
        *,
        on_round_completed: Callable[[PseudoMultiPointRoundResult], None] | None = None,
    ) -> tuple[list[PseudoMultiPointRoundResult], str | None]:
        rounds: list[PseudoMultiPointRoundResult] = []
        interrupt_message: str | None = None
        calibration = storage_service.load_path(self.runtime_config.configPath)

        for round_index in range(1, self.runtime_config.rounds + 1):
            started_at = _iso_utc_now()
            round_started_perf = perf_counter()
            transition_result = self._turn_transition_preset(
                device_id=calibration.deviceId,
                channel_id=calibration.channelId,
            )
            transition_settle_wait_ms_actual = 0
            recognition_result: RecognitionRunResult | None = None
            expected_matched: bool | None = None
            actual_visual_state: VisualStateLiteral | None = None
            failure_step: str | None = None
            failure_reason: str | None = None
            status: RoundStatusLiteral = "failed"

            if not transition_result.accepted:
                failure_step = self._transition_failure_step(transition_result.failureCategory)
                failure_reason = transition_result.message or "Transition preset turn failed."
            elif transition_result.timedOut:
                failure_step = "transition_timeout"
                failure_reason = (
                    f"Transition preset step exceeded soft timeout {transition_result.timeoutSeconds:.2f}s "
                    f"(elapsed {transition_result.elapsedMs} ms)."
                )
            else:
                try:
                    transition_settle_wait_ms_actual = self._wait_transition_settle()
                    recognition_result = self.run_once_service.run(
                        config_path=self.runtime_config.configPath,
                        requested_preset_index=calibration.presetIndex,
                    )
                except KeyboardInterrupt:
                    interrupt_message = (
                        f"Interrupted during round {round_index} "
                        f"{'recognition' if transition_settle_wait_ms_actual > 0 else 'transition settle wait'}."
                    )
                    round_result = self._finalize_round(
                        round_index=round_index,
                        started_at=started_at,
                        round_started_perf=round_started_perf,
                        transition_result=transition_result,
                        transition_settle_wait_ms_actual=transition_settle_wait_ms_actual,
                        recognition_result=None,
                        expected_visual_state=self.runtime_config.expectedVisualState,
                        expected_matched=None,
                        actual_visual_state=None,
                        failure_step="interrupted",
                        failure_reason=interrupt_message,
                        status="failed",
                    )
                    rounds.append(round_result)
                    if on_round_completed is not None:
                        on_round_completed(round_result)
                    break
                except Exception as error:
                    failure_step = "recognition_exception"
                    failure_reason = f"Unexpected RunOnceService error: {error}"
                else:
                    actual_visual_state = recognition_result.visualState
                    expected_matched = (
                        recognition_result.executionResult == "success"
                        and recognition_result.visualState == self.runtime_config.expectedVisualState
                    )
                    if recognition_result.executionResult != "success":
                        if recognition_result.executionResult in {
                            "visual_not_ready",
                            "visual_not_ready_timeout",
                            "visual_blurry_before_detection",
                        }:
                            failure_step = "visual_readiness"
                        elif recognition_result.executionResult in {
                            "scene_mode_transition_timeout",
                            "scene_mode_probe_incomplete",
                        }:
                            failure_step = "scene_mode_transition"
                        elif recognition_result.executionResult in {
                            "sample_quality_degraded",
                            "sample_quality_timeout",
                        }:
                            failure_step = "sample_quality"
                        else:
                            failure_step = "recognition_execution"
                        failure_reason = recognition_result.message or (
                            f"Recognition executionResult={recognition_result.executionResult}"
                        )
                    elif not expected_matched:
                        failure_step = "visual_state_mismatch"
                        failure_reason = (
                            f"Expected visualState={self.runtime_config.expectedVisualState}, "
                            f"got {recognition_result.visualState}."
                        )
                    else:
                        status = "success"

            round_result = self._finalize_round(
                round_index=round_index,
                started_at=started_at,
                round_started_perf=round_started_perf,
                transition_result=transition_result,
                transition_settle_wait_ms_actual=transition_settle_wait_ms_actual,
                recognition_result=recognition_result,
                expected_visual_state=self.runtime_config.expectedVisualState,
                expected_matched=expected_matched,
                actual_visual_state=actual_visual_state,
                failure_step=failure_step,
                failure_reason=failure_reason,
                status=status,
            )
            rounds.append(round_result)
            if on_round_completed is not None:
                on_round_completed(round_result)

            if interrupt_message is not None:
                break

        return rounds, interrupt_message

    def _wait_transition_settle(self) -> int:
        wait_ms = max(0, self.runtime_config.transitionSettleMs)
        print(
            (
                "[pseudo-multi-point] "
                f"transition preset accepted, waiting {wait_ms} ms before recognition"
            ),
            file=sys.stderr,
        )
        if wait_ms <= 0:
            return 0

        started_at = perf_counter()
        sleep(wait_ms / 1000)
        return _elapsed_ms(started_at)

    def _turn_transition_preset(self, *, device_id: str, channel_id: str) -> TransitionPresetStepResult:
        started_at = perf_counter()
        try:
            response = self.transition_turner.turn_preset(
                device_id,
                channel_id,
                self.runtime_config.transitionPresetIndex,
            )
            elapsed_ms = _elapsed_ms(started_at)
            timed_out = elapsed_ms > int(round(self.runtime_config.transitionPresetTimeoutSeconds * 1000))
            message = None
            if timed_out:
                message = (
                    f"Transition preset step exceeded soft timeout {self.runtime_config.transitionPresetTimeoutSeconds:.2f}s "
                    f"(elapsed {elapsed_ms} ms)."
                )
            return TransitionPresetStepResult(
                accepted=bool(response.accepted),
                presetIndex=self.runtime_config.transitionPresetIndex,
                elapsedMs=elapsed_ms,
                timedOut=timed_out,
                timeoutSeconds=self.runtime_config.transitionPresetTimeoutSeconds,
                message=message,
                raw=response.raw,
                attemptCount=int(getattr(response, "attemptCount", 1)),
                attempts=list(getattr(response, "attempts", []) or []),
                unknownStateRetrySucceeded=bool(getattr(response, "unknownStateRetrySucceeded", False)),
            )
        except Exception as error:
            failure_category = getattr(error, "network_failure_kind", None)
            return TransitionPresetStepResult(
                accepted=False,
                presetIndex=self.runtime_config.transitionPresetIndex,
                elapsedMs=_elapsed_ms(started_at),
                timedOut=False,
                timeoutSeconds=self.runtime_config.transitionPresetTimeoutSeconds,
                message=str(error),
                raw=None,
                attemptCount=len(getattr(error, "attempts", []) or []) or 1,
                attempts=list(getattr(error, "attempts", []) or []),
                failureCategory=failure_category or "rejected",
            )

    @staticmethod
    def _transition_failure_step(failure_category: str | None) -> str:
        if failure_category in {"connect_timeout", "read_timeout"}:
            return "transition_preset_network_timeout"
        if failure_category == "connection_error":
            return "transition_preset_network_error"
        return "transition_preset_rejected"

    def _finalize_round(
        self,
        *,
        round_index: int,
        started_at: str,
        round_started_perf: float,
        transition_result: TransitionPresetStepResult,
        transition_settle_wait_ms_actual: int,
        recognition_result: RecognitionRunResult | None,
        expected_visual_state: VisualStateLiteral,
        expected_matched: bool | None,
        actual_visual_state: VisualStateLiteral | None,
        failure_step: str | None,
        failure_reason: str | None,
        status: RoundStatusLiteral,
    ) -> PseudoMultiPointRoundResult:
        round_elapsed_ms = _elapsed_ms(round_started_perf)
        round_timed_out = round_elapsed_ms > int(round(self.runtime_config.roundTimeoutSeconds * 1000))
        timing_slo_exceeded = round_timed_out
        timing_slo_reason = (
            (
                f"Round exceeded timing SLO {self.runtime_config.roundTimeoutSeconds:.2f}s "
                f"(elapsed {round_elapsed_ms} ms)."
            )
            if round_timed_out
            else None
        )
        strict_timeout_failed = False
        if round_timed_out and status == "success":
            if self.runtime_config.roundTimeoutPolicy == "fail":
                status = "failed"
                strict_timeout_failed = True
                failure_step = "round_timeout"
                failure_reason = timing_slo_reason
        elif round_timed_out and failure_step is None and self.runtime_config.roundTimeoutPolicy == "fail":
            failure_step = "round_timeout"
            failure_reason = timing_slo_reason

        recognition_execution_result = recognition_result.executionResult if recognition_result is not None else None
        transition_preset_attempt_count = transition_result.attemptCount
        transition_preset_attempts = transition_result.attempts
        transition_preset_failure_category = transition_result.failureCategory
        transition_preset_unknown_state_retry_succeeded = transition_result.unknownStateRetrySucceeded
        recognition_effective_scene_mode = recognition_result.effectiveSceneMode if recognition_result is not None else None
        recognition_effective_scene_profile = (
            recognition_result.effectiveSceneProfile if recognition_result is not None else None
        )
        scene_mode_initial = (
            recognition_result.sceneModeStability.sceneModeInitial
            if recognition_result is not None and recognition_result.sceneModeStability is not None
            else None
        )
        scene_mode_final = (
            recognition_result.sceneModeStability.sceneModeFinal
            if recognition_result is not None and recognition_result.sceneModeStability is not None
            else None
        )
        scene_mode_stable = (
            recognition_result.sceneModeStability.sceneModeStable
            if recognition_result is not None and recognition_result.sceneModeStability is not None
            else None
        )
        scene_mode_stability_elapsed_ms = (
            recognition_result.sceneModeStability.sceneModeStabilityElapsedMs
            if recognition_result is not None and recognition_result.sceneModeStability is not None
            else None
        )
        scene_mode_stability_window_count = (
            recognition_result.sceneModeStability.sceneModeStabilityWindowCount
            if recognition_result is not None and recognition_result.sceneModeStability is not None
            else None
        )
        scene_mode_transition_observed = (
            recognition_result.sceneModeStability.sceneModeTransitionObserved
            if recognition_result is not None and recognition_result.sceneModeStability is not None
            else None
        )
        scene_mode_relock_count = (
            recognition_result.sceneModeStability.sceneModeRelockCount
            if recognition_result is not None and recognition_result.sceneModeStability is not None
            else None
        )
        scene_mode_relock_reason = (
            recognition_result.sceneModeStability.sceneModeRelockReason
            if recognition_result is not None and recognition_result.sceneModeStability is not None
            else None
        )
        scene_mode_transition_timeout = (
            recognition_result.sceneModeStability.sceneModeTransitionTimeout
            if recognition_result is not None and recognition_result.sceneModeStability is not None
            else None
        )
        focus_anchor_roi_fallback_used = (
            recognition_result.focusAnchorRoiFallbackUsed if recognition_result is not None else None
        )
        focus_anchor_roi_source = recognition_result.focusAnchorRoiSource if recognition_result is not None else None
        recognition_temporal_vote_reason = (
            recognition_result.scoreSummary.temporalVoteReason if recognition_result is not None else None
        )
        score_hard_gate_pass_count = (
            recognition_result.scoreSummary.hardGatePassCount if recognition_result is not None else None
        )
        score_dynamic_evidence_pass_count = (
            recognition_result.scoreSummary.dynamicEvidencePassCount if recognition_result is not None else None
        )
        score_largest_bright_component_pass_count = (
            recognition_result.scoreSummary.largestBrightComponentPassCount if recognition_result is not None else None
        )
        score_center_bright_coverage_pass_count = (
            recognition_result.scoreSummary.centerBrightCoveragePassCount if recognition_result is not None else None
        )
        score_vertical_spread_pass_count = (
            recognition_result.scoreSummary.verticalSpreadPassCount if recognition_result is not None else None
        )
        score_continuous_bright_pass_count = (
            recognition_result.scoreSummary.continuousBrightPassCount if recognition_result is not None else None
        )
        score_gap_fill_pass_count = (
            recognition_result.scoreSummary.gapFillPassCount if recognition_result is not None else None
        )
        score_hard_gate_min_gap_fill_ratio_configured = (
            recognition_result.scoreSummary.hardGateMinGapFillRatioConfigured if recognition_result is not None else None
        )
        score_gap_fill_ratio_mean = (
            recognition_result.scoreSummary.gapFillRatio if recognition_result is not None else None
        )
        roi_tolerance_enabled = recognition_result.roiToleranceEnabled if recognition_result is not None else None
        roi_tolerance_candidate_count = (
            recognition_result.roiToleranceCandidateCount if recognition_result is not None else None
        )
        roi_tolerance_evaluated_candidate_count = (
            recognition_result.roiToleranceEvaluatedCandidateCount if recognition_result is not None else None
        )
        roi_tolerance_selected_roi = (
            recognition_result.roiToleranceSelectedRoi.model_dump()
            if recognition_result is not None and recognition_result.roiToleranceSelectedRoi is not None
            else None
        )
        roi_tolerance_selected_offset_x_ratio = (
            recognition_result.roiToleranceSelectedOffsetXRatio if recognition_result is not None else None
        )
        roi_tolerance_selected_offset_y_ratio = (
            recognition_result.roiToleranceSelectedOffsetYRatio if recognition_result is not None else None
        )
        roi_tolerance_selected_scale = (
            recognition_result.roiToleranceSelectedScale if recognition_result is not None else None
        )
        roi_tolerance_base_frame_pass_count = (
            recognition_result.roiToleranceBaseFramePassCount if recognition_result is not None else None
        )
        roi_tolerance_selected_frame_pass_count = (
            recognition_result.roiToleranceSelectedFramePassCount if recognition_result is not None else None
        )
        roi_tolerance_rescued = recognition_result.roiToleranceRescued if recognition_result is not None else None
        twilight_profile_applied = recognition_result.twilightProfileApplied if recognition_result is not None else None
        twilight_profile_reason = recognition_result.twilightProfileReason if recognition_result is not None else None
        twilight_brightness_mean = recognition_result.twilightBrightnessMean if recognition_result is not None else None
        recognition_preset_turn_ms = recognition_result.timing.presetTurnMs if recognition_result is not None else None
        recognition_settle_wait_ms = recognition_result.timing.settleWaitMs if recognition_result is not None else None
        stream_startup_freshness_exit_reason = (
            recognition_result.streamStartupFreshness.exitReason
            if recognition_result is not None and recognition_result.streamStartupFreshness is not None
            else None
        )
        stream_startup_freshness_consumed_frames = (
            recognition_result.streamStartupFreshness.consumedFrames
            if recognition_result is not None and recognition_result.streamStartupFreshness is not None
            else None
        )
        stream_startup_freshness_elapsed_ms = (
            recognition_result.streamStartupFreshness.elapsedMs
            if recognition_result is not None and recognition_result.streamStartupFreshness is not None
            else None
        )
        stream_startup_freshness_jump_detected = (
            recognition_result.streamStartupFreshness.jumpDetected
            if recognition_result is not None and recognition_result.streamStartupFreshness is not None
            else None
        )
        stream_startup_freshness_stable_after_jump = (
            recognition_result.streamStartupFreshness.stableAfterJump
            if recognition_result is not None and recognition_result.streamStartupFreshness is not None
            else None
        )
        stream_startup_read_failure_reason = (
            recognition_result.streamStartupFreshness.streamReadFailureReason
            if recognition_result is not None and recognition_result.streamStartupFreshness is not None
            else None
        )
        stream_startup_read_failure_count = (
            recognition_result.streamStartupFreshness.streamReadFailureCount
            if recognition_result is not None and recognition_result.streamStartupFreshness is not None
            else None
        )
        stream_startup_read_call_elapsed_ms = (
            recognition_result.streamStartupFreshness.streamReadCallElapsedMs
            if recognition_result is not None and recognition_result.streamStartupFreshness is not None
            else None
        )
        pre_readiness_session_reopened = recognition_result.preReadinessSessionReopened if recognition_result else None
        pre_readiness_stream_recovered = recognition_result.preReadinessStreamRecovered if recognition_result else None
        pre_readiness_stream_retry_count = recognition_result.preReadinessStreamRetryCount if recognition_result else None
        stream_read_failure_reason = recognition_result.streamReadFailureReason if recognition_result else None
        stream_read_failure_count = recognition_result.streamReadFailureCount if recognition_result else None
        stream_read_call_elapsed_ms = recognition_result.streamReadCallElapsedMs if recognition_result else None
        stream_recovery = getattr(recognition_result, "streamRecovery", None) if recognition_result else None
        visual_readiness_passed = recognition_result.visualReadinessPassed if recognition_result is not None else None
        visual_readiness_reason = recognition_result.visualReadinessReason if recognition_result is not None else None
        visual_readiness_ms = recognition_result.timing.visualReadinessMs if recognition_result is not None else None
        visual_readiness_sharpness_mean = (
            recognition_result.visualReadiness.sharpnessMean
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_sharpness_min = (
            recognition_result.visualReadiness.sharpnessMin
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_sharpness_robust_score = (
            recognition_result.visualReadiness.sharpnessRobustScore
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_sharpness_grid_median = (
            recognition_result.visualReadiness.sharpnessGridMedian
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_sharpness_grid_lower_quantile = (
            recognition_result.visualReadiness.sharpnessGridLowerQuantile
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_sharp_cell_ratio = (
            recognition_result.visualReadiness.sharpCellRatio
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_sharp_cell_count = (
            recognition_result.visualReadiness.sharpCellCount
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_total_cell_count = (
            recognition_result.visualReadiness.totalCellCount
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_stability_score = (
            recognition_result.visualReadiness.stabilityScore
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_sharpness_trend = (
            recognition_result.visualReadiness.sharpnessTrend
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_sharpness_improvement_ratio = (
            recognition_result.visualReadiness.sharpnessImprovementRatio
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_ready_window_ms_actual = (
            recognition_result.visualReadiness.readyWindowMsActual
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_min_elapsed_gate_passed = (
            recognition_result.visualReadiness.minElapsedGatePassed
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_min_observe_gate_passed = (
            recognition_result.visualReadiness.minObserveGatePassed
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_min_ready_window_gate_passed = (
            recognition_result.visualReadiness.minReadyWindowGatePassed
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_stable_blur_rejected = (
            recognition_result.visualReadiness.stableBlurRejected
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_continued_after_candidate_reject = (
            recognition_result.visualReadiness.continuedAfterCandidateReject
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_post_ready_recheck_passed = (
            recognition_result.visualReadiness.postReadyRecheckPassed
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_post_ready_recheck_reason = (
            recognition_result.visualReadiness.postReadyRecheckReason
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_post_ready_recheck_frames_checked = (
            recognition_result.visualReadiness.postReadyRecheckFramesChecked
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        visual_readiness_post_ready_recheck_window_ms_actual = (
            recognition_result.visualReadiness.postReadyRecheckWindowMsActual
            if recognition_result is not None and recognition_result.visualReadiness is not None
            else None
        )
        sample_quality_passed = recognition_result.sampleQualityPassed if recognition_result is not None else None
        sample_quality_reason = recognition_result.sampleQualityReason if recognition_result is not None else None
        sample_quality_recovery_count = (
            recognition_result.sampleQuality.recoveryCount
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_max_recoveries_configured = (
            recognition_result.sampleQualityMaxRecoveriesConfigured if recognition_result is not None else None
        )
        sample_quality_recovery_count_semantics = (
            recognition_result.sampleQualityRecoveryCountSemantics if recognition_result is not None else None
        )
        sample_quality_qualified_frames_collected = (
            recognition_result.sampleQuality.qualifiedFramesCollected
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_accepted_frame_count = (
            recognition_result.sampleQuality.acceptedFrameCount
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_rejected_frames = (
            recognition_result.sampleQuality.rejectedFrames
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_elapsed_ms = (
            recognition_result.sampleQuality.elapsedMs
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_window_ms_actual = (
            recognition_result.sampleQuality.sampleWindowMsActual
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_window_max_allowed_ms = (
            recognition_result.sampleQuality.sampleWindowMaxAllowedMs
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_reused_readiness_frames = (
            recognition_result.sampleQuality.reusedReadinessFrames
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_restarted_during_sampling = (
            recognition_result.sampleQuality.restartedDuringSampling
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_reject_sharpness_count = (
            recognition_result.sampleQuality.rejectSharpnessCount
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_reject_clear_cell_ratio_count = (
            recognition_result.sampleQuality.rejectClearCellRatioCount
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_reject_stability_count = (
            recognition_result.sampleQuality.rejectStabilityCount
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_first_rejected_frame_index = (
            recognition_result.sampleQuality.firstRejectedFrameIndex
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_first_rejected_elapsed_ms = (
            recognition_result.sampleQuality.firstRejectedElapsedMs
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_first_rejected_sharpness = (
            recognition_result.sampleQuality.firstRejectedSharpness
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_first_rejected_clear_cell_ratio = (
            recognition_result.sampleQuality.firstRejectedClearCellRatio
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_first_rejected_stability = (
            recognition_result.sampleQuality.firstRejectedStability
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_last_rejected_frame_index = (
            recognition_result.sampleQuality.lastRejectedFrameIndex
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_last_rejected_elapsed_ms = (
            recognition_result.sampleQuality.lastRejectedElapsedMs
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_last_rejected_sharpness = (
            recognition_result.sampleQuality.lastRejectedSharpness
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_last_rejected_clear_cell_ratio = (
            recognition_result.sampleQuality.lastRejectedClearCellRatio
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_last_rejected_stability = (
            recognition_result.sampleQuality.lastRejectedStability
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_window_too_long_rejected = (
            recognition_result.sampleQuality.windowTooLongRejected
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_window_too_long_candidate_frame_count = (
            recognition_result.sampleQuality.windowTooLongCandidateFrameCount
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_window_too_long_trigger_sharpness = (
            recognition_result.sampleQuality.windowTooLongTriggerSharpness
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_window_too_long_trigger_clear_cell_ratio = (
            recognition_result.sampleQuality.windowTooLongTriggerClearCellRatio
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_window_too_long_trigger_stability = (
            recognition_result.sampleQuality.windowTooLongTriggerStability
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_stream_read_failure_reason = (
            recognition_result.sampleQuality.streamReadFailureReason
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_stream_read_failure_count = (
            recognition_result.sampleQuality.streamReadFailureCount
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_stream_recovered = (
            recognition_result.sampleQuality.sampleQualityStreamRecovered
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_session_reopened = (
            recognition_result.sampleQuality.sampleQualitySessionReopened
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        sample_quality_stream_retry_count = (
            recognition_result.sampleQuality.sampleQualityStreamRetryCount
            if recognition_result is not None and recognition_result.sampleQuality is not None
            else None
        )
        static_bright_interference_suppressed = (
            recognition_result.scoreSummary.staticBrightInterferenceSuppressed
            if recognition_result is not None
            else None
        )
        recognition_sample_ms = recognition_result.timing.sampleMs if recognition_result is not None else None
        recognition_detect_ms = recognition_result.timing.detectMs if recognition_result is not None else None
        replay_save_status = recognition_result.replaySave.status if recognition_result is not None else None
        replay_save_status_path = recognition_result.replaySave.statusPath if recognition_result is not None else None
        replay_save_message = recognition_result.replaySave.message if recognition_result is not None else None
        replay_evidence_ready = replay_save_status == "ready" if replay_save_status is not None else None
        stream_startup_start_frame_target_path = (
            recognition_result.evidencePaths.streamStartupStartFramePath if recognition_result is not None else None
        )
        stream_startup_settled_frame_target_path = (
            recognition_result.evidencePaths.streamStartupSettledFramePath if recognition_result is not None else None
        )
        scene_mode_stability_start_frame_target_path = (
            recognition_result.evidencePaths.sceneModeStabilityStartFramePath if recognition_result is not None else None
        )
        scene_mode_stability_settled_frame_target_path = (
            recognition_result.evidencePaths.sceneModeStabilitySettledFramePath
            if recognition_result is not None
            else None
        )
        scene_probe_start_frame_target_path = (
            recognition_result.evidencePaths.sceneProbeStartFramePath if recognition_result is not None else None
        )
        scene_probe_end_frame_target_path = (
            recognition_result.evidencePaths.sceneProbeEndFramePath if recognition_result is not None else None
        )
        representative_frame_target_path = (
            recognition_result.evidencePaths.representativeFramePath if recognition_result is not None else None
        )
        roi_tolerance_selected_frame_target_path = (
            recognition_result.evidencePaths.roiToleranceSelectedFramePath if recognition_result is not None else None
        )
        debug_image_target_path = recognition_result.evidencePaths.debugImagePath if recognition_result is not None else None
        visual_readiness_start_frame_target_path = (
            recognition_result.evidencePaths.visualReadinessStartFramePath if recognition_result is not None else None
        )
        visual_readiness_ready_frame_target_path = (
            recognition_result.evidencePaths.visualReadinessReadyFramePath if recognition_result is not None else None
        )
        visual_readiness_confirm_frame_target_path = (
            recognition_result.evidencePaths.visualReadinessConfirmFramePath if recognition_result is not None else None
        )
        sample_start_frame_target_path = (
            recognition_result.evidencePaths.sampleStartFramePath if recognition_result is not None else None
        )
        sample_quality_attempt_start_frame_target_path = (
            recognition_result.evidencePaths.sampleQualityAttemptStartFramePath
            if recognition_result is not None
            else None
        )
        sample_quality_degraded_frame_target_path = (
            recognition_result.evidencePaths.sampleQualityDegradedFramePath
            if recognition_result is not None
            else None
        )
        sample_quality_last_qualified_frame_target_path = (
            recognition_result.evidencePaths.sampleQualityLastQualifiedFramePath
            if recognition_result is not None
            else None
        )
        sample_quality_accepted_middle_frame_target_path = (
            recognition_result.evidencePaths.sampleQualityAcceptedMiddleFramePath
            if recognition_result is not None
            else None
        )
        sample_quality_accepted_end_frame_target_path = (
            recognition_result.evidencePaths.sampleQualityAcceptedEndFramePath
            if recognition_result is not None
            else None
        )
        stream_startup_start_frame_path = stream_startup_start_frame_target_path if replay_evidence_ready else None
        stream_startup_settled_frame_path = (
            stream_startup_settled_frame_target_path if replay_evidence_ready else None
        )
        scene_mode_stability_start_frame_path = (
            scene_mode_stability_start_frame_target_path if replay_evidence_ready else None
        )
        scene_mode_stability_settled_frame_path = (
            scene_mode_stability_settled_frame_target_path if replay_evidence_ready else None
        )
        scene_probe_start_frame_path = scene_probe_start_frame_target_path if replay_evidence_ready else None
        scene_probe_end_frame_path = scene_probe_end_frame_target_path if replay_evidence_ready else None
        representative_frame_path = representative_frame_target_path if replay_evidence_ready else None
        roi_tolerance_selected_frame_path = (
            roi_tolerance_selected_frame_target_path if replay_evidence_ready else None
        )
        debug_image_path = debug_image_target_path if replay_evidence_ready else None
        visual_readiness_start_frame_path = (
            visual_readiness_start_frame_target_path if replay_evidence_ready else None
        )
        visual_readiness_ready_frame_path = (
            visual_readiness_ready_frame_target_path if replay_evidence_ready else None
        )
        visual_readiness_confirm_frame_path = (
            visual_readiness_confirm_frame_target_path if replay_evidence_ready else None
        )
        sample_start_frame_path = sample_start_frame_target_path if replay_evidence_ready else None
        sample_quality_attempt_start_frame_path = (
            sample_quality_attempt_start_frame_target_path if replay_evidence_ready else None
        )
        sample_quality_degraded_frame_path = (
            sample_quality_degraded_frame_target_path if replay_evidence_ready else None
        )
        sample_quality_last_qualified_frame_path = (
            sample_quality_last_qualified_frame_target_path if replay_evidence_ready else None
        )
        sample_quality_accepted_middle_frame_path = (
            sample_quality_accepted_middle_frame_target_path if replay_evidence_ready else None
        )
        sample_quality_accepted_end_frame_path = (
            sample_quality_accepted_end_frame_target_path if replay_evidence_ready else None
        )

        return PseudoMultiPointRoundResult(
            roundIndex=round_index,
            startedAt=started_at,
            finishedAt=_iso_utc_now(),
            status=status,
            expectedVisualState=expected_visual_state,
            expectedVisualStateMatched=expected_matched,
            actualVisualState=actual_visual_state,
            failureStep=failure_step,
            failureReason=failure_reason,
            roundElapsedMs=round_elapsed_ms,
            roundTimedOut=round_timed_out,
            roundTimeoutSeconds=self.runtime_config.roundTimeoutSeconds,
            roundTimeoutPolicy=self.runtime_config.roundTimeoutPolicy,
            timingSloExceeded=timing_slo_exceeded,
            timingSloReason=timing_slo_reason,
            strictTimeoutFailed=strict_timeout_failed,
            transitionSettleMsConfigured=self.runtime_config.transitionSettleMs,
            transitionSettleWaitMsActual=transition_settle_wait_ms_actual,
            transitionPreset=transition_result,
            transitionPresetAttemptCount=transition_preset_attempt_count,
            transitionPresetAttempts=transition_preset_attempts,
            transitionPresetFailureCategory=transition_preset_failure_category,
            transitionPresetUnknownStateRetrySucceeded=transition_preset_unknown_state_retry_succeeded,
            recognitionExecutionResult=recognition_execution_result,
            recognitionEffectiveSceneMode=recognition_effective_scene_mode,
            recognitionEffectiveSceneProfile=recognition_effective_scene_profile,
            sceneModeInitial=scene_mode_initial,
            sceneModeFinal=scene_mode_final,
            sceneModeStable=scene_mode_stable,
            sceneModeStabilityElapsedMs=scene_mode_stability_elapsed_ms,
            sceneModeStabilityWindowCount=scene_mode_stability_window_count,
            sceneModeTransitionObserved=scene_mode_transition_observed,
            sceneModeRelockCount=scene_mode_relock_count,
            sceneModeRelockReason=scene_mode_relock_reason,
            sceneModeTransitionTimeout=scene_mode_transition_timeout,
            focusAnchorRoiFallbackUsed=focus_anchor_roi_fallback_used,
            focusAnchorRoiSource=focus_anchor_roi_source,
            recognitionTemporalVoteReason=recognition_temporal_vote_reason,
            scoreHardGatePassCount=score_hard_gate_pass_count,
            scoreDynamicEvidencePassCount=score_dynamic_evidence_pass_count,
            scoreLargestBrightComponentPassCount=score_largest_bright_component_pass_count,
            scoreCenterBrightCoveragePassCount=score_center_bright_coverage_pass_count,
            scoreVerticalSpreadPassCount=score_vertical_spread_pass_count,
            scoreContinuousBrightPassCount=score_continuous_bright_pass_count,
            scoreGapFillPassCount=score_gap_fill_pass_count,
            scoreHardGateMinGapFillRatioConfigured=score_hard_gate_min_gap_fill_ratio_configured,
            scoreGapFillRatioMean=score_gap_fill_ratio_mean,
            roiToleranceEnabled=roi_tolerance_enabled,
            roiToleranceCandidateCount=roi_tolerance_candidate_count,
            roiToleranceEvaluatedCandidateCount=roi_tolerance_evaluated_candidate_count,
            roiToleranceSelectedRoi=roi_tolerance_selected_roi,
            roiToleranceSelectedOffsetXRatio=roi_tolerance_selected_offset_x_ratio,
            roiToleranceSelectedOffsetYRatio=roi_tolerance_selected_offset_y_ratio,
            roiToleranceSelectedScale=roi_tolerance_selected_scale,
            roiToleranceBaseFramePassCount=roi_tolerance_base_frame_pass_count,
            roiToleranceSelectedFramePassCount=roi_tolerance_selected_frame_pass_count,
            roiToleranceRescued=roi_tolerance_rescued,
            twilightProfileApplied=twilight_profile_applied,
            twilightProfileReason=twilight_profile_reason,
            twilightBrightnessMean=twilight_brightness_mean,
            recognitionPresetTurnMs=recognition_preset_turn_ms,
            recognitionSettleWaitMs=recognition_settle_wait_ms,
            streamStartupFreshnessExitReason=stream_startup_freshness_exit_reason,
            streamStartupFreshnessConsumedFrames=stream_startup_freshness_consumed_frames,
            streamStartupFreshnessElapsedMs=stream_startup_freshness_elapsed_ms,
            streamStartupFreshnessJumpDetected=stream_startup_freshness_jump_detected,
            streamStartupFreshnessStableAfterJump=stream_startup_freshness_stable_after_jump,
            streamStartupReadFailureReason=stream_startup_read_failure_reason,
            streamStartupReadFailureCount=stream_startup_read_failure_count,
            streamStartupReadCallElapsedMs=stream_startup_read_call_elapsed_ms,
            preReadinessSessionReopened=pre_readiness_session_reopened,
            preReadinessStreamRecovered=pre_readiness_stream_recovered,
            preReadinessStreamRetryCount=pre_readiness_stream_retry_count,
            streamReadFailureReason=stream_read_failure_reason,
            streamReadFailureCount=stream_read_failure_count,
            streamReadCallElapsedMs=stream_read_call_elapsed_ms,
            streamRecovery=stream_recovery,
            visualReadinessPassed=visual_readiness_passed,
            visualReadinessReason=visual_readiness_reason,
            visualReadinessMs=visual_readiness_ms,
            visualReadinessSharpnessMean=visual_readiness_sharpness_mean,
            visualReadinessSharpnessMin=visual_readiness_sharpness_min,
            visualReadinessSharpnessRobustScore=visual_readiness_sharpness_robust_score,
            visualReadinessSharpnessGridMedian=visual_readiness_sharpness_grid_median,
            visualReadinessSharpnessGridLowerQuantile=visual_readiness_sharpness_grid_lower_quantile,
            visualReadinessSharpCellRatio=visual_readiness_sharp_cell_ratio,
            visualReadinessSharpCellCount=visual_readiness_sharp_cell_count,
            visualReadinessTotalCellCount=visual_readiness_total_cell_count,
            visualReadinessStabilityScore=visual_readiness_stability_score,
            visualReadinessSharpnessTrend=visual_readiness_sharpness_trend,
            visualReadinessSharpnessImprovementRatio=visual_readiness_sharpness_improvement_ratio,
            visualReadinessReadyWindowMsActual=visual_readiness_ready_window_ms_actual,
            visualReadinessMinElapsedGatePassed=visual_readiness_min_elapsed_gate_passed,
            visualReadinessMinObserveGatePassed=visual_readiness_min_observe_gate_passed,
            visualReadinessMinReadyWindowGatePassed=visual_readiness_min_ready_window_gate_passed,
            visualReadinessStableBlurRejected=visual_readiness_stable_blur_rejected,
            visualReadinessContinuedAfterCandidateReject=visual_readiness_continued_after_candidate_reject,
            visualReadinessPostReadyRecheckPassed=visual_readiness_post_ready_recheck_passed,
            visualReadinessPostReadyRecheckReason=visual_readiness_post_ready_recheck_reason,
            visualReadinessPostReadyRecheckFramesChecked=visual_readiness_post_ready_recheck_frames_checked,
            visualReadinessPostReadyRecheckWindowMsActual=visual_readiness_post_ready_recheck_window_ms_actual,
            sampleQualityPassed=sample_quality_passed,
            sampleQualityReason=sample_quality_reason,
            sampleQualityRecoveryCount=sample_quality_recovery_count,
            sampleQualityMaxRecoveriesConfigured=sample_quality_max_recoveries_configured,
            sampleQualityRecoveryCountSemantics=sample_quality_recovery_count_semantics,
            sampleQualityQualifiedFramesCollected=sample_quality_qualified_frames_collected,
            sampleQualityAcceptedFrameCount=sample_quality_accepted_frame_count,
            sampleQualityRejectedFrames=sample_quality_rejected_frames,
            sampleQualityElapsedMs=sample_quality_elapsed_ms,
            sampleQualityWindowMsActual=sample_quality_window_ms_actual,
            sampleQualityWindowMaxAllowedMs=sample_quality_window_max_allowed_ms,
            sampleQualityReusedReadinessFrames=sample_quality_reused_readiness_frames,
            sampleQualityRestartedDuringSampling=sample_quality_restarted_during_sampling,
            sampleQualityRejectSharpnessCount=sample_quality_reject_sharpness_count,
            sampleQualityRejectClearCellRatioCount=sample_quality_reject_clear_cell_ratio_count,
            sampleQualityRejectStabilityCount=sample_quality_reject_stability_count,
            sampleQualityFirstRejectedFrameIndex=sample_quality_first_rejected_frame_index,
            sampleQualityFirstRejectedElapsedMs=sample_quality_first_rejected_elapsed_ms,
            sampleQualityFirstRejectedSharpness=sample_quality_first_rejected_sharpness,
            sampleQualityFirstRejectedClearCellRatio=sample_quality_first_rejected_clear_cell_ratio,
            sampleQualityFirstRejectedStability=sample_quality_first_rejected_stability,
            sampleQualityLastRejectedFrameIndex=sample_quality_last_rejected_frame_index,
            sampleQualityLastRejectedElapsedMs=sample_quality_last_rejected_elapsed_ms,
            sampleQualityLastRejectedSharpness=sample_quality_last_rejected_sharpness,
            sampleQualityLastRejectedClearCellRatio=sample_quality_last_rejected_clear_cell_ratio,
            sampleQualityLastRejectedStability=sample_quality_last_rejected_stability,
            sampleQualityWindowTooLongRejected=sample_quality_window_too_long_rejected,
            sampleQualityWindowTooLongCandidateFrameCount=sample_quality_window_too_long_candidate_frame_count,
            sampleQualityWindowTooLongTriggerSharpness=sample_quality_window_too_long_trigger_sharpness,
            sampleQualityWindowTooLongTriggerClearCellRatio=sample_quality_window_too_long_trigger_clear_cell_ratio,
            sampleQualityWindowTooLongTriggerStability=sample_quality_window_too_long_trigger_stability,
            sampleQualityStreamReadFailureReason=sample_quality_stream_read_failure_reason,
            sampleQualityStreamReadFailureCount=sample_quality_stream_read_failure_count,
            sampleQualityStreamRecovered=sample_quality_stream_recovered,
            sampleQualitySessionReopened=sample_quality_session_reopened,
            sampleQualityStreamRetryCount=sample_quality_stream_retry_count,
            staticBrightInterferenceSuppressed=static_bright_interference_suppressed,
            recognitionSampleMs=recognition_sample_ms,
            recognitionDetectMs=recognition_detect_ms,
            replaySaveStatus=replay_save_status,
            replaySaveStatusPath=replay_save_status_path,
            replaySaveMessage=replay_save_message,
            replayEvidenceReady=replay_evidence_ready,
            streamStartupStartFramePath=stream_startup_start_frame_path,
            streamStartupSettledFramePath=stream_startup_settled_frame_path,
            sceneModeStabilityStartFramePath=scene_mode_stability_start_frame_path,
            sceneModeStabilitySettledFramePath=scene_mode_stability_settled_frame_path,
            sceneProbeStartFramePath=scene_probe_start_frame_path,
            sceneProbeEndFramePath=scene_probe_end_frame_path,
            representativeFramePath=representative_frame_path,
            roiToleranceSelectedFramePath=roi_tolerance_selected_frame_path,
            debugImagePath=debug_image_path,
            visualReadinessStartFramePath=visual_readiness_start_frame_path,
            visualReadinessReadyFramePath=visual_readiness_ready_frame_path,
            visualReadinessConfirmFramePath=visual_readiness_confirm_frame_path,
            sampleStartFramePath=sample_start_frame_path,
            sampleQualityAttemptStartFramePath=sample_quality_attempt_start_frame_path,
            sampleQualityDegradedFramePath=sample_quality_degraded_frame_path,
            sampleQualityLastQualifiedFramePath=sample_quality_last_qualified_frame_path,
            sampleQualityAcceptedMiddleFramePath=sample_quality_accepted_middle_frame_path,
            sampleQualityAcceptedEndFramePath=sample_quality_accepted_end_frame_path,
            streamStartupStartFrameTargetPath=stream_startup_start_frame_target_path,
            streamStartupSettledFrameTargetPath=stream_startup_settled_frame_target_path,
            sceneModeStabilityStartFrameTargetPath=scene_mode_stability_start_frame_target_path,
            sceneModeStabilitySettledFrameTargetPath=scene_mode_stability_settled_frame_target_path,
            sceneProbeStartFrameTargetPath=scene_probe_start_frame_target_path,
            sceneProbeEndFrameTargetPath=scene_probe_end_frame_target_path,
            representativeFrameTargetPath=representative_frame_target_path,
            roiToleranceSelectedFrameTargetPath=roi_tolerance_selected_frame_target_path,
            debugImageTargetPath=debug_image_target_path,
            visualReadinessStartFrameTargetPath=visual_readiness_start_frame_target_path,
            visualReadinessReadyFrameTargetPath=visual_readiness_ready_frame_target_path,
            visualReadinessConfirmFrameTargetPath=visual_readiness_confirm_frame_target_path,
            sampleStartFrameTargetPath=sample_start_frame_target_path,
            sampleQualityAttemptStartFrameTargetPath=sample_quality_attempt_start_frame_target_path,
            sampleQualityDegradedFrameTargetPath=sample_quality_degraded_frame_target_path,
            sampleQualityLastQualifiedFrameTargetPath=sample_quality_last_qualified_frame_target_path,
            sampleQualityAcceptedMiddleFrameTargetPath=sample_quality_accepted_middle_frame_target_path,
            sampleQualityAcceptedEndFrameTargetPath=sample_quality_accepted_end_frame_target_path,
            recognitionResult=recognition_result,
        )


def build_runtime_config(args: argparse.Namespace) -> PseudoMultiPointRuntimeConfig:
    config_path = resolve_config_path(args.config)
    output_root = _resolve_output_root(args.output_root)
    return PseudoMultiPointRuntimeConfig(
        configPath=config_path,
        transitionPresetIndex=args.transition_preset,
        transitionSettleMs=args.transition_settle_ms,
        rounds=args.rounds,
        expectedVisualState=args.expected_visual_state,
        sceneModeOverride=args.scene_mode,
        transitionPresetTimeoutSeconds=args.transition_timeout_seconds,
        roundTimeoutSeconds=args.round_timeout_seconds,
        outputRoot=output_root,
        roundTimeoutPolicy=args.round_timeout_policy,
    )


def build_run_once_service(scene_mode_override: SceneModeOverrideLiteral | None) -> RunOnceService:
    if scene_mode_override is None:
        return RunOnceService()

    raw_config = load_recognition_raw_config()
    raw_config["sceneMode"] = scene_mode_override
    global_config = build_recognition_config(raw_config)
    return RunOnceService(global_config=global_config, raw_config=raw_config)


def preflight(runtime_config: PseudoMultiPointRuntimeConfig) -> tuple[RunOnceService, Any]:
    calibration = storage_service.load_path(runtime_config.configPath)
    if runtime_config.transitionPresetIndex == calibration.presetIndex:
        raise ValueError(
            f"transition preset {runtime_config.transitionPresetIndex} must differ from recognition preset {calibration.presetIndex}"
        )
    if not settings.is_dahua_configured:
        raise RuntimeError("Dahua credentials are not configured.")

    try:
        from app.services.dahua_preset_service import preset_service
    except Exception as error:
        raise RuntimeError(f"Failed to import Dahua preset runtime: {error}") from error

    return build_run_once_service(runtime_config.sceneModeOverride), preset_service


def create_run_directory(
    runtime_config: PseudoMultiPointRuntimeConfig,
    *,
    recognition_preset_index: int,
) -> Path:
    timestamp = _iso_utc_now().replace(":", "-")
    run_name = (
        f"{_safe_name(runtime_config.configPath.stem)}"
        f"_p{recognition_preset_index}_t{runtime_config.transitionPresetIndex}"
        f"_{runtime_config.expectedVisualState}_{timestamp}"
    )
    run_dir = runtime_config.outputRoot / run_name
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def emit_round_progress(round_result: PseudoMultiPointRoundResult) -> None:
    recognition = round_result.recognitionResult
    parts = [
        "[pseudo-multi-point]",
        f"round={round_result.roundIndex}",
        f"status={round_result.status}",
        f"transitionAccepted={round_result.transitionPreset.accepted}",
        f"transitionTimedOut={round_result.transitionPreset.timedOut}",
        f"transitionSettleMs={round_result.transitionSettleMsConfigured}",
        f"transitionSettleWaitMs={round_result.transitionSettleWaitMsActual}",
        f"roundTimedOut={round_result.roundTimedOut}",
        f"timingSloExceeded={round_result.timingSloExceeded}",
        f"strictTimeoutFailed={round_result.strictTimeoutFailed}",
        f"expected={round_result.expectedVisualState}",
        f"actual={round_result.actualVisualState}",
        f"matched={round_result.expectedVisualStateMatched}",
        f"elapsedMs={round_result.roundElapsedMs}",
    ]
    if recognition is not None:
        parts.append(f"executionResult={recognition.executionResult}")
        parts.append(f"scene={recognition.effectiveSceneMode}")
        parts.append(f"presetTurnMs={recognition.timing.presetTurnMs}")
        parts.append(f"settleWaitMs={recognition.timing.settleWaitMs}")
        parts.append(f"visualReady={recognition.visualReadinessPassed}")
        parts.append(f"visualReadyReason={recognition.visualReadinessReason}")
        parts.append(f"visualReadinessMs={recognition.timing.visualReadinessMs}")
        parts.append(f"staticBrightGate={recognition.scoreSummary.staticBrightInterferenceSuppressed}")
        parts.append(f"sampleMs={recognition.timing.sampleMs}")
        parts.append(f"detectMs={recognition.timing.detectMs}")
    if round_result.failureStep:
        parts.append(f"failureStep={round_result.failureStep}")
    if round_result.failureReason:
        parts.append(f"reason={round_result.failureReason}")
    if round_result.timingSloExceeded:
        if round_result.status == "success":
            parts.append("timingOutcome=recognized_but_over_slo")
        elif round_result.strictTimeoutFailed:
            parts.append("timingOutcome=strict_timeout_failed")
        else:
            parts.append("timingOutcome=over_slo_with_recognition_failure")
        if round_result.timingSloReason:
            parts.append(f"timingSloReason={round_result.timingSloReason}")
    print(" ".join(parts), file=sys.stderr)


def build_summary(
    *,
    run_status: RunStatusLiteral,
    run_id: str,
    run_dir: Path,
    started_at: str,
    total_started_perf: float,
    runtime_config: PseudoMultiPointRuntimeConfig,
    recognition_preset_index: int,
    effective_requested_scene_mode: SceneModeOverrideLiteral,
    rounds: list[PseudoMultiPointRoundResult],
    message: str | None,
) -> PseudoMultiPointSummary:
    round_timeout_policy: RoundTimeoutPolicy = getattr(runtime_config, "roundTimeoutPolicy", "warn_only")
    round_elapsed_values = [item.roundElapsedMs for item in rounds]
    failure_breakdown = Counter(item.failureStep for item in rounds if item.failureStep)
    execution_breakdown = Counter(item.recognitionExecutionResult for item in rounds if item.recognitionExecutionResult)
    visual_readiness_reason_breakdown = Counter(
        item.visualReadinessReason for item in rounds if item.visualReadinessPassed is False and item.visualReadinessReason
    )
    sample_quality_reason_breakdown = Counter(
        item.sampleQualityReason for item in rounds if item.sampleQualityPassed is False and item.sampleQualityReason
    )
    successful_rounds = sum(1 for item in rounds if item.status == "success")
    matched_expected_rounds = sum(1 for item in rounds if item.expectedVisualStateMatched is True)
    visual_readiness_failed_rounds = sum(1 for item in rounds if item.visualReadinessPassed is False)
    visual_blurry_before_detection_rounds = sum(
        1 for item in rounds if item.recognitionExecutionResult == "visual_blurry_before_detection"
    )
    visual_not_ready_timeout_rounds = sum(1 for item in rounds if item.recognitionExecutionResult == "visual_not_ready_timeout")
    static_bright_interference_suppressed_rounds = sum(
        1 for item in rounds if item.staticBrightInterferenceSuppressed is True
    )
    timing_slo_exceeded_rounds = sum(1 for item in rounds if item.timingSloExceeded)
    strict_timeout_failed_rounds = sum(1 for item in rounds if item.strictTimeoutFailed)
    return PseudoMultiPointSummary(
        runStatus=run_status,
        runId=run_id,
        runDirectory=str(run_dir),
        startedAt=started_at,
        finishedAt=_iso_utc_now(),
        totalElapsedMs=_elapsed_ms(total_started_perf),
        configuredRounds=runtime_config.rounds,
        attemptedRounds=len(rounds),
        successfulRounds=successful_rounds,
        failedRounds=len(rounds) - successful_rounds,
        expectedVisualState=runtime_config.expectedVisualState,
        configPath=str(runtime_config.configPath),
        recognitionPresetIndex=recognition_preset_index,
        transitionPresetIndex=runtime_config.transitionPresetIndex,
        requestedSceneModeOverride=runtime_config.sceneModeOverride,
        effectiveRequestedSceneMode=effective_requested_scene_mode,
        transitionPresetTimeoutSeconds=runtime_config.transitionPresetTimeoutSeconds,
        transitionSettleMs=runtime_config.transitionSettleMs,
        roundTimeoutSeconds=runtime_config.roundTimeoutSeconds,
        roundTimeoutPolicy=round_timeout_policy,
        timeoutSemantics=(
            "soft_observation_warn_only" if round_timeout_policy == "warn_only" else "strict_timeout_failure"
        ),
        averageRoundElapsedMs=(
            round(sum(round_elapsed_values) / len(round_elapsed_values), 2) if round_elapsed_values else None
        ),
        minRoundElapsedMs=min(round_elapsed_values) if round_elapsed_values else None,
        maxRoundElapsedMs=max(round_elapsed_values) if round_elapsed_values else None,
        matchedExpectedRounds=matched_expected_rounds,
        timingSloExceededRounds=timing_slo_exceeded_rounds,
        strictTimeoutFailedRounds=strict_timeout_failed_rounds,
        visualReadinessFailedRounds=visual_readiness_failed_rounds,
        visualBlurryBeforeDetectionRounds=visual_blurry_before_detection_rounds,
        visualNotReadyTimeoutRounds=visual_not_ready_timeout_rounds,
        staticBrightInterferenceSuppressedRounds=static_bright_interference_suppressed_rounds,
        recognitionExecutionBreakdown=dict(sorted(execution_breakdown.items())),
        visualReadinessFailureReasons=dict(sorted(visual_readiness_reason_breakdown.items())),
        sampleQualityFailureReasons=dict(sorted(sample_quality_reason_breakdown.items())),
        failureBreakdownByStep=dict(sorted(failure_breakdown.items())),
        roundResultFiles=[f"round_{item.roundIndex:02d}.json" for item in rounds],
        failureReviewPriority=(
            "If failure keeps effectiveSceneMode normal, overflowFrameRatio at 0, globalMotionExceeded false, "
            "and representative frame shows subject shifted or cropped, treat it as sampling/composition timing first, "
            "not algorithm-threshold drift."
        ),
        message=message,
    )


def write_json(path: Path, payload: BaseModel | dict[str, Any]) -> None:
    if isinstance(payload, BaseModel):
        data = payload.model_dump()
    else:
        data = payload
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def persist_round_result(run_dir: Path, round_result: PseudoMultiPointRoundResult) -> None:
    write_json(run_dir / f"round_{round_result.roundIndex:02d}.json", round_result)
    emit_round_progress(round_result)


def _resolve_output_root(raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate
    return settings.workspace_root / candidate


def _safe_name(value: str) -> str:
    allowed = []
    for char in value.strip():
        if char.isalnum() or char in {"-", "_", "."}:
            allowed.append(char)
        else:
            allowed.append("_")
    return "".join(allowed).strip("_") or "run"


def _iso_utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _elapsed_ms(started_at: float) -> int:
    return max(1, int(round((perf_counter() - started_at) * 1000)))


def main(argv: list[str] | None = None) -> int:
    setup_logging("inspector", force=True)
    parser = build_parser()
    args = parser.parse_args(argv)
    runtime_config = build_runtime_config(args)
    total_started_perf = perf_counter()
    started_at = _iso_utc_now()

    run_dir: Path | None = None
    rounds: list[PseudoMultiPointRoundResult] = []
    exit_code = 1
    run_status: RunStatusLiteral = "aborted"
    summary_message: str | None = None
    recognition_preset_index = -1
    effective_requested_scene_mode: SceneModeOverrideLiteral = "day_visible"

    try:
        calibration = storage_service.load_path(runtime_config.configPath)
        recognition_preset_index = calibration.presetIndex
        service, preset_service = preflight(runtime_config)
        effective_requested_scene_mode = service.global_config.sceneMode
        run_dir = create_run_directory(runtime_config, recognition_preset_index=recognition_preset_index)

        print(
            (
                "[pseudo-multi-point] "
                f"runDir={run_dir} "
                f"recognitionPreset={recognition_preset_index} "
                f"transitionPreset={runtime_config.transitionPresetIndex} "
                f"transitionSettleMs={runtime_config.transitionSettleMs} "
                f"rounds={runtime_config.rounds} "
                f"expected={runtime_config.expectedVisualState} "
                f"sceneMode={effective_requested_scene_mode}"
            ),
            file=sys.stderr,
        )

        runner = PseudoMultiPointRunner(
            runtime_config,
            run_once_service=service,
            transition_turner=preset_service,
        )
        rounds, interrupt_message = runner.execute(
            on_round_completed=lambda round_result: persist_round_result(run_dir, round_result)
        )

        if interrupt_message is not None:
            run_status = "interrupted"
            summary_message = interrupt_message
            exit_code = 130
        else:
            run_status = "completed"
            exit_code = 0 if all(item.status == "success" for item in rounds) and len(rounds) == runtime_config.rounds else 1
    except KeyboardInterrupt:
        run_status = "interrupted"
        summary_message = "Interrupted before all rounds completed."
        exit_code = 130
    except Exception as error:
        run_status = "aborted"
        summary_message = f"{error}\n{traceback.format_exc()}"
        exit_code = 1
    finally:
        if run_dir is None:
            fallback_stamp = _iso_utc_now().replace(":", "-")
            fallback_root = runtime_config.outputRoot
            fallback_root.mkdir(parents=True, exist_ok=True)
            run_dir = fallback_root / f"aborted_{fallback_stamp}"
            run_dir.mkdir(parents=True, exist_ok=True)

        summary = build_summary(
            run_status=run_status,
            run_id=run_dir.name,
            run_dir=run_dir,
            started_at=started_at,
            total_started_perf=total_started_perf,
            runtime_config=runtime_config,
            recognition_preset_index=recognition_preset_index,
            effective_requested_scene_mode=effective_requested_scene_mode,
            rounds=rounds,
            message=summary_message,
        )
        write_json(run_dir / "summary.json", summary)
        print(json.dumps(summary.model_dump(), ensure_ascii=False, indent=2))

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
