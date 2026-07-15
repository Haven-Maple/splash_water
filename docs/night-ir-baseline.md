# Night IR Recognition Baseline

Date: 2026-07-06

## Purpose

This document freezes the current night infrared recognition baseline for phase-2 splash detection before automatic scene-mode switching is introduced.

The goal is to preserve:

- the currently validated night parameters
- the operational lessons that made night recall stable
- the latest acceptance result that should be used as the comparison point for later changes

## Scope

- Scenario: night infrared preview
- Target: one calibrated preset / one ROI / one aerator splash area
- Runtime path:
  - turn preset
  - wait settle
  - sample short FLV sequence
  - full-frame alignment
  - ROI frame feature extraction
  - frame hard gate + weighted scoring
  - temporal vote
- Current manual scene selection:
  - `recognition_v1.sceneMode = night_ir`
- Current algorithm version:
  - `phase-2-v1-night-relative-threshold`

## Final Night Direction

The accepted night path is now structure-dominant and dynamic-auxiliary.

Night IR does not treat "bright white exists" as the main signal.

Current recognition logic treats a true splash as:

- a dominant central bright mass inside the ROI
- a body that fills the former blade gaps instead of preserving separated bright bars
- sufficient vertical spread and center coverage
- at least one weak dynamic branch confirming the structure is not static

This direction is necessary because at night:

- aerator blades themselves are bright
- the image is blurrier and noisier than daytime
- static bars can look white without being splash

## Key Engineering Lesson

The biggest practical improvement in the latest night round was not only threshold tuning.

It was also ROI margin correction.

Observed field behavior:

- the aerator is rope-fixed but can still drift slightly
- splash body can shift a little relative to the original tight ROI
- a tight night ROI can clip the true splash body and depress structure features

Current accepted ROI from the latest validated samples:

- `x = 306`
- `y = 296`
- `width = 159`
- `height = 99`

Operational rule from now on:

- night ROI should not be tight to the visible body
- leave margin for small target drift and splash-body expansion

## Frozen Runtime Contract

### Frame level

A frame only enters scoring after the night hard gate passes.

The night hard gate requires:

1. Structure evidence
- dominant bright component large enough
- sufficient center coverage
- sufficient vertical spread
- sufficient continuity
- sufficient dark-gap filling

2. Dynamic evidence
- at least one weak dynamic branch over minimum:
  - `localResidualMotion`
  - `highlightDisturbance`
  - `temporalAreaVariance`
  - `temporalShapeVariance`

If the hard gate fails:

- `hardGatePassed = false`
- `weightedScore = 0`
- `framePass = false`

### Sequence level

- `framePassRatio` drives the temporal vote
- `passRatio >= sequenceVoteThreshold` -> `has_splash`
- `passRatio <= 1 - sequenceVoteThreshold` -> `no_splash`
- otherwise -> `undetermined`

## Frozen Night Parameters

These are the currently accepted night-specific overrides from [local_config.json](C:/Users/Maple_Rain/Documents/Items/splash_water/backend/local_config.json).

They should be treated as frozen until automatic scene switching is added and a new night regression exposes a real issue.

### Scene mode and version

- `sceneMode = night_ir`
- `algorithmVersion = phase-2-v1-night-relative-threshold`

### Night extraction

- `nightBrightQuantile = 0.82`
- `nightBrightStdMultiplier = 0.45`
- `nightBrightMinThreshold = 92`
- `nightBrightBlurRadius = 1`
- `brightComponentMinAreaRatio = 0.002`

### Night feature scales

- `largestBrightComponentFeatureScale = 0.18`
- `continuousBrightFeatureScale = 0.8`
- `centerBrightCoverageFeatureScale = 0.18`
- `verticalSpreadFeatureScale = 0.35`
- `gapFillFeatureScale = 0.85`
- `temporalAreaVarianceFeatureScale = 0.18`
- `temporalShapeVarianceFeatureScale = 0.55`

### Night hard gate minimums

- `hardGateMinLargestBrightComponentRatio = 0.25`
- `hardGateMinCenterBrightCoverage = 0.46`
- `hardGateMinVerticalSpreadRatio = 0.55`
- `hardGateMinContinuousBrightRatio = 0.6`
- `hardGateMinGapFillRatio = 0.86`
- `hardGateMinLocalMotion = 0.001`
- `hardGateMinHighlightMotion = 0.0005`
- `hardGateMinTemporalAreaVariance = 0.003`
- `hardGateMinTemporalShapeVariance = 0.01`

### Night weights

- `highlightMotionWeight = 0.12`
- `largestBrightComponentWeight = 0.24`
- `continuousBrightWeight = 0.1`
- `centerBrightCoverageWeight = 0.16`
- `verticalSpreadWeight = 0.08`
- `gapFillWeight = 0.18`
- `temporalAreaVarianceWeight = 0.18`
- `temporalShapeVarianceWeight = 0.14`

### Shared sequence thresholds still in effect

- `framePassThreshold = 0.6`
- `sequenceVoteThreshold = 0.6`
- `overflowFrameRatioThreshold = 0.5`
- `alignmentMotionReductionRatioThreshold = 0.15`

## Latest Acceptance Sample Set

Validated sample folders:

- positive samples: [run_once_result_night_has](C:/Users/Maple_Rain/Documents/Items/splash_water/run_once_result_night_has/)
- negative samples: [run_once_result_night_no](C:/Users/Maple_Rain/Documents/Items/splash_water/run_once_result_night_no/)

Current acceptance counts:

- positive samples: `40`
- negative samples: `10`

## Acceptance Result

### Positive set

- sample count: `40`
- result: `40 / 40` classified as `has_splash`
- all samples: `executionResult = success`
- observed current pattern:
  - `hardGatePassRatio` remains high
  - `framePassRatio` remains high
  - structure features clearly dominate the separation

Representative real positive snapshot from the latest set:

- `largestBrightComponentRatio = 0.4257`
- `centerBrightCoverage = 0.9597`
- `verticalSpreadRatio = 0.9187`
- `gapFillRatio = 0.9512`
- `temporalAreaVariance = 0.00548`
- `temporalShapeVariance = 0.03766`
- `framePassRatio = 0.95`
- `visualState = has_splash`

### Negative set

- sample count: `10`
- result: `10 / 10` classified as `no_splash`
- all samples: `executionResult = success`
- observed current pattern:
  - hard gate stays closed
  - center coverage remains very low or zero
  - preserved bar/gap structure blocks sequence pass

Representative real negative snapshot from the latest set:

- `largestBrightComponentRatio = 0.0928`
- `centerBrightCoverage = 0.0`
- `verticalSpreadRatio = 0.2525`
- `gapFillRatio = 0.6427`
- `temporalAreaVariance = 0.00172`
- `temporalShapeVariance = 0.00318`
- `framePassRatio = 0.0`
- `visualState = no_splash`

## Main Interpretation

The current night line is no longer failing at the extraction layer.

It is now separating night samples mainly through:

- dominant bright-mass structure
- center occupancy
- vertical spread
- gap filling

Dynamic evidence still matters, but it acts as a weak guard instead of the main body of proof.

This is the correct direction for night IR.

## Frozen Baseline Rule

Until automatic day/night switching or future difficult night regression exposes a real issue:

- do not casually retune the current night thresholds
- do not shrink the night ROI back to a tight box
- use this document as the reference point for:
  - automatic scene-mode switching
  - later windy-night regression
  - later noisy or low-contrast IR regression

## Deferred Night Risks

These remain intentionally deferred and should be collected as later regression samples:

- strong wind moving camera or splash body
- noisier or dimmer IR scenes
- partial splash or weak splash states
- unusual reflections or nearby lamp interference

## Next Step

Next priority is not more same-condition retuning.

Next priority is adding automatic scene recognition and switching while preserving:

- manual override capability
- replay traceability
- this frozen night baseline as the fallback comparison
