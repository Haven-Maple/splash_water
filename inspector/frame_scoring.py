from __future__ import annotations

from inspector.config import RecognitionGlobalConfig
from inspector.models import FrameFeature, FrameScore


class WeightedFrameScorer:
    def __init__(self, global_config: RecognitionGlobalConfig) -> None:
        self.global_config = global_config
        self.day_total_weight = (
            self.global_config.localMotionWeight
            + self.global_config.dynamicAreaWeight
            + self.global_config.highlightMotionWeight
            + self.global_config.largestBrightComponentWeight
            + self.global_config.continuousBrightWeight
            + self.global_config.centerBrightCoverageWeight
            + self.global_config.verticalSpreadWeight
        )
        self.night_total_weight = (
            self.global_config.highlightMotionWeight
            + self.global_config.largestBrightComponentWeight
            + self.global_config.continuousBrightWeight
            + self.global_config.centerBrightCoverageWeight
            + self.global_config.verticalSpreadWeight
            + self.global_config.gapFillWeight
            + self.global_config.temporalAreaVarianceWeight
            + self.global_config.temporalShapeVarianceWeight
        )

    def score(self, features: list[FrameFeature]) -> list[FrameScore]:
        scores: list[FrameScore] = []
        for feature in features:
            local_component = self._normalize(feature.localResidualMotion, self.global_config.localMotionFeatureScale)
            dynamic_component = self._normalize(feature.dynamicAreaRatio, self.global_config.dynamicAreaFeatureScale)
            highlight_component = self._normalize(
                feature.highlightDisturbance,
                self.global_config.highlightMotionFeatureScale,
            )
            largest_component = self._normalize(
                feature.largestBrightComponentRatio,
                self.global_config.largestBrightComponentFeatureScale,
            )
            continuous_component = self._normalize(
                max(0.0, 1.0 - feature.fragmentationScore),
                self.global_config.continuousBrightFeatureScale,
            )
            center_component = self._normalize(
                feature.centerBrightCoverage,
                self.global_config.centerBrightCoverageFeatureScale,
            )
            vertical_component = self._normalize(
                feature.verticalSpreadRatio,
                self.global_config.verticalSpreadFeatureScale,
            )
            gap_fill_component = self._normalize(
                feature.gapFillRatio,
                self.global_config.gapFillFeatureScale,
            )
            temporal_area_component = self._normalize(
                feature.temporalAreaVariance,
                self.global_config.temporalAreaVarianceFeatureScale,
            )
            temporal_shape_component = self._normalize(
                feature.temporalShapeVariance,
                self.global_config.temporalShapeVarianceFeatureScale,
            )

            if self.global_config.sceneMode == "night_ir":
                dynamic_evidence_passed = self._night_dynamic_evidence_passed(feature)
                hard_gate_passed = self._night_hard_gate_passed(
                    feature=feature,
                    continuous_bright_ratio=max(0.0, 1.0 - feature.fragmentationScore),
                    dynamic_evidence_passed=dynamic_evidence_passed,
                )
                weighted_score = 0.0
                if hard_gate_passed:
                    weighted_score = (
                        highlight_component * self.global_config.highlightMotionWeight
                        + largest_component * self.global_config.largestBrightComponentWeight
                        + continuous_component * self.global_config.continuousBrightWeight
                        + center_component * self.global_config.centerBrightCoverageWeight
                        + vertical_component * self.global_config.verticalSpreadWeight
                        + gap_fill_component * self.global_config.gapFillWeight
                        + temporal_area_component * self.global_config.temporalAreaVarianceWeight
                        + temporal_shape_component * self.global_config.temporalShapeVarianceWeight
                    ) / self.night_total_weight
            else:
                dynamic_evidence_passed = self._day_dynamic_evidence_passed(feature)
                hard_gate_passed = self._day_hard_gate_passed(
                    feature=feature,
                    continuous_bright_ratio=max(0.0, 1.0 - feature.fragmentationScore),
                    dynamic_evidence_passed=dynamic_evidence_passed,
                )
                weighted_score = 0.0
                if hard_gate_passed:
                    weighted_score = (
                        local_component * self.global_config.localMotionWeight
                        + dynamic_component * self.global_config.dynamicAreaWeight
                        + highlight_component * self.global_config.highlightMotionWeight
                        + largest_component * self.global_config.largestBrightComponentWeight
                        + continuous_component * self.global_config.continuousBrightWeight
                        + center_component * self.global_config.centerBrightCoverageWeight
                        + vertical_component * self.global_config.verticalSpreadWeight
                    ) / self.day_total_weight

            scores.append(
                FrameScore(
                    frameIndex=feature.frameIndex,
                    dynamicEvidencePassed=dynamic_evidence_passed,
                    hardGatePassed=hard_gate_passed,
                    localMotionComponent=local_component,
                    dynamicAreaComponent=dynamic_component,
                    highlightMotionComponent=highlight_component,
                    largestBrightComponentComponent=largest_component,
                    continuousBrightComponent=continuous_component,
                    centerCoverageComponent=center_component,
                    verticalSpreadComponent=vertical_component,
                    gapFillComponent=gap_fill_component,
                    temporalAreaVarianceComponent=temporal_area_component,
                    temporalShapeVarianceComponent=temporal_shape_component,
                    weightedScore=weighted_score,
                    framePass=hard_gate_passed and weighted_score >= self.global_config.framePassThreshold,
                )
            )
        return scores

    def _day_hard_gate_passed(
        self,
        *,
        feature: FrameFeature,
        continuous_bright_ratio: float,
        dynamic_evidence_passed: bool,
    ) -> bool:
        return (
            feature.largestBrightComponentRatio >= self.global_config.hardGateMinLargestBrightComponentRatio
            and feature.centerBrightCoverage >= self.global_config.hardGateMinCenterBrightCoverage
            and feature.verticalSpreadRatio >= self.global_config.hardGateMinVerticalSpreadRatio
            and continuous_bright_ratio >= self.global_config.hardGateMinContinuousBrightRatio
            and dynamic_evidence_passed
        )

    def _night_hard_gate_passed(
        self,
        *,
        feature: FrameFeature,
        continuous_bright_ratio: float,
        dynamic_evidence_passed: bool,
    ) -> bool:
        return (
            feature.largestBrightComponentRatio >= self.global_config.hardGateMinLargestBrightComponentRatio
            and feature.centerBrightCoverage >= self.global_config.hardGateMinCenterBrightCoverage
            and feature.verticalSpreadRatio >= self.global_config.hardGateMinVerticalSpreadRatio
            and continuous_bright_ratio >= self.global_config.hardGateMinContinuousBrightRatio
            and feature.gapFillRatio >= self.global_config.hardGateMinGapFillRatio
            and dynamic_evidence_passed
        )

    def _day_dynamic_evidence_passed(self, feature: FrameFeature) -> bool:
        return (
            feature.localResidualMotion >= self.global_config.hardGateMinLocalMotion
            or feature.dynamicAreaRatio >= self.global_config.hardGateMinDynamicAreaRatio
            or feature.highlightDisturbance >= self.global_config.hardGateMinHighlightMotion
        )

    def _night_dynamic_evidence_passed(self, feature: FrameFeature) -> bool:
        return (
            feature.localResidualMotion >= self.global_config.hardGateMinLocalMotion
            or feature.highlightDisturbance >= self.global_config.hardGateMinHighlightMotion
            or feature.temporalAreaVariance >= self.global_config.hardGateMinTemporalAreaVariance
            or feature.temporalShapeVariance >= self.global_config.hardGateMinTemporalShapeVariance
        )

    @staticmethod
    def _normalize(value: float, scale: float) -> float:
        return max(0.0, min(1.0, value / scale))
