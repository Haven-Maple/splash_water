from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.config import settings
from inspector.models import ResolvedSceneMode, SceneMode


class RecognitionGlobalConfig(BaseModel):
    sceneMode: SceneMode = "day_visible"
    sceneAutoFrameCount: int = Field(default=4, ge=1, le=10)
    sceneAutoCenterCropRatio: float = Field(default=0.6, gt=0, le=1)
    sceneAutoConfidenceThreshold: float = Field(default=0.68, ge=0, le=1)
    sceneAutoMinColorfulness: float = Field(default=12.0, ge=0)
    sceneAutoMinSaturationP90: float = Field(default=0.08, ge=0, le=1)
    sceneAutoMaxChannelDeltaForIr: float = Field(default=6.0, ge=0)
    sceneAutoMinChannelCorrelationForIr: float = Field(default=0.985, ge=0, le=1)
    sceneAutoUseDualPathFallback: bool = True
    sceneModeStabilityEnabled: bool = True
    sceneModeStabilityRequiredWindows: int = Field(default=2, ge=2, le=6)
    sceneModeStabilityFramesPerWindow: int = Field(default=4, ge=1, le=12)
    sceneModeStabilityTimeoutMs: int = Field(default=1600, ge=200, le=5000)
    sceneModeStabilityMaxBrightnessDelta: float = Field(default=18.0, ge=0)
    sceneModeStabilityMaxColorfulnessDelta: float = Field(default=6.0, ge=0)
    sceneModeStabilityMaxRelocks: int = Field(default=1, ge=0, le=4)
    sampleDurationMs: int = Field(default=2000, ge=200)
    sampleFps: int = Field(default=10, ge=1)
    sequenceFrameCount: int = Field(default=20, ge=1)
    presetTurnSettleMs: int = Field(default=1800, ge=0)
    streamCatchupMs: int = Field(default=1000, ge=0)
    streamOpenTimeoutMs: int = Field(default=6000, ge=500)
    frameReadTimeoutMs: int = Field(default=3000, ge=500)
    streamRecoveryMaxReopens: int = Field(default=2, ge=0, le=6)
    streamRecoveryBaseBackoffMs: int = Field(default=500, ge=0, le=5000)
    streamRecoveryBudgetMs: int = Field(default=10000, ge=1000, le=60000)
    streamStartupFreshnessEnabled: bool = True
    streamStartupFreshnessTimeoutMs: int = Field(default=800, ge=0, le=5000)
    streamStartupFreshnessJumpThreshold: float = Field(default=0.08, ge=0, le=1)
    streamStartupFreshnessStableThreshold: float = Field(default=0.025, ge=0, le=1)
    streamStartupFreshnessStableFrames: int = Field(default=2, ge=1, le=12)
    streamStartupFreshnessCropRatio: float = Field(default=0.9, gt=0, le=1)
    streamStartupFreshnessDownsampleWidth: int = Field(default=160, ge=32, le=512)
    visualReadinessEnabled: bool = True
    visualReadinessTimeoutMs: int = Field(default=3500, ge=500)
    visualReadinessMinElapsedMs: int = Field(default=500, ge=0)
    visualReadinessMinObserveMs: int = Field(default=0, ge=0)
    visualReadinessMinReadyWindowMs: int = Field(default=400, ge=0)
    visualReadinessMinFrames: int = Field(default=4, ge=2, le=12)
    visualReadinessMinSharpness: float = Field(default=300.0, ge=0)
    visualReadinessMinSharpnessMargin: float = Field(default=0.0, ge=0)
    visualReadinessRequireRobustScoreMargin: bool = False
    visualReadinessMinSharpCellRatio: float = Field(default=0.45, ge=0, le=1)
    visualReadinessMaxStabilityScore: float = Field(default=0.12, ge=0, le=1)
    visualReadinessUseTargetRoi: bool = True
    visualReadinessRoiExpandRatio: float = Field(default=0.4, ge=0, le=2)
    visualReadinessRoiCoreRatio: float = Field(default=0.7, gt=0, le=1)
    visualReadinessMinImprovementRatio: float = Field(default=1.2, ge=1.0)
    visualReadinessStableHighSharpnessMultiplier: float = Field(default=2.0, ge=1.0)
    visualReadinessStableBlurMaxTrend: float = Field(default=40.0, ge=0)
    visualReadinessCropRatio: float = Field(default=0.6, gt=0, le=1)
    visualReadinessDownsampleWidth: int = Field(default=192, ge=32, le=512)
    visualReadinessGridRows: int = Field(default=3, ge=1, le=8)
    visualReadinessGridCols: int = Field(default=3, ge=1, le=8)
    visualReadinessGridLowerQuantile: float = Field(default=0.25, ge=0, le=1)
    visualReadinessPostReadyRecheckFrames: int = Field(default=0, ge=0, le=12)
    visualReadinessPostReadyRecheckWindowMs: int = Field(default=0, ge=0, le=2000)
    visualReadinessPostReadyRecheckGraceMs: int = Field(default=400, ge=0, le=4000)
    visualReadinessNightPostReadyRecheckFrames: int = Field(default=0, ge=0, le=12)
    visualReadinessNightPostReadyRecheckWindowMs: int = Field(default=0, ge=0, le=2000)
    sampleQualityTimeoutMs: int = Field(default=4500, ge=500)
    sampleQualityMaxRecoveries: int = Field(default=2, ge=0, le=10)
    alignmentEnabled: bool = True
    alignmentDownsampleFactor: int = Field(default=2, ge=1)
    maxAlignmentShiftRatio: float = Field(default=0.08, gt=0, le=0.5)
    dynamicPixelThreshold: int = Field(default=18, ge=1, le=255)
    highlightPixelThreshold: int = Field(default=200, ge=1, le=255)
    nightBrightQuantile: float = Field(default=0.85, gt=0, lt=1)
    nightBrightStdMultiplier: float = Field(default=0.6, ge=0)
    nightBrightMinThreshold: float = Field(default=96.0, ge=0, le=255)
    nightBrightBlurRadius: int = Field(default=1, ge=0, le=5)
    nightRoiToleranceEnabled: bool = True
    nightRoiToleranceOffsetRatio: float = Field(default=0.08, ge=0, le=0.25)
    nightRoiToleranceExpandedScale: float = Field(default=1.1, ge=1.0, le=1.5)
    nightRoiToleranceMaxFullCandidates: int = Field(default=3, ge=1, le=18)
    brightComponentMinAreaRatio: float = Field(default=0.003, gt=0, le=0.2)
    localMotionFeatureScale: float = Field(default=0.03, gt=0)
    dynamicAreaFeatureScale: float = Field(default=0.08, gt=0)
    highlightMotionFeatureScale: float = Field(default=0.03, gt=0)
    largestBrightComponentFeatureScale: float = Field(default=0.12, gt=0)
    continuousBrightFeatureScale: float = Field(default=0.6, gt=0)
    centerBrightCoverageFeatureScale: float = Field(default=0.12, gt=0)
    verticalSpreadFeatureScale: float = Field(default=0.28, gt=0)
    gapFillFeatureScale: float = Field(default=0.7, gt=0)
    temporalAreaVarianceFeatureScale: float = Field(default=0.12, gt=0)
    temporalShapeVarianceFeatureScale: float = Field(default=0.45, gt=0)
    hardGateMinLargestBrightComponentRatio: float = Field(default=0.035, ge=0, le=1)
    hardGateMinCenterBrightCoverage: float = Field(default=0.04, ge=0, le=1)
    hardGateMinVerticalSpreadRatio: float = Field(default=0.18, ge=0, le=1)
    hardGateMinContinuousBrightRatio: float = Field(default=0.35, ge=0, le=1)
    hardGateMinLocalMotion: float = Field(default=0.018, ge=0, le=1)
    hardGateMinDynamicAreaRatio: float = Field(default=0.12, ge=0, le=1)
    hardGateMinHighlightMotion: float = Field(default=0.015, ge=0, le=1)
    hardGateMinGapFillRatio: float = Field(default=0.5, ge=0, le=1)
    hardGateMinTemporalAreaVariance: float = Field(default=0.04, ge=0, le=1)
    hardGateMinTemporalShapeVariance: float = Field(default=0.2, ge=0, le=1)
    localMotionWeight: float = Field(default=0.06, ge=0)
    dynamicAreaWeight: float = Field(default=0.04, ge=0)
    highlightMotionWeight: float = Field(default=0.12, ge=0)
    largestBrightComponentWeight: float = Field(default=0.32, ge=0)
    continuousBrightWeight: float = Field(default=0.12, ge=0)
    centerBrightCoverageWeight: float = Field(default=0.22, ge=0)
    verticalSpreadWeight: float = Field(default=0.12, ge=0)
    gapFillWeight: float = Field(default=0.18, ge=0)
    temporalAreaVarianceWeight: float = Field(default=0.18, ge=0)
    temporalShapeVarianceWeight: float = Field(default=0.16, ge=0)
    framePassThreshold: float = Field(default=0.6, ge=0, le=1)
    sequenceVoteThreshold: float = Field(default=0.6, ge=0.5, le=1)
    overflowFrameRatioThreshold: float = Field(default=0.5, ge=0, le=1)
    alignmentMotionReductionRatioThreshold: float = Field(default=0.15, ge=0, le=1)
    staticBrightSuppressionEnabled: bool = True
    staticBrightMinLargestBrightComponentRatio: float = Field(default=0.08, ge=0, le=1)
    staticBrightMinCenterBrightCoverage: float = Field(default=0.08, ge=0, le=1)
    staticBrightMaxHighlightMotionMean: float = Field(default=0.01, ge=0, le=1)
    staticBrightMaxTemporalAreaVariance: float = Field(default=0.03, ge=0, le=1)
    staticBrightMaxTemporalShapeVariance: float = Field(default=0.12, ge=0, le=1)
    staticBrightMiddleBandSuppressionEnabled: bool = False
    staticBrightMiddleBandMinPassRatio: float = Field(default=0.45, ge=0, le=1)
    saveReplayMaterials: bool = True
    replayAsyncSave: bool = True
    replayDirName: str = "recognition_replays"
    algorithmVersion: str = "phase-2-v1-step4-center-gate"

    @model_validator(mode="after")
    def validate_sequence_shape(self) -> "RecognitionGlobalConfig":
        expected_frame_count = round(self.sampleDurationMs / 1000 * self.sampleFps)
        if abs(self.sequenceFrameCount - expected_frame_count) > 1:
            raise ValueError(
                "sequenceFrameCount must stay aligned with sampleDurationMs * sampleFps "
                f"(expected about {expected_frame_count}, got {self.sequenceFrameCount})"
            )
        total_weight = (
            self.localMotionWeight
            + self.dynamicAreaWeight
            + self.highlightMotionWeight
            + self.largestBrightComponentWeight
            + self.continuousBrightWeight
            + self.centerBrightCoverageWeight
            + self.verticalSpreadWeight
        )
        night_weight = (
            self.highlightMotionWeight
            + self.largestBrightComponentWeight
            + self.continuousBrightWeight
            + self.centerBrightCoverageWeight
            + self.verticalSpreadWeight
            + self.gapFillWeight
            + self.temporalAreaVarianceWeight
            + self.temporalShapeVarianceWeight
        )
        if total_weight <= 0:
            raise ValueError("At least one daytime frame feature weight must be positive")
        if night_weight <= 0:
            raise ValueError("At least one night IR frame feature weight must be positive")
        return self

    def snapshot(self) -> dict[str, Any]:
        return self.model_dump()

    def summary(self) -> dict[str, Any]:
        return {
            "algorithmVersion": self.algorithmVersion,
            "sceneMode": self.sceneMode,
            "sceneAutoFrameCount": self.sceneAutoFrameCount,
            "sceneAutoCenterCropRatio": self.sceneAutoCenterCropRatio,
            "sceneAutoConfidenceThreshold": self.sceneAutoConfidenceThreshold,
            "sceneAutoMinColorfulness": self.sceneAutoMinColorfulness,
            "sceneAutoMinSaturationP90": self.sceneAutoMinSaturationP90,
            "sceneAutoMaxChannelDeltaForIr": self.sceneAutoMaxChannelDeltaForIr,
            "sceneAutoMinChannelCorrelationForIr": self.sceneAutoMinChannelCorrelationForIr,
            "sceneAutoUseDualPathFallback": self.sceneAutoUseDualPathFallback,
            "sceneModeStabilityEnabled": self.sceneModeStabilityEnabled,
            "sceneModeStabilityRequiredWindows": self.sceneModeStabilityRequiredWindows,
            "sceneModeStabilityFramesPerWindow": self.sceneModeStabilityFramesPerWindow,
            "sceneModeStabilityTimeoutMs": self.sceneModeStabilityTimeoutMs,
            "sceneModeStabilityMaxBrightnessDelta": self.sceneModeStabilityMaxBrightnessDelta,
            "sceneModeStabilityMaxColorfulnessDelta": self.sceneModeStabilityMaxColorfulnessDelta,
            "sceneModeStabilityMaxRelocks": self.sceneModeStabilityMaxRelocks,
            "sampleDurationMs": self.sampleDurationMs,
            "sampleFps": self.sampleFps,
            "sequenceFrameCount": self.sequenceFrameCount,
            "streamStartupFreshnessEnabled": self.streamStartupFreshnessEnabled,
            "streamStartupFreshnessTimeoutMs": self.streamStartupFreshnessTimeoutMs,
            "streamStartupFreshnessJumpThreshold": self.streamStartupFreshnessJumpThreshold,
            "streamStartupFreshnessStableThreshold": self.streamStartupFreshnessStableThreshold,
            "streamStartupFreshnessStableFrames": self.streamStartupFreshnessStableFrames,
            "streamStartupFreshnessCropRatio": self.streamStartupFreshnessCropRatio,
            "streamStartupFreshnessDownsampleWidth": self.streamStartupFreshnessDownsampleWidth,
            "visualReadinessEnabled": self.visualReadinessEnabled,
            "visualReadinessTimeoutMs": self.visualReadinessTimeoutMs,
            "visualReadinessMinElapsedMs": self.visualReadinessMinElapsedMs,
            "visualReadinessMinObserveMs": self.visualReadinessMinObserveMs,
            "visualReadinessMinReadyWindowMs": self.visualReadinessMinReadyWindowMs,
            "visualReadinessMinFrames": self.visualReadinessMinFrames,
            "visualReadinessMinSharpness": self.visualReadinessMinSharpness,
            "visualReadinessMinSharpnessMargin": self.visualReadinessMinSharpnessMargin,
            "visualReadinessRequireRobustScoreMargin": self.visualReadinessRequireRobustScoreMargin,
            "visualReadinessMinSharpCellRatio": self.visualReadinessMinSharpCellRatio,
            "visualReadinessMaxStabilityScore": self.visualReadinessMaxStabilityScore,
            "visualReadinessUseTargetRoi": self.visualReadinessUseTargetRoi,
            "visualReadinessRoiExpandRatio": self.visualReadinessRoiExpandRatio,
            "visualReadinessRoiCoreRatio": self.visualReadinessRoiCoreRatio,
            "visualReadinessMinImprovementRatio": self.visualReadinessMinImprovementRatio,
            "visualReadinessStableHighSharpnessMultiplier": self.visualReadinessStableHighSharpnessMultiplier,
            "visualReadinessStableBlurMaxTrend": self.visualReadinessStableBlurMaxTrend,
            "visualReadinessGridRows": self.visualReadinessGridRows,
            "visualReadinessGridCols": self.visualReadinessGridCols,
            "visualReadinessGridLowerQuantile": self.visualReadinessGridLowerQuantile,
            "visualReadinessPostReadyRecheckFrames": self.visualReadinessPostReadyRecheckFrames,
            "visualReadinessPostReadyRecheckWindowMs": self.visualReadinessPostReadyRecheckWindowMs,
            "visualReadinessPostReadyRecheckGraceMs": self.visualReadinessPostReadyRecheckGraceMs,
            "visualReadinessNightPostReadyRecheckFrames": self.visualReadinessNightPostReadyRecheckFrames,
            "visualReadinessNightPostReadyRecheckWindowMs": self.visualReadinessNightPostReadyRecheckWindowMs,
            "sampleQualityTimeoutMs": self.sampleQualityTimeoutMs,
            "sampleQualityMaxRecoveries": self.sampleQualityMaxRecoveries,
            "framePassThreshold": self.framePassThreshold,
            "sequenceVoteThreshold": self.sequenceVoteThreshold,
            "overflowFrameRatioThreshold": self.overflowFrameRatioThreshold,
            "alignmentMotionReductionRatioThreshold": self.alignmentMotionReductionRatioThreshold,
            "staticBrightSuppressionEnabled": self.staticBrightSuppressionEnabled,
            "staticBrightMinLargestBrightComponentRatio": self.staticBrightMinLargestBrightComponentRatio,
            "staticBrightMinCenterBrightCoverage": self.staticBrightMinCenterBrightCoverage,
            "staticBrightMaxHighlightMotionMean": self.staticBrightMaxHighlightMotionMean,
            "staticBrightMaxTemporalAreaVariance": self.staticBrightMaxTemporalAreaVariance,
            "staticBrightMaxTemporalShapeVariance": self.staticBrightMaxTemporalShapeVariance,
            "staticBrightMiddleBandSuppressionEnabled": self.staticBrightMiddleBandSuppressionEnabled,
            "staticBrightMiddleBandMinPassRatio": self.staticBrightMiddleBandMinPassRatio,
            "alignmentEnabled": self.alignmentEnabled,
            "maxAlignmentShiftRatio": self.maxAlignmentShiftRatio,
            "nightBrightQuantile": self.nightBrightQuantile,
            "nightBrightStdMultiplier": self.nightBrightStdMultiplier,
            "nightBrightMinThreshold": self.nightBrightMinThreshold,
            "nightBrightBlurRadius": self.nightBrightBlurRadius,
            "hardGateMinLargestBrightComponentRatio": self.hardGateMinLargestBrightComponentRatio,
            "hardGateMinCenterBrightCoverage": self.hardGateMinCenterBrightCoverage,
            "hardGateMinVerticalSpreadRatio": self.hardGateMinVerticalSpreadRatio,
            "hardGateMinContinuousBrightRatio": self.hardGateMinContinuousBrightRatio,
            "hardGateMinLocalMotion": self.hardGateMinLocalMotion,
            "hardGateMinDynamicAreaRatio": self.hardGateMinDynamicAreaRatio,
            "hardGateMinHighlightMotion": self.hardGateMinHighlightMotion,
            "hardGateMinGapFillRatio": self.hardGateMinGapFillRatio,
            "hardGateMinTemporalAreaVariance": self.hardGateMinTemporalAreaVariance,
            "hardGateMinTemporalShapeVariance": self.hardGateMinTemporalShapeVariance,
        }


def load_recognition_raw_config() -> dict[str, Any]:
    raw_config = settings.local_config.get("recognition_v1", {})
    if not isinstance(raw_config, dict):
        return {}
    return deepcopy(raw_config)


def load_recognition_config() -> RecognitionGlobalConfig:
    return build_recognition_config(load_recognition_raw_config())


def build_recognition_config(
    raw_config: dict[str, Any],
    scene_mode_override: ResolvedSceneMode | None = None,
) -> RecognitionGlobalConfig:
    return RecognitionGlobalConfig.model_validate(_resolve_scene_mode_config(raw_config, scene_mode_override))


def _resolve_scene_mode_config(
    raw_config: dict[str, Any],
    scene_mode_override: ResolvedSceneMode | None = None,
) -> dict[str, Any]:
    scene_mode = scene_mode_override or raw_config.get("sceneMode", "day_visible")
    if scene_mode not in {"auto", "day_visible", "night_ir"}:
        scene_mode = "day_visible"

    effective_config = {
        key: value
        for key, value in raw_config.items()
        if key not in {"dayVisible", "nightIr"}
    }
    if scene_mode in {"day_visible", "night_ir"}:
        profile_key = "dayVisible" if scene_mode == "day_visible" else "nightIr"
        profile_overrides = raw_config.get(profile_key, {})
        if isinstance(profile_overrides, dict):
            effective_config.update(profile_overrides)
    effective_config["sceneMode"] = scene_mode
    return effective_config


def resolve_config_path(raw_path: str) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate

    search_roots = [
        Path.cwd(),
        settings.workspace_root,
        settings.data_root,
        settings.calibration_dir,
    ]
    for root in search_roots:
        resolved = root / candidate
        if resolved.exists():
            return resolved

    return settings.workspace_root / candidate
