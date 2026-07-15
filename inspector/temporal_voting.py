from __future__ import annotations

from dataclasses import dataclass

from inspector.config import RecognitionGlobalConfig
from inspector.models import RecognitionScoreSummary, VisualState


@dataclass(slots=True)
class TemporalVoteDecision:
    visualState: VisualState
    passRatio: float
    overflowFrameRatio: float
    motionReductionRatio: float | None
    reliabilityGateTriggered: bool
    staticBrightInterferenceSuppressed: bool
    reason: str


class TemporalVoteResolver:
    def __init__(self, global_config: RecognitionGlobalConfig) -> None:
        self.global_config = global_config

    def resolve(self, summary: RecognitionScoreSummary) -> TemporalVoteDecision:
        sampled_frame_count = max(0, summary.sampledFrameCount or 0)
        frame_pass_count = max(0, summary.framePassCount or 0)
        pass_ratio = (frame_pass_count / sampled_frame_count) if sampled_frame_count > 0 else 0.0
        overflow_frame_count = max(0, summary.overflowFrameCount or 0)
        overflow_frame_ratio = (overflow_frame_count / sampled_frame_count) if sampled_frame_count > 0 else 0.0
        motion_reduction_ratio = self._motion_reduction_ratio(
            summary.preAlignmentRoiMotionMean,
            summary.postAlignmentRoiMotionMean,
        )

        if overflow_frame_ratio >= self.global_config.overflowFrameRatioThreshold:
            return TemporalVoteDecision(
                visualState="undetermined",
                passRatio=pass_ratio,
                overflowFrameRatio=overflow_frame_ratio,
                motionReductionRatio=motion_reduction_ratio,
                reliabilityGateTriggered=True,
                staticBrightInterferenceSuppressed=False,
                reason="overflow_ratio_gate",
            )

        if (
            summary.globalMotionExceeded
            and motion_reduction_ratio is not None
            and motion_reduction_ratio < self.global_config.alignmentMotionReductionRatioThreshold
        ):
            return TemporalVoteDecision(
                visualState="undetermined",
                passRatio=pass_ratio,
                overflowFrameRatio=overflow_frame_ratio,
                motionReductionRatio=motion_reduction_ratio,
                reliabilityGateTriggered=True,
                staticBrightInterferenceSuppressed=False,
                reason="alignment_reduction_gate",
            )

        if pass_ratio >= self.global_config.sequenceVoteThreshold and self._static_bright_interference(summary):
            return TemporalVoteDecision(
                visualState="no_splash",
                passRatio=pass_ratio,
                overflowFrameRatio=overflow_frame_ratio,
                motionReductionRatio=motion_reduction_ratio,
                reliabilityGateTriggered=True,
                staticBrightInterferenceSuppressed=True,
                reason="static_bright_interference_gate",
            )

        if self._static_bright_middle_band_interference(summary, pass_ratio):
            return TemporalVoteDecision(
                visualState="no_splash",
                passRatio=pass_ratio,
                overflowFrameRatio=overflow_frame_ratio,
                motionReductionRatio=motion_reduction_ratio,
                reliabilityGateTriggered=True,
                staticBrightInterferenceSuppressed=True,
                reason="static_bright_interference_middle_band_gate",
            )

        if pass_ratio >= self.global_config.sequenceVoteThreshold:
            return TemporalVoteDecision(
                visualState="has_splash",
                passRatio=pass_ratio,
                overflowFrameRatio=overflow_frame_ratio,
                motionReductionRatio=motion_reduction_ratio,
                reliabilityGateTriggered=False,
                staticBrightInterferenceSuppressed=False,
                reason="pass_ratio_high",
            )

        if pass_ratio <= 1.0 - self.global_config.sequenceVoteThreshold:
            return TemporalVoteDecision(
                visualState="no_splash",
                passRatio=pass_ratio,
                overflowFrameRatio=overflow_frame_ratio,
                motionReductionRatio=motion_reduction_ratio,
                reliabilityGateTriggered=False,
                staticBrightInterferenceSuppressed=False,
                reason="pass_ratio_low",
            )

        return TemporalVoteDecision(
            visualState="undetermined",
            passRatio=pass_ratio,
            overflowFrameRatio=overflow_frame_ratio,
            motionReductionRatio=motion_reduction_ratio,
            reliabilityGateTriggered=False,
            staticBrightInterferenceSuppressed=False,
            reason="pass_ratio_middle_band",
        )

    def _static_bright_interference(self, summary: RecognitionScoreSummary) -> bool:
        if not self._static_bright_suppression_applies(summary):
            return False
        if not self.global_config.staticBrightSuppressionEnabled:
            return False
        return (
            (summary.largestBrightComponentRatio or 0.0) >= self.global_config.staticBrightMinLargestBrightComponentRatio
            and (summary.centerBrightCoverage or 0.0) >= self.global_config.staticBrightMinCenterBrightCoverage
            and (summary.highlightMotionMean or 0.0) <= self.global_config.staticBrightMaxHighlightMotionMean
            and (summary.temporalAreaVariance or 0.0) <= self.global_config.staticBrightMaxTemporalAreaVariance
            and (summary.temporalShapeVariance or 0.0) <= self.global_config.staticBrightMaxTemporalShapeVariance
        )

    def _static_bright_middle_band_interference(self, summary: RecognitionScoreSummary, pass_ratio: float) -> bool:
        if not self.global_config.staticBrightMiddleBandSuppressionEnabled:
            return False
        middle_band_floor = max(
            self.global_config.staticBrightMiddleBandMinPassRatio,
            1.0 - self.global_config.sequenceVoteThreshold,
        )
        if pass_ratio < middle_band_floor or pass_ratio >= self.global_config.sequenceVoteThreshold:
            return False
        return self._static_bright_interference(summary)

    def _static_bright_suppression_applies(self, summary: RecognitionScoreSummary) -> bool:
        scene_mode = summary.sceneMode or self.global_config.sceneMode
        return scene_mode == "day_visible"

    @staticmethod
    def _motion_reduction_ratio(pre_alignment_motion: float | None, post_alignment_motion: float | None) -> float | None:
        if pre_alignment_motion is None or post_alignment_motion is None:
            return None
        baseline = max(pre_alignment_motion, 1e-9)
        return (pre_alignment_motion - post_alignment_motion) / baseline
