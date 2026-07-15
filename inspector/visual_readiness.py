from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

import numpy as np

from app.schemas.calibration import RoiModel
from inspector.config import RecognitionGlobalConfig
from inspector.flv_sampler import FlvStreamSession
from inspector.models import VisualReadinessMetrics


@dataclass(slots=True)
class VisualReadinessOutcome:
    metrics: VisualReadinessMetrics
    frames: list[np.ndarray]
    frameTimestampsMs: list[int]
    frameCapturedAts: list[float]
    streamType: str
    streamUrl: str
    readyFrameIndex: int | None = None
    confirmFrameIndex: int | None = None


@dataclass(slots=True)
class FrameQualityEvaluation:
    frameIndex: int
    capturedAt: float
    sharpness: float
    gridMedian: float
    gridLowerQuantile: float
    clearCellRatio: float
    sharpCellCount: int
    totalCellCount: int
    stability: float


@dataclass(slots=True)
class _PendingReadyCandidate:
    frameIndex: int
    capturedAt: float


class VisualReadinessChecker:
    def __init__(self, global_config: RecognitionGlobalConfig) -> None:
        self.global_config = global_config

    def wait_until_ready(
        self,
        session: FlvStreamSession,
        *,
        roi: RoiModel | None = None,
    ) -> VisualReadinessOutcome:
        started_at = monotonic()
        deadline = started_at + self.global_config.visualReadinessTimeoutMs / 1000
        previous_focus_gray: np.ndarray | None = None
        frames: list[np.ndarray] = []
        timestamps_ms: list[int] = []
        frame_captured_ats: list[float] = []
        frame_history: list[FrameQualityEvaluation] = []
        sharpness_values: list[float] = []
        stability_values: list[float] = []
        pass_window: list[FrameQualityEvaluation] = []
        stable_window: list[FrameQualityEvaluation] = []
        pending_candidate: _PendingReadyCandidate | None = None
        confirmation_frames: list[FrameQualityEvaluation] = []
        extended_deadline: float | None = None
        min_elapsed_gate_passed = False
        min_observe_gate_passed = False
        min_ready_window_gate_passed = False
        stable_blur_observed = False
        continued_after_candidate_reject = False
        post_ready_recheck_passed: bool | None = None
        post_ready_recheck_reason: str | None = None
        post_ready_recheck_frames_checked = 0
        post_ready_recheck_window_ms_actual = 0

        while True:
            active_deadline = extended_deadline if extended_deadline is not None else deadline
            if monotonic() >= active_deadline:
                break
            frame_result = session.read_frame_until(active_deadline)
            if frame_result is None:
                break

            frame, now = frame_result
            frame_index = len(frames)
            frame_stats, previous_focus_gray = self.evaluate_frame_quality(
                frame,
                frame_index=frame_index,
                captured_at=now,
                roi=roi,
                previous_gray=previous_focus_gray,
            )

            frames.append(frame.copy())
            timestamps_ms.append(int(round((now - started_at) * 1000)))
            frame_captured_ats.append(now)
            frame_history.append(frame_stats)
            sharpness_values.append(frame_stats.sharpness)
            if len(frames) > 1:
                stability_values.append(frame_stats.stability)

            stable_frame = frame_stats.stability <= self.global_config.visualReadinessMaxStabilityScore
            frame_passed = self._frame_passed(frame_stats)

            if stable_frame:
                stable_window.append(frame_stats)
            else:
                stable_window.clear()
                stable_blur_observed = False

            if frame_passed:
                pass_window.append(frame_stats)
                stable_blur_observed = False
            else:
                pass_window.clear()

            if pending_candidate is not None and frame_index > pending_candidate.frameIndex:
                if frame_passed:
                    confirmation_frames.append(frame_stats)
                    post_ready_recheck_frames_checked = len(confirmation_frames)
                    post_ready_recheck_window_ms_actual = max(
                        0,
                        int(round((frame_stats.capturedAt - pending_candidate.capturedAt) * 1000)),
                    )
                    if self._confirmation_satisfied(confirmation_frames, pending_candidate):
                        summary = self._window_summary(
                            frame_history=frame_history,
                            sharpness_values=sharpness_values,
                            stability_values=stability_values,
                            ready_window=pass_window,
                        )
                        elapsed_ms = int(round((now - started_at) * 1000))
                        return VisualReadinessOutcome(
                            metrics=self._build_metrics(
                                ready=True,
                                reason="visual_ready",
                                summary=summary,
                                frames_checked=len(frames),
                                elapsed_ms=elapsed_ms,
                                min_elapsed_gate_passed=min_elapsed_gate_passed,
                                min_observe_gate_passed=min_observe_gate_passed,
                                min_ready_window_gate_passed=min_ready_window_gate_passed,
                                stable_blur_rejected=False,
                                continued_after_candidate_reject=continued_after_candidate_reject,
                                post_ready_recheck_passed=True,
                                post_ready_recheck_reason="visual_ready_recheck_passed",
                                post_ready_recheck_frames_checked=post_ready_recheck_frames_checked,
                                post_ready_recheck_window_ms_actual=post_ready_recheck_window_ms_actual,
                            ),
                            frames=frames,
                            frameTimestampsMs=timestamps_ms,
                            frameCapturedAts=frame_captured_ats,
                            streamType=session.streamType,
                            streamUrl=session.streamUrl,
                            readyFrameIndex=pending_candidate.frameIndex,
                            confirmFrameIndex=frame_index,
                        )
                else:
                    post_ready_recheck_passed = False
                    post_ready_recheck_reason = self._post_ready_recheck_failure_reason(frame_stats)
                    post_ready_recheck_frames_checked = len(confirmation_frames)
                    post_ready_recheck_window_ms_actual = max(
                        0,
                        int(round((frame_stats.capturedAt - pending_candidate.capturedAt) * 1000)),
                    )
                    continued_after_candidate_reject = True
                    pending_candidate = None
                    confirmation_frames.clear()
                    extended_deadline = None

            if len(pass_window) >= self.global_config.visualReadinessMinFrames:
                elapsed_ms = int(round((now - started_at) * 1000))
                min_elapsed_gate_passed = elapsed_ms >= self.global_config.visualReadinessMinElapsedMs
                min_observe_gate_passed = elapsed_ms >= self.global_config.visualReadinessMinObserveMs
                if not min_elapsed_gate_passed or not min_observe_gate_passed:
                    continue
                ready_summary = self._window_summary(
                    frame_history=frame_history,
                    sharpness_values=sharpness_values,
                    stability_values=stability_values,
                    ready_window=pass_window,
                )
                min_ready_window_gate_passed = (
                    ready_summary["readyWindowMsActual"] >= self.global_config.visualReadinessMinReadyWindowMs
                )
                if not min_ready_window_gate_passed:
                    continue
                if self._converged(ready_summary):
                    if self._post_ready_recheck_enabled():
                        if pending_candidate is None:
                            pending_candidate = _PendingReadyCandidate(
                                frameIndex=frame_index,
                                capturedAt=now,
                            )
                            confirmation_frames.clear()
                            post_ready_recheck_passed = False
                            post_ready_recheck_reason = "visual_ready_candidate_pending_recheck"
                            post_ready_recheck_frames_checked = 0
                            post_ready_recheck_window_ms_actual = 0
                            extended_deadline = self._extended_recheck_deadline(
                                now=now,
                                deadline=deadline,
                                current_extended_deadline=extended_deadline,
                            )
                    else:
                        return VisualReadinessOutcome(
                            metrics=self._build_metrics(
                                ready=True,
                                reason="visual_ready",
                                summary=ready_summary,
                                frames_checked=len(frames),
                                elapsed_ms=elapsed_ms,
                                min_elapsed_gate_passed=True,
                                min_observe_gate_passed=True,
                                min_ready_window_gate_passed=True,
                                stable_blur_rejected=False,
                                continued_after_candidate_reject=continued_after_candidate_reject,
                                post_ready_recheck_passed=None,
                                post_ready_recheck_reason=None,
                                post_ready_recheck_frames_checked=0,
                                post_ready_recheck_window_ms_actual=0,
                            ),
                            frames=frames,
                            frameTimestampsMs=timestamps_ms,
                            frameCapturedAts=frame_captured_ats,
                            streamType=session.streamType,
                            streamUrl=session.streamUrl,
                            readyFrameIndex=frame_index,
                            confirmFrameIndex=None,
                        )

            if len(stable_window) >= self.global_config.visualReadinessMinFrames:
                elapsed_ms = int(round((now - started_at) * 1000))
                min_elapsed_gate_passed = elapsed_ms >= self.global_config.visualReadinessMinElapsedMs
                min_observe_gate_passed = elapsed_ms >= self.global_config.visualReadinessMinObserveMs
                if not min_elapsed_gate_passed or not min_observe_gate_passed:
                    continue
                stable_summary = self._window_summary(
                    frame_history=frame_history,
                    sharpness_values=sharpness_values,
                    stability_values=stability_values,
                    ready_window=stable_window,
                )
                min_ready_window_gate_passed = (
                    stable_summary["readyWindowMsActual"] >= self.global_config.visualReadinessMinReadyWindowMs
                )
                stable_blur_observed = (
                    min_ready_window_gate_passed and self._stable_blur_rejected(stable_summary)
                )

        elapsed_ms = max(
            int(round((monotonic() - started_at) * 1000)),
            timestamps_ms[-1] if timestamps_ms else 0,
        )
        if pending_candidate is not None and post_ready_recheck_reason is None:
            post_ready_recheck_passed = False
            post_ready_recheck_reason = "visual_post_ready_recheck_timeout"
            post_ready_recheck_frames_checked = len(confirmation_frames)
            if confirmation_frames:
                post_ready_recheck_window_ms_actual = max(
                    0,
                    int(round((confirmation_frames[-1].capturedAt - pending_candidate.capturedAt) * 1000)),
                )

        diagnostic_summary = self._history_summary(
            frame_history,
            sharpness_values,
            stability_values,
            timestamps_ms,
            active_window=pass_window if pass_window else stable_window,
        )
        reason = self._failure_reason(
            sharpness_mean=self._none_if_empty(sharpness_values, np.mean),
            sharpness_min=self._none_if_empty(sharpness_values, np.min),
            stability_mean=self._none_if_empty(stability_values, np.mean),
            min_elapsed_gate_passed=min_elapsed_gate_passed,
            min_observe_gate_passed=min_observe_gate_passed,
            min_ready_window_gate_passed=min_ready_window_gate_passed,
            stable_blur_rejected=stable_blur_observed,
        )
        if post_ready_recheck_reason == "visual_post_ready_recheck_timeout":
            reason = "visual_post_ready_recheck_timeout"
        return VisualReadinessOutcome(
            metrics=self._build_metrics(
                ready=False,
                reason=reason,
                summary=diagnostic_summary,
                frames_checked=len(frames),
                elapsed_ms=elapsed_ms,
                min_elapsed_gate_passed=min_elapsed_gate_passed,
                min_observe_gate_passed=min_observe_gate_passed,
                min_ready_window_gate_passed=min_ready_window_gate_passed,
                stable_blur_rejected=stable_blur_observed,
                continued_after_candidate_reject=continued_after_candidate_reject,
                post_ready_recheck_passed=post_ready_recheck_passed,
                post_ready_recheck_reason=post_ready_recheck_reason,
                post_ready_recheck_frames_checked=post_ready_recheck_frames_checked,
                post_ready_recheck_window_ms_actual=post_ready_recheck_window_ms_actual,
            ),
            frames=frames,
            frameTimestampsMs=timestamps_ms,
            frameCapturedAts=frame_captured_ats,
            streamType=session.streamType,
            streamUrl=session.streamUrl,
            readyFrameIndex=pending_candidate.frameIndex if pending_candidate is not None else None,
            confirmFrameIndex=None,
        )

    def evaluate_frame_quality(
        self,
        frame: np.ndarray,
        *,
        frame_index: int,
        captured_at: float,
        roi: RoiModel | None = None,
        previous_gray: np.ndarray | None = None,
        use_roi_for_stability: bool = True,
    ) -> tuple[FrameQualityEvaluation, np.ndarray]:
        focus_gray = self._prepare_grayscale(frame, roi=roi)
        stability_gray = (
            focus_gray
            if use_roi_for_stability
            else self._prepare_grayscale(frame, roi=None)
        )
        previous_stability_gray = previous_gray if use_roi_for_stability else previous_gray
        stability = (
            0.0
            if previous_stability_gray is None or previous_stability_gray.shape != stability_gray.shape
            else self._frame_delta(previous_stability_gray, stability_gray)
        )
        frame_quality = self._frame_quality(focus_gray)
        return (
            FrameQualityEvaluation(
                frameIndex=frame_index,
                capturedAt=captured_at,
                sharpness=frame_quality["robustScore"],
                gridMedian=frame_quality["gridMedian"],
                gridLowerQuantile=frame_quality["gridLowerQuantile"],
                clearCellRatio=frame_quality["clearCellRatio"],
                sharpCellCount=frame_quality["sharpCellCount"],
                totalCellCount=frame_quality["totalCellCount"],
                stability=stability,
            ),
            stability_gray,
        )

    def _build_metrics(
        self,
        *,
        ready: bool,
        reason: str,
        summary: dict[str, float | int | None],
        frames_checked: int,
        elapsed_ms: int,
        min_elapsed_gate_passed: bool,
        min_observe_gate_passed: bool,
        min_ready_window_gate_passed: bool,
        stable_blur_rejected: bool,
        continued_after_candidate_reject: bool,
        post_ready_recheck_passed: bool | None,
        post_ready_recheck_reason: str | None,
        post_ready_recheck_frames_checked: int,
        post_ready_recheck_window_ms_actual: int,
    ) -> VisualReadinessMetrics:
        return VisualReadinessMetrics(
            ready=ready,
            reason=reason,
            sharpnessMean=self._float_or_none(summary.get("sharpnessMean")),
            sharpnessMin=self._float_or_none(summary.get("sharpnessMin")),
            sharpnessRobustScore=self._float_or_none(summary.get("sharpnessRobustScore")),
            sharpnessGridMedian=self._float_or_none(summary.get("sharpnessGridMedian")),
            sharpnessGridLowerQuantile=self._float_or_none(summary.get("sharpnessGridLowerQuantile")),
            sharpCellRatio=self._float_or_none(summary.get("sharpCellRatio")),
            sharpCellCount=self._int_or_none(summary.get("sharpCellCount")),
            totalCellCount=self._int_or_none(summary.get("totalCellCount")),
            stabilityScore=self._float_or_none(summary.get("stabilityScore")),
            sharpnessTrend=self._float_or_none(summary.get("sharpnessTrend")),
            sharpnessImprovementRatio=self._float_or_none(summary.get("sharpnessImprovementRatio")),
            readyWindowMsActual=self._int_or_default(summary.get("readyWindowMsActual"), 0),
            minElapsedGatePassed=min_elapsed_gate_passed,
            minObserveGatePassed=min_observe_gate_passed,
            minReadyWindowGatePassed=min_ready_window_gate_passed,
            stableBlurRejected=stable_blur_rejected,
            continuedAfterCandidateReject=continued_after_candidate_reject,
            postReadyRecheckPassed=post_ready_recheck_passed,
            postReadyRecheckReason=post_ready_recheck_reason,
            postReadyRecheckFramesChecked=post_ready_recheck_frames_checked,
            postReadyRecheckWindowMsActual=post_ready_recheck_window_ms_actual,
            framesChecked=frames_checked,
            elapsedMs=elapsed_ms,
        )

    def _failure_reason(
        self,
        sharpness_mean: float | None,
        sharpness_min: float | None,
        stability_mean: float | None,
        *,
        min_elapsed_gate_passed: bool,
        min_observe_gate_passed: bool,
        min_ready_window_gate_passed: bool,
        stable_blur_rejected: bool,
    ) -> str:
        if not min_elapsed_gate_passed:
            return "visual_not_ready_min_elapsed"
        if not min_observe_gate_passed:
            return "visual_not_ready_min_observe"
        if not min_ready_window_gate_passed:
            return "visual_not_ready_ready_window_short"
        blurry = (
            sharpness_mean is None
            or sharpness_min is None
            or sharpness_mean < self.global_config.visualReadinessMinSharpness
            or sharpness_min < self.global_config.visualReadinessMinSharpness
        )
        unstable = stability_mean is not None and stability_mean > self.global_config.visualReadinessMaxStabilityScore
        if stable_blur_rejected or blurry:
            if unstable:
                return "visual_not_ready_blurry_and_unstable"
            return "visual_not_ready_blurry"
        if unstable:
            return "visual_not_ready_unstable"
        return "visual_not_ready_timeout"

    def _prepare_grayscale(
        self,
        frame: np.ndarray,
        *,
        roi: RoiModel | None = None,
    ) -> np.ndarray:
        grayscale = 0.114 * frame[..., 0] + 0.587 * frame[..., 1] + 0.299 * frame[..., 2]
        cropped = self._select_focus_region(grayscale, roi=roi)
        return self._downsample(cropped, self.global_config.visualReadinessDownsampleWidth)

    def _select_focus_region(
        self,
        grayscale: np.ndarray,
        *,
        roi: RoiModel | None = None,
    ) -> np.ndarray:
        if self.global_config.visualReadinessUseTargetRoi and roi is not None:
            roi_region = self._expanded_roi_crop(
                grayscale,
                roi=roi,
                expand_ratio=self.global_config.visualReadinessRoiExpandRatio,
            )
            if roi_region.size > 0:
                return self._center_crop(roi_region, self.global_config.visualReadinessRoiCoreRatio)
        return self._center_crop(grayscale, self.global_config.visualReadinessCropRatio)

    def _frame_quality(self, grayscale: np.ndarray) -> dict[str, float | int]:
        row_blocks = np.array_split(grayscale, self.global_config.visualReadinessGridRows, axis=0)
        cell_scores: list[float] = []
        for row_block in row_blocks:
            for cell in np.array_split(row_block, self.global_config.visualReadinessGridCols, axis=1):
                cell_scores.append(self._laplacian_variance(cell))

        scores = np.asarray(cell_scores, dtype=np.float32)
        if scores.size == 0:
            return {
                "robustScore": 0.0,
                "gridMedian": 0.0,
                "gridLowerQuantile": 0.0,
                "clearCellRatio": 0.0,
                "sharpCellCount": 0,
                "totalCellCount": 0,
            }

        lower_quantile = float(np.quantile(scores, self.global_config.visualReadinessGridLowerQuantile))
        grid_median = float(np.median(scores))
        sharp_cell_count = int(np.count_nonzero(scores >= self.global_config.visualReadinessMinSharpness))
        total_cell_count = int(scores.size)
        clear_cell_ratio = sharp_cell_count / max(total_cell_count, 1)
        return {
            "robustScore": grid_median,
            "gridMedian": grid_median,
            "gridLowerQuantile": lower_quantile,
            "clearCellRatio": clear_cell_ratio,
            "sharpCellCount": sharp_cell_count,
            "totalCellCount": total_cell_count,
        }

    def _window_summary(
        self,
        *,
        frame_history: list[FrameQualityEvaluation],
        sharpness_values: list[float],
        stability_values: list[float],
        ready_window: list[FrameQualityEvaluation],
    ) -> dict[str, float | int]:
        ready_sharpness = [item.sharpness for item in ready_window]
        ready_stability = [item.stability for item in ready_window[1:]] or [0.0]
        ready_window_ms = max(0, int(round((ready_window[-1].capturedAt - ready_window[0].capturedAt) * 1000)))
        baseline_count = min(len(sharpness_values), self.global_config.visualReadinessMinFrames)
        baseline_values = sharpness_values[:baseline_count] if baseline_count > 0 else []
        baseline_mean = float(np.mean(baseline_values)) if baseline_values else None
        baseline_min = float(np.min(baseline_values)) if baseline_values else None
        ready_mean = float(np.mean(ready_sharpness)) if ready_sharpness else None
        sharpness_trend = None
        sharpness_improvement_ratio = None
        if baseline_mean is not None and ready_mean is not None:
            sharpness_trend = ready_mean - baseline_mean
            sharpness_improvement_ratio = ready_mean / max(baseline_mean, 1e-6)

        latest = ready_window[-1]
        return {
            "baselineSharpnessMean": baseline_mean,
            "baselineSharpnessMin": baseline_min,
            "sharpnessMean": ready_mean,
            "sharpnessMin": float(np.min(ready_sharpness)) if ready_sharpness else 0.0,
            "sharpnessRobustScore": latest.sharpness,
            "sharpnessGridMedian": latest.gridMedian,
            "sharpnessGridLowerQuantile": latest.gridLowerQuantile,
            "sharpCellRatio": latest.clearCellRatio,
            "sharpCellCount": latest.sharpCellCount,
            "totalCellCount": latest.totalCellCount,
            "stabilityScore": float(np.mean(ready_stability)) if ready_stability else 0.0,
            "sharpnessTrend": sharpness_trend,
            "sharpnessImprovementRatio": sharpness_improvement_ratio,
            "readyWindowMsActual": ready_window_ms,
        }

    def _history_summary(
        self,
        frame_history: list[FrameQualityEvaluation],
        sharpness_values: list[float],
        stability_values: list[float],
        timestamps_ms: list[int],
        *,
        active_window: list[FrameQualityEvaluation] | None = None,
    ) -> dict[str, float | int | None]:
        if not frame_history:
            return {
                "sharpnessMean": None,
                "sharpnessMin": None,
                "sharpnessRobustScore": None,
                "sharpnessGridMedian": None,
                "sharpnessGridLowerQuantile": None,
                "sharpCellRatio": None,
                "sharpCellCount": None,
                "totalCellCount": None,
                "stabilityScore": None,
                "sharpnessTrend": None,
                "sharpnessImprovementRatio": None,
                "readyWindowMsActual": 0,
            }

        baseline_count = min(len(sharpness_values), self.global_config.visualReadinessMinFrames)
        baseline_mean = float(np.mean(sharpness_values[:baseline_count]))
        if active_window:
            recent_window = active_window
            recent_sharpness = [item.sharpness for item in recent_window]
            recent_mean = float(np.mean(recent_sharpness))
            ready_window_ms = max(0, int(round((recent_window[-1].capturedAt - recent_window[0].capturedAt) * 1000)))
            stability_score = (
                float(np.mean([item.stability for item in recent_window[1:]]))
                if len(recent_window) > 1
                else 0.0
            )
            latest = recent_window[-1]
        else:
            recent_count = min(len(frame_history), self.global_config.visualReadinessMinFrames)
            recent_window = frame_history[-recent_count:]
            recent_sharpness = [item.sharpness for item in recent_window]
            recent_mean = float(np.mean(recent_sharpness))
            ready_window_ms = (
                max(0, timestamps_ms[-1] - timestamps_ms[-recent_count]) if len(timestamps_ms) >= recent_count else 0
            )
            stability_score = float(np.mean(stability_values[-max(recent_count - 1, 1):])) if stability_values else None
            latest = recent_window[-1]

        return {
            "sharpnessMean": recent_mean,
            "sharpnessMin": float(np.min(recent_sharpness)) if recent_sharpness else None,
            "sharpnessRobustScore": latest.sharpness,
            "sharpnessGridMedian": latest.gridMedian,
            "sharpnessGridLowerQuantile": latest.gridLowerQuantile,
            "sharpCellRatio": latest.clearCellRatio,
            "sharpCellCount": latest.sharpCellCount,
            "totalCellCount": latest.totalCellCount,
            "stabilityScore": stability_score,
            "sharpnessTrend": recent_mean - baseline_mean,
            "sharpnessImprovementRatio": recent_mean / max(baseline_mean, 1e-6),
            "readyWindowMsActual": ready_window_ms,
        }

    def _converged(self, window_summary: dict[str, float | int | None]) -> bool:
        baseline_sharpness_mean = self._float_or_default(window_summary.get("baselineSharpnessMean"), 0.0)
        baseline_sharpness_min = self._float_or_default(window_summary.get("baselineSharpnessMin"), 0.0)
        sharpness_mean = self._float_or_default(window_summary.get("sharpnessMean"), 0.0)
        sharpness_min = self._float_or_default(window_summary.get("sharpnessMin"), 0.0)
        sharpness_robust_score = self._float_or_default(window_summary.get("sharpnessRobustScore"), 0.0)
        stability_score = self._float_or_default(window_summary.get("stabilityScore"), 1.0)
        sharpness_improvement_ratio = self._float_or_default(window_summary.get("sharpnessImprovementRatio"), 0.0)
        stable_high_sharpness = (
            self.global_config.visualReadinessMinSharpness
            * self.global_config.visualReadinessStableHighSharpnessMultiplier
        )
        sharpness_margin_threshold = (
            self.global_config.visualReadinessMinSharpness + self.global_config.visualReadinessMinSharpnessMargin
        )
        if sharpness_min < self.global_config.visualReadinessMinSharpness:
            return False
        if stability_score > self.global_config.visualReadinessMaxStabilityScore:
            return False
        if self.global_config.visualReadinessMinSharpnessMargin > 0:
            if sharpness_mean < sharpness_margin_threshold:
                return False
            if (
                self.global_config.visualReadinessRequireRobustScoreMargin
                and sharpness_robust_score < sharpness_margin_threshold
            ):
                return False
        if (
            baseline_sharpness_min >= self.global_config.visualReadinessMinSharpness
            and baseline_sharpness_mean >= self.global_config.visualReadinessMinSharpness
        ):
            return True
        return (
            sharpness_improvement_ratio >= self.global_config.visualReadinessMinImprovementRatio
            or sharpness_mean >= stable_high_sharpness
        )

    def _stable_blur_rejected(self, window_summary: dict[str, float | int | None]) -> bool:
        sharpness_mean = self._float_or_default(window_summary.get("sharpnessMean"), 0.0)
        sharpness_min = self._float_or_default(window_summary.get("sharpnessMin"), 0.0)
        sharpness_trend = self._float_or_default(window_summary.get("sharpnessTrend"), 0.0)
        sharpness_improvement_ratio = self._float_or_default(window_summary.get("sharpnessImprovementRatio"), 0.0)
        stability_score = self._float_or_default(window_summary.get("stabilityScore"), 1.0)
        stable_high_sharpness = (
            self.global_config.visualReadinessMinSharpness
            * self.global_config.visualReadinessStableHighSharpnessMultiplier
        )
        if stability_score > self.global_config.visualReadinessMaxStabilityScore:
            return False
        if sharpness_min >= self.global_config.visualReadinessMinSharpness:
            return False
        if sharpness_mean >= stable_high_sharpness:
            return False
        return (
            sharpness_improvement_ratio < self.global_config.visualReadinessMinImprovementRatio
            and sharpness_trend <= self.global_config.visualReadinessStableBlurMaxTrend
        )

    def _frame_passed(self, frame_stats: FrameQualityEvaluation) -> bool:
        return (
            frame_stats.sharpness >= self.global_config.visualReadinessMinSharpness
            and frame_stats.clearCellRatio >= self.global_config.visualReadinessMinSharpCellRatio
            and frame_stats.stability <= self.global_config.visualReadinessMaxStabilityScore
        )

    def _post_ready_recheck_enabled(self) -> bool:
        return self._active_post_ready_recheck_frames() > 0 or self._active_post_ready_recheck_window_ms() > 0

    def _confirmation_satisfied(
        self,
        confirmation_frames: list[FrameQualityEvaluation],
        pending_candidate: _PendingReadyCandidate,
    ) -> bool:
        if not confirmation_frames:
            return False
        frames_gate_passed = (
            len(confirmation_frames) >= self._active_post_ready_recheck_frames()
            if self._active_post_ready_recheck_frames() > 0
            else True
        )
        latest = confirmation_frames[-1]
        window_ms = max(0, int(round((latest.capturedAt - pending_candidate.capturedAt) * 1000)))
        window_gate_passed = (
            window_ms >= self._active_post_ready_recheck_window_ms()
            if self._active_post_ready_recheck_window_ms() > 0
            else True
        )
        return frames_gate_passed and window_gate_passed

    def _active_post_ready_recheck_frames(self) -> int:
        if (
            self.global_config.sceneMode == "night_ir"
            and self.global_config.visualReadinessNightPostReadyRecheckFrames > 0
        ):
            return self.global_config.visualReadinessNightPostReadyRecheckFrames
        return self.global_config.visualReadinessPostReadyRecheckFrames

    def _active_post_ready_recheck_window_ms(self) -> int:
        if (
            self.global_config.sceneMode == "night_ir"
            and self.global_config.visualReadinessNightPostReadyRecheckWindowMs > 0
        ):
            return self.global_config.visualReadinessNightPostReadyRecheckWindowMs
        return self.global_config.visualReadinessPostReadyRecheckWindowMs

    def _extended_recheck_deadline(
        self,
        *,
        now: float,
        deadline: float,
        current_extended_deadline: float | None,
    ) -> float | None:
        grace_ms = self.global_config.visualReadinessPostReadyRecheckGraceMs
        if grace_ms <= 0:
            return current_extended_deadline
        required_recheck_ms = max(
            self._active_post_ready_recheck_window_ms(),
            1 if self._active_post_ready_recheck_frames() > 0 else 0,
        )
        remaining_ms = max(0, int(round((deadline - now) * 1000)))
        if remaining_ms >= required_recheck_ms:
            return current_extended_deadline
        grace_deadline = deadline + (grace_ms / 1000.0)
        if current_extended_deadline is None:
            return grace_deadline
        return max(current_extended_deadline, grace_deadline)

    def _post_ready_recheck_failure_reason(self, frame_stats: FrameQualityEvaluation) -> str:
        blurry = frame_stats.sharpness < self.global_config.visualReadinessMinSharpness
        sparse = frame_stats.clearCellRatio < self.global_config.visualReadinessMinSharpCellRatio
        unstable = frame_stats.stability > self.global_config.visualReadinessMaxStabilityScore
        if blurry or sparse:
            if unstable:
                return "visual_post_ready_recheck_blurry_and_unstable"
            return "visual_post_ready_recheck_blurry"
        if unstable:
            return "visual_post_ready_recheck_unstable"
        return "visual_post_ready_recheck_failed"

    @staticmethod
    def _laplacian_variance(grayscale: np.ndarray) -> float:
        center = grayscale[1:-1, 1:-1]
        if center.size == 0:
            return 0.0
        laplacian = (
            grayscale[:-2, 1:-1]
            + grayscale[2:, 1:-1]
            + grayscale[1:-1, :-2]
            + grayscale[1:-1, 2:]
            - (4.0 * center)
        )
        return float(np.var(laplacian))

    @staticmethod
    def _frame_delta(previous: np.ndarray, current: np.ndarray) -> float:
        if previous.shape != current.shape:
            return 1.0
        return float(np.mean(np.abs(current - previous)) / 255.0)

    @staticmethod
    def _center_crop(image: np.ndarray, crop_ratio: float) -> np.ndarray:
        if crop_ratio >= 1.0:
            return image
        height, width = image.shape
        crop_height = max(1, int(round(height * crop_ratio)))
        crop_width = max(1, int(round(width * crop_ratio)))
        start_y = max(0, (height - crop_height) // 2)
        start_x = max(0, (width - crop_width) // 2)
        return image[start_y:start_y + crop_height, start_x:start_x + crop_width]

    @staticmethod
    def _expanded_roi_crop(
        image: np.ndarray,
        *,
        roi: RoiModel,
        expand_ratio: float,
    ) -> np.ndarray:
        height, width = image.shape
        expand_x = int(round(roi.width * expand_ratio))
        expand_y = int(round(roi.height * expand_ratio))
        start_x = max(0, roi.x - expand_x)
        start_y = max(0, roi.y - expand_y)
        end_x = min(width, roi.x + roi.width + expand_x)
        end_y = min(height, roi.y + roi.height + expand_y)
        if end_x <= start_x or end_y <= start_y:
            return image
        return image[start_y:end_y, start_x:end_x]

    @staticmethod
    def _downsample(image: np.ndarray, target_width: int) -> np.ndarray:
        height, width = image.shape
        if width <= target_width or target_width <= 0:
            return image.astype(np.float32)
        step = max(1, int(np.floor(width / target_width)))
        return image[::step, ::step].astype(np.float32)

    @staticmethod
    def _float_or_default(value: float | int | None, default: float) -> float:
        if value is None:
            return default
        return float(value)

    @staticmethod
    def _float_or_none(value: float | int | None) -> float | None:
        if value is None:
            return None
        return float(value)

    @staticmethod
    def _int_or_none(value: float | int | None) -> int | None:
        if value is None:
            return None
        return int(value)

    @staticmethod
    def _int_or_default(value: float | int | None, default: int) -> int:
        if value is None:
            return default
        return int(value)

    @staticmethod
    def _none_if_empty(values: list[float], reducer: object) -> float | None:
        if not values:
            return None
        return float(reducer(values))  # type: ignore[misc]
