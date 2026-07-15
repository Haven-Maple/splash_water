# Auto Scene Mode Switching Checklist

Date: 2026-07-07

## Goal

Implement automatic switching between `day_visible` and `night_ir` without breaking the current frozen recognition baselines.

## 1. Extend The Scene Mode Contract

- Change `SceneMode` from:
  - `day_visible | night_ir`
- To:
  - `auto | day_visible | night_ir`

Files to update:

- [models.py](C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/models.py)
- [config.py](C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/config.py)
- [local_config.example.json](C:/Users/Maple_Rain/Documents/Items/splash_water/backend/local_config.example.json)

Acceptance:

- config accepts `sceneMode = auto`
- manual `day_visible` and `night_ir` still work unchanged

## 2. Add Scene Auto Config Knobs

Add global recognition config fields for scene classification:

- `sceneAutoFrameCount`
- `sceneAutoCenterCropRatio`
- `sceneAutoConfidenceThreshold`
- `sceneAutoMinColorfulness`
- `sceneAutoMinSaturationP90`
- `sceneAutoMaxChannelDeltaForIr`
- `sceneAutoMinChannelCorrelationForIr`
- `sceneAutoUseDualPathFallback`

Files to update:

- [config.py](C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/config.py)
- [local_config.example.json](C:/Users/Maple_Rain/Documents/Items/splash_water/backend/local_config.example.json)
- [local_config.json](C:/Users/Maple_Rain/Documents/Items/splash_water/backend/local_config.json)

Acceptance:

- config parses cleanly
- defaults are conservative

## 3. Add A Scene Classifier Module

Create a small dedicated module, for example:

- `inspector/scene_mode_resolver.py`

Responsibilities:

- inspect early sampled frames
- compute compact global scene statistics
- classify into:
  - `day_visible`
  - `night_ir`
  - `ambiguous`
- return confidence and diagnostics

Suggested diagnostics:

- `colorfulnessMean`
- `saturationP90`
- `channelDeltaMean`
- `channelCorrelation`
- `brightnessMean`
- `brightnessStd`

Acceptance:

- module is independent of splash scoring logic
- no day/night feature thresholds are mixed into this layer

## 4. Add Result And Metadata Fields

Extend result objects with scene-switch diagnostics.

Recommended fields:

- `requestedSceneMode`
- `effectiveSceneMode`
- `sceneModeConfidence`
- `sceneModeReason`
- `sceneModeFallbackUsed`
- `sceneModeDiagnostics`

Optional fallback diagnostics:

- `dayVisibleVisualState`
- `nightIrVisualState`
- `fallbackResolution`

Files to update:

- [models.py](C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/models.py)
- [run_once_service.py](C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/run_once_service.py)
- replay metadata writer path

Acceptance:

- result JSON clearly explains how mode was chosen
- replay metadata contains the same mode decision summary

## 5. Integrate Auto Mode Into `run_once`

In `run_once_service.py`:

- if requested mode is manual, keep current behavior
- if requested mode is `auto`:
  - sample sequence first
  - run scene classifier
  - choose effective mode
  - build effective config for that mode
  - run existing align + features + scoring + vote path

Important:

- do not duplicate the whole pipeline unnecessarily
- isolate only the mode decision step

Acceptance:

- clear day sample selects `day_visible`
- clear night sample selects `night_ir`

## 6. Add Ambiguous Dual-Path Fallback

If scene classifier confidence is below threshold:

- run recognition once as `day_visible`
- run recognition once as `night_ir`
- resolve final result:
  - same output -> adopt it
  - conflict -> `undetermined`

Acceptance:

- ambiguous scenes do not silently pick one side without trace
- fallback use is explicit in result and logs

## 7. Preserve Existing Baselines

Before changing defaults, replay the frozen baselines:

- [daytime-recognition-baseline.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/daytime-recognition-baseline.md)
- [night-ir-baseline.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/night-ir-baseline.md)

Minimum regression set:

- daytime positive and negative folders
- night positive and negative folders

Acceptance:

- daytime baseline remains acceptable
- night baseline remains acceptable

## 8. Logging Requirements

Add concise runtime log lines:

- requested scene mode
- effective scene mode
- confidence
- reason
- fallback used or not

Avoid noisy logs.

One compact summary line per run is enough.

## 9. Rollout Order

Strict recommended order:

1. extend config and models
2. add scene classifier module
3. integrate auto mode without fallback
4. verify clear day/night routing
5. add ambiguous dual-path fallback
6. replay frozen baselines
7. set local default to `auto` only after acceptance

## 10. Suggested Stop Rules

Stop and review before going further if:

- auto mode misroutes obvious night samples to day
- auto mode misroutes obvious day samples to night
- fallback triggers too often on clear scenes
- frozen day or night baseline regresses
- result JSON no longer clearly states why the mode was selected

## 11. Final Acceptance

This work is ready only when:

- one `run_once` command can run without manual day/night switching
- manual override still exists
- replay artifacts can explain every mode decision
- current day and night baselines remain valid
