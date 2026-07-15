# Auto Scene Mode Switching Design

Date: 2026-07-07

## Purpose

This document defines how phase-2 recognition should move from manual scene selection to automatic scene-mode switching.

Current situation:

- daytime visible recognition has a frozen baseline
- night IR recognition has a frozen baseline
- runtime still depends on manual `recognition_v1.sceneMode`

Goal:

- let one `run_once` command decide whether the current scene should run as `day_visible` or `night_ir`
- keep manual override available
- avoid accidental wrong-mode execution
- preserve replay traceability and diagnosability

## Design Conclusion

Automatic switching should not be implemented as a single "is this black-and-white?" check.

The correct direction is:

- a lightweight pre-recognition scene classifier
- based mainly on color / channel statistics
- with a confidence score
- and a conservative fallback path when the decision is not clear

This is safer than forcing a hard switch from only one signal.

## Why A Single Black-White Check Is Not Enough

Using only "gray image vs colored image" is too fragile because:

- daytime visible scenes can be low-saturation under cloud, haze, or muddy water
- some night IR frames may still carry weak pseudo-color or codec tint
- ROI alone is not a reliable color source because water and machine occupy most of the area
- one wrong mode choice affects the whole detection pass

So color is the main clue, but not the only clue.

## Core Runtime Decision

The requested mode should become three-state:

- `auto`
- `day_visible`
- `night_ir`

Runtime behavior:

- if manually set to `day_visible`, force daytime path
- if manually set to `night_ir`, force night path
- if set to `auto`, classify the sampled scene first, then choose the effective mode

This keeps field fallback simple.

## Recommended Runtime Chain

New runtime chain under `sceneMode = auto`:

1. turn preset
2. wait settle
3. sample short FLV sequence
4. run lightweight scene classification on early frames
5. decide `effectiveSceneMode`
6. run the existing alignment + feature extraction + scoring + temporal vote path using that mode
7. write result plus scene-classification diagnostics

This keeps the existing recognition body unchanged as much as possible.

## Scene Classification Layer

### Input scope

The classifier should use a few early sampled frames, not a single frame.

Recommended:

- first `3-5` usable frames from the sampled sequence
- use full frame or a center-cropped frame
- avoid relying only on ROI

Reason:

- scene mode is a camera-level property, not a target-only property
- ROI may be too small or too dominated by water reflections

### Primary features

The classifier should be driven mainly by these global frame statistics:

- `colorfulnessMean`
- `saturationP90`
- `channelDeltaMean`
- `channelCorrelation`

Interpretation:

- `day_visible` usually has more colorfulness and larger RGB channel difference
- `night_ir` usually behaves like near-grayscale replicated across channels

### Secondary features

Use only as supporting evidence:

- `brightnessMean`
- `brightnessStd`
- `highlightClipRatio`

These can help explain the scene, but they should not dominate the mode switch.

## Recommended Decision Logic

The classifier should output:

- `day_visible`
- `night_ir`
- `ambiguous`

with:

- `sceneModeConfidence`
- `sceneModeReason`
- compact diagnostics

Recommended rule:

- clearly colored -> `day_visible`
- clearly near-grayscale -> `night_ir`
- borderline -> `ambiguous`

The system should not pretend uncertainty does not exist.

## Fallback Strategy For Ambiguous Scenes

This is the most important safety rule.

If the scene classifier is not confident enough, do not hard-switch anyway.

Instead:

1. run the existing recognition path once as `day_visible`
2. run it once as `night_ir`
3. compare the final outputs

Decision:

- if both results agree, accept that result
- if they disagree, return `undetermined`

This prevents a bad auto-switch guess from silently turning into a false production result.

## Why Dual-Path Fallback Is Worth It

The extra compute cost only happens on ambiguous scenes.

That is a good tradeoff because:

- clear scenes stay cheap
- uncertain scenes stay safe
- current project priority is reliable field behavior, not shaving a few milliseconds at all costs

## Data Contract Changes

The result model should be extended so later debugging is straightforward.

Recommended additions:

- `requestedSceneMode`
- `effectiveSceneMode`
- `sceneModeConfidence`
- `sceneModeReason`
- `sceneModeDiagnostics`
- optional `sceneModeFallbackUsed`

If dual-path fallback is triggered, also record:

- `dayVisibleVisualState`
- `nightIrVisualState`
- `fallbackResolution`

## Config Strategy

Add auto-scene settings under `recognition_v1` as global config, not per preset.

Recommended config group:

- `sceneMode = auto | day_visible | night_ir`
- `sceneAutoMinColorfulness`
- `sceneAutoMinSaturationP90`
- `sceneAutoMaxChannelDeltaForIr`
- `sceneAutoMinChannelCorrelationForIr`
- `sceneAutoConfidenceThreshold`
- `sceneAutoUseDualPathFallback`
- `sceneAutoFrameCount`
- `sceneAutoCenterCropRatio`

Do not make this per-preset.

Scene recognition is global camera behavior.

## Logging Requirements

Every run should log:

- requested mode
- effective mode
- confidence
- classifier reason
- whether dual-path fallback was used

This should appear in:

- structured result JSON
- replay metadata
- runtime logger output

Without this, later field debugging will become guesswork.

## Rollout Strategy

The safest rollout order is:

1. add `auto` mode contract and logging only
2. implement scene classifier
3. keep manual override intact
4. add ambiguous dual-path fallback
5. validate on known day and night samples
6. only then flip local default from manual mode to `auto`

Do not flip the default first.

## Acceptance Standard

Auto scene switching is acceptable only if it satisfies all of these:

- clear daytime samples resolve to `effectiveSceneMode = day_visible`
- clear night IR samples resolve to `effectiveSceneMode = night_ir`
- unclear samples do not silently force a wrong mode
- current frozen daytime baseline does not regress
- current frozen night baseline does not regress
- result JSON and replay metadata always explain how the mode was chosen

## Non-Goals

This stage should not:

- invent a learned scene classifier
- introduce per-preset mode heuristics
- replace current day/night recognition bodies
- optimize for minimum latency before correctness is proven

## Final Recommendation

The right implementation is:

- `auto` scene mode
- color/channel-statistics classifier before recognition
- confidence-aware switching
- dual-path fallback for ambiguous scenes
- manual override retained permanently

This gives the project the convenience of one command, while keeping the current day and night baselines protected.
