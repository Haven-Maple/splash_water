from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from app.schemas.calibration import CalibrationRecord, RoiModel


ExecutionResult = Literal[
    "success",
    "preset_failed",
    "stream_failed",
    "insufficient_frames",
    "visual_not_ready",
    "visual_not_ready_timeout",
    "visual_blurry_before_detection",
    "scene_mode_probe_incomplete",
    "scene_mode_transition_timeout",
    "sample_quality_degraded",
    "sample_quality_timeout",
    "detect_error",
]
VisualState = Literal["has_splash", "no_splash", "undetermined"]
ReplaySaveStatus = Literal["disabled", "pending", "ready", "failed"]
ResolvedSceneMode = Literal["day_visible", "night_ir"]
SceneMode = Literal["auto", "day_visible", "night_ir"]
SceneModeClassification = Literal["day_visible", "night_ir", "ambiguous"]
SceneModeFallbackResolution = Literal["not_needed", "disabled", "agreed", "conflict"]
EffectiveSceneProfile = Literal["day_visible_normal", "day_visible_twilight", "night_ir"]


class RecognitionTarget(BaseModel):
    deviceId: str
    channelId: str
    presetIndex: int
    presetName: str
    targetId: str
    targetName: str
    roi: RoiModel
    focusAnchorRoi: RoiModel | None = None

    @classmethod
    def from_calibration(cls, record: CalibrationRecord) -> "RecognitionTarget":
        return cls(
            deviceId=record.deviceId,
            channelId=record.channelId,
            presetIndex=record.presetIndex,
            presetName=record.presetName,
            targetId=record.targetId,
            targetName=record.targetName,
            roi=record.roi,
            focusAnchorRoi=getattr(record, "focusAnchorRoi", None),
        )


class RecognitionScoreSummary(BaseModel):
    sceneMode: ResolvedSceneMode | None = None
    brightThresholdMean: float | None = None
    roiBrightnessQ99Mean: float | None = None
    roiBrightnessMaxMean: float | None = None
    localMotionScore: float | None = None
    dynamicAreaScore: float | None = None
    dynamicAreaRatio: float | None = None
    highlightMotionScore: float | None = None
    largestBrightComponentRatio: float | None = None
    brightComponentCount: float | None = None
    fragmentationScore: float | None = None
    centerBrightCoverage: float | None = None
    upperHalfBrightRatio: float | None = None
    lowerHalfBrightRatio: float | None = None
    verticalSpreadRatio: float | None = None
    gapFillRatio: float | None = None
    temporalAreaVariance: float | None = None
    temporalShapeVariance: float | None = None
    anyHardGatePassed: bool | None = None
    hardGatePassed: bool | None = None
    hardGatePassRatio: float | None = None
    hardGatePassCount: int | None = None
    dynamicEvidencePassCount: int | None = None
    largestBrightComponentPassCount: int | None = None
    centerBrightCoveragePassCount: int | None = None
    verticalSpreadPassCount: int | None = None
    continuousBrightPassCount: int | None = None
    gapFillPassCount: int | None = None
    hardGateMinGapFillRatioConfigured: float | None = None
    framePassRatio: float | None = None
    framePassCount: int | None = None
    sampledFrameCount: int | None = None
    targetFrameCount: int | None = None
    configuredSampleFps: float | None = None
    actualSampleFps: float | None = None
    configuredSampleDurationMs: int | None = None
    actualSampleDurationMs: int | None = None
    streamType: str | None = None
    alignmentApplied: bool | None = None
    globalMotionExceeded: bool | None = None
    overflowFrameCount: int | None = None
    meanGlobalShiftX: float | None = None
    meanGlobalShiftY: float | None = None
    maxGlobalShiftMagnitude: float | None = None
    maxAppliedShiftMagnitude: float | None = None
    overflowFrameRatio: float | None = None
    motionReductionRatio: float | None = None
    reliabilityGateTriggered: bool | None = None
    temporalVoteReason: str | None = None
    preAlignmentRoiMotionMean: float | None = None
    postAlignmentRoiMotionMean: float | None = None
    localMotionMean: float | None = None
    localMotionMax: float | None = None
    dynamicAreaMean: float | None = None
    dynamicAreaMax: float | None = None
    highlightMotionMean: float | None = None
    highlightMotionMax: float | None = None
    weightedFrameScoreMean: float | None = None
    weightedFrameScoreMax: float | None = None
    configuredFrameCount: int | None = None
    framePassThreshold: float | None = None
    sequenceVoteThreshold: float | None = None
    staticBrightInterferenceSuppressed: bool | None = None


class RecognitionEvidencePaths(BaseModel):
    calibrationPath: str | None = None
    snapshotPath: str | None = None
    snapshotUrl: str | None = None
    streamStartupStartFramePath: str | None = None
    streamStartupSettledFramePath: str | None = None
    sceneModeStabilityStartFramePath: str | None = None
    sceneModeStabilitySettledFramePath: str | None = None
    sceneProbeStartFramePath: str | None = None
    sceneProbeEndFramePath: str | None = None
    representativeFramePath: str | None = None
    visualReadinessStartFramePath: str | None = None
    visualReadinessReadyFramePath: str | None = None
    visualReadinessConfirmFramePath: str | None = None
    sampleStartFramePath: str | None = None
    sampleQualityAttemptStartFramePath: str | None = None
    sampleQualityDegradedFramePath: str | None = None
    sampleQualityLastQualifiedFramePath: str | None = None
    sampleQualityAcceptedMiddleFramePath: str | None = None
    sampleQualityAcceptedEndFramePath: str | None = None
    replaySequencePath: str | None = None
    replayMetadataPath: str | None = None
    debugImagePath: str | None = None
    recognitionConfigSnapshotPath: str | None = None


class SceneModeDiagnostics(BaseModel):
    classification: SceneModeClassification
    suggestedMode: ResolvedSceneMode
    inspectedFrameCount: int
    centerCropRatio: float
    colorfulnessMean: float
    saturationP90: float
    channelDeltaMean: float
    channelCorrelation: float
    brightnessMean: float
    brightnessStd: float
    dayVisibleScore: float
    nightIrScore: float
    scoreMargin: float


class VisualReadinessMetrics(BaseModel):
    ready: bool
    reason: str
    sharpnessMean: float | None = None
    sharpnessMin: float | None = None
    sharpnessRobustScore: float | None = None
    sharpnessGridMedian: float | None = None
    sharpnessGridLowerQuantile: float | None = None
    sharpCellRatio: float | None = None
    sharpCellCount: int | None = None
    totalCellCount: int | None = None
    stabilityScore: float | None = None
    sharpnessTrend: float | None = None
    sharpnessImprovementRatio: float | None = None
    readyWindowMsActual: int = 0
    minElapsedGatePassed: bool = False
    minObserveGatePassed: bool = False
    minReadyWindowGatePassed: bool = False
    stableBlurRejected: bool = False
    continuedAfterCandidateReject: bool = False
    postReadyRecheckPassed: bool | None = None
    postReadyRecheckReason: str | None = None
    postReadyRecheckFramesChecked: int = 0
    postReadyRecheckWindowMsActual: int = 0
    framesChecked: int = 0
    elapsedMs: int = 0


class SampleQualityMetrics(BaseModel):
    passed: bool
    reason: str
    recoveryCount: int = 0
    restartCount: int = 0
    qualifiedFramesCollected: int = 0
    acceptedFrameCount: int = 0
    rejectedFrames: int = 0
    reusedReadinessFrames: int = 0
    restartedDuringSampling: bool = False
    elapsedMs: int = 0
    sampleWindowMsActual: int = 0
    sampleWindowMaxAllowedMs: int = 0
    lastFailureReason: str | None = None
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
    windowTooLongRejected: bool = False
    windowTooLongCandidateFrameCount: int = 0
    windowTooLongTriggerSharpness: float | None = None
    windowTooLongTriggerClearCellRatio: float | None = None
    windowTooLongTriggerStability: float | None = None
    streamReadFailureReason: str | None = None
    streamReadFailureCount: int = 0
    sampleQualityStreamRecovered: bool = False
    sampleQualitySessionReopened: bool = False
    sampleQualityStreamRetryCount: int = 0


class StreamStartupFreshnessMetrics(BaseModel):
    enabled: bool
    consumedFrames: int = 0
    elapsedMs: int = 0
    jumpDetected: bool = False
    stableAfterJump: bool = False
    exitReason: str


class SceneModeStabilityMetrics(BaseModel):
    enabled: bool
    sceneModeInitial: ResolvedSceneMode | None = None
    sceneModeFinal: ResolvedSceneMode | None = None
    sceneModeStable: bool = False
    sceneModeStabilityElapsedMs: int = 0
    sceneModeStabilityWindowCount: int = 0
    sceneModeTransitionObserved: bool = False
    sceneModeRelockCount: int = 0
    sceneModeRelockReason: str | None = None
    sceneModeTransitionTimeout: bool = False


class RecognitionTiming(BaseModel):
    configLoadMs: int = 0
    presetTurnMs: int = 0
    settleWaitMs: int = 0
    visualReadinessMs: int = 0
    sampleMs: int = 0
    detectMs: int = 0
    totalMs: int = 0


class RecognitionRunResult(BaseModel):
    executionResult: ExecutionResult
    visualState: VisualState | None = None
    sceneMode: SceneMode
    requestedSceneMode: SceneMode
    effectiveSceneMode: ResolvedSceneMode | None = None
    effectiveSceneProfile: EffectiveSceneProfile | None = None
    sceneModeConfidence: float | None = None
    sceneModeReason: str | None = None
    sceneModeFallbackUsed: bool = False
    sceneModeDiagnostics: SceneModeDiagnostics | None = None
    twilightProfileApplied: bool | None = None
    twilightProfileReason: str | None = None
    twilightBrightnessMean: float | None = None
    focusAnchorRoiFallbackUsed: bool | None = None
    focusAnchorRoiSource: str | None = None
    dayVisibleVisualState: VisualState | None = None
    nightIrVisualState: VisualState | None = None
    fallbackResolution: SceneModeFallbackResolution | None = None
    streamStartupFreshness: StreamStartupFreshnessMetrics | None = None
    sceneModeStability: SceneModeStabilityMetrics | None = None
    visualReadinessPassed: bool | None = None
    visualReadinessReason: str | None = None
    visualReadiness: VisualReadinessMetrics | None = None
    sampleQualityPassed: bool | None = None
    sampleQualityReason: str | None = None
    sampleQualityMaxRecoveriesConfigured: int | None = None
    sampleQualityRecoveryCountSemantics: str | None = None
    sampleQuality: SampleQualityMetrics | None = None
    scoreSummary: RecognitionScoreSummary
    evidencePaths: RecognitionEvidencePaths
    replaySave: "ReplaySaveState"
    timing: RecognitionTiming
    algorithmVersion: str
    configPath: str
    target: RecognitionTarget
    message: str | None = None


class ReplaySaveState(BaseModel):
    status: ReplaySaveStatus
    statusPath: str | None = None
    message: str | None = None


@dataclass(slots=True)
class SampledSequence:
    streamType: str
    streamUrl: str
    frames: "object"
    frameTimestampsMs: list[int]
    targetFrameCount: int
    sampledFrameCount: int
    configuredSampleFps: float
    actualSampleFps: float
    configuredSampleDurationMs: int
    actualSampleDurationMs: int
    frameWidth: int
    frameHeight: int


@dataclass(slots=True)
class AlignedSequence:
    alignedFrames: "object"
    globalShifts: list[tuple[int, int]]
    shiftMagnitudes: list[float]
    appliedGlobalShifts: list[tuple[int, int]]
    appliedShiftMagnitudes: list[float]
    overflowFlags: list[bool]
    alignmentApplied: bool


@dataclass(slots=True)
class FrameFeature:
    frameIndex: int
    brightThreshold: float
    roiBrightnessQ99: float
    roiBrightnessMax: float
    localResidualMotion: float
    dynamicAreaRatio: float
    highlightDisturbance: float
    largestBrightComponentRatio: float
    brightComponentCount: int
    fragmentationScore: float
    centerBrightCoverage: float
    upperHalfBrightRatio: float
    lowerHalfBrightRatio: float
    verticalSpreadRatio: float
    gapFillRatio: float
    temporalAreaVariance: float
    temporalShapeVariance: float


@dataclass(slots=True)
class FrameScore:
    frameIndex: int
    dynamicEvidencePassed: bool
    hardGatePassed: bool
    localMotionComponent: float
    dynamicAreaComponent: float
    highlightMotionComponent: float
    largestBrightComponentComponent: float
    continuousBrightComponent: float
    centerCoverageComponent: float
    verticalSpreadComponent: float
    gapFillComponent: float
    temporalAreaVarianceComponent: float
    temporalShapeVarianceComponent: float
    weightedScore: float
    framePass: bool
