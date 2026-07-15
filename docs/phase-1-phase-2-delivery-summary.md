# Phase 1 And Phase 2 Delivery Summary

Date: 2026-07-08

## Purpose

This document consolidates the current deliverable state of:

- phase 1: the web calibration tool
- phase 2: the single-camera splash recognition runtime

It is intended to serve as a practical handoff and review document based on the actual code and frozen baselines already accepted in this repository.

## Overall Delivery Conclusion

The project has completed two usable stages:

1. Phase 1 established a usable remote calibration workflow for one camera / one preset / one ROI.
2. Phase 2 established a usable splash-recognition runtime for one camera, including:
   - daytime visible-light recognition
   - night infrared recognition
   - automatic scene-mode switching
   - pseudo multi-point repeatability testing

For the current project stage, phase 1 is considered operationally usable, and phase 2 is considered ready for delivery as the current single-camera recognition baseline.

## 2026-07-09 Addendum: Calibration Payload Slimming

Phase-1 calibration JSON has now been narrowed further so the saved file describes the preset target itself, not the operator-tool runtime knobs.

Current calibration JSON scope:

- `deviceId`
- `channelId`
- `targetId`
- `targetName`
- `presetIndex`
- `presetName`
- `roi`
- `notes`
- `snapshotPath`
- `snapshotUrl`
- `updatedAt`

Current runtime-config ownership:

- phase-1 page gate timing and visual-stability tuning:
  - backend `calibration_tool`
- phase-2 recognition settle knobs:
  - backend `recognition_v1`

Compatibility rule:

- old calibration JSON still loads
- legacy runtime fields are ignored on read
- new saves do not write those fields back

## Phase 1 Summary

## Goal

Phase 1 solves one specific problem:

- let an operator remotely connect to a Dahua camera
- preview the live image
- control PTZ and preset points
- freeze a usable frame
- draw the detection ROI
- save a calibration JSON that phase 2 can consume directly

This phase is a tooling phase, not the final recognition runtime.

## Main Components

Frontend:

- [C:\Users\Maple_Rain\Documents\Items\splash_water\frontend\src\pages\CalibrationPage.tsx](C:/Users/Maple_Rain/Documents/Items/splash_water/frontend/src/pages/CalibrationPage.tsx)
- [C:\Users\Maple_Rain\Documents\Items\splash_water\frontend\src\hooks\useStreamPlayer.ts](C:/Users/Maple_Rain/Documents/Items/splash_water/frontend/src/hooks/useStreamPlayer.ts)
- [C:\Users\Maple_Rain\Documents\Items\splash_water\frontend\src\hooks\useVisualStability.ts](C:/Users/Maple_Rain/Documents/Items/splash_water/frontend/src/hooks/useVisualStability.ts)

Backend:

- [C:\Users\Maple_Rain\Documents\Items\splash_water\backend\app\services\calibration_storage_service.py](C:/Users/Maple_Rain/Documents/Items/splash_water/backend/app/services/calibration_storage_service.py)

Supporting project records:

- [C:\Users\Maple_Rain\Documents\Items\splash_water\docs\calibration-tool-dev-log.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/calibration-tool-dev-log.md)
- [C:\Users\Maple_Rain\Documents\Items\splash_water\docs\calibration-tool-integration-notes.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/calibration-tool-integration-notes.md)
- [C:\Users\Maple_Rain\Documents\Items\splash_water\docs\calibration-tool-issues.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/calibration-tool-issues.md)

## Delivered Phase-1 Capability

The current calibration tool supports:

- device online check
- FLV preview, with HLS-compatible player path retained
- PTZ move control
- preset query, preset save, preset turn
- capture gating before freeze:
  - command accepted
  - mechanical settle
  - stream catch-up
  - full-frame visual stability check
  - ready-for-capture
- frozen-frame ROI drawing
- local calibration JSON save
- snapshot artifact save

The current calibration JSON is consumed directly by phase 2 through the storage layer, so calibration is not just UI state; it is part of the actual runtime contract.

## Phase-1 Important Design Decisions

### One ROI only

`stabilityRoi` was intentionally removed from the calibration flow.

The tool now only saves the recognition ROI. Visual stability still exists, but it samples the full preview frame instead of requiring a second manually maintained ROI.

### Capture gate retained

The calibration page still keeps a full capture gate because this protects snapshot quality:

- PTZ or preset command is accepted
- wait for mechanical settle
- wait for the stream to catch up
- verify the preview has visually stabilized
- only then allow freeze and ROI work

This reduces bad calibration caused by stream lag, short jitter, or PTZ tail motion.

### Preview stability favored over extreme low latency

The current preview player has been tuned toward stable usability:

- FLV recovery remains in place
- hard network failure can request a fresh stream URL
- common `waiting/stalled` events use lighter recovery first
- the final player tuning currently prefers a small live delay over frequent freezes

This means the preview is good enough for calibration use, but it is not claimed to be a perfect zero-latency monitor.

## Phase-1 Output Contract

The saved calibration record includes:

- device identity
- channel identity
- target identity
- preset index and preset name
- recognition ROI
- stream preference
- PTZ/preset settle timing
- stream catch-up and visual-stability parameters
- optional notes
- saved snapshot path

Storage behavior:

- new calibration JSONs no longer contain `stabilityRoi`
- old JSONs that still contain `stabilityRoi` are read compatibly and normalized on load

## Phase-1 Current Status

Phase 1 is considered complete enough for its intended role:

- operators can calibrate one camera / one preset / one ROI remotely
- the saved output is stable enough for phase 2 use
- remaining preview imperfections are non-blocking for the current delivery

## Phase-1 Known Boundaries

Phase 1 is not trying to solve:

- multi-camera operations
- long-running monitoring
- final production orchestration
- zero-latency perfect preview

Its job is to produce valid calibration data and support occasional recalibration when field conditions change.

## Phase 2 Summary

## Goal

Phase 2 solves a different problem from phase 1.

It is the backend recognition runtime that:

- consumes a saved calibration JSON
- turns to the target preset
- samples a short live sequence
- decides whether the ROI currently contains splash
- outputs a structured result and replay evidence

This runtime is designed to run independently from the calibration web tool, because the final recognition workload is intended for edge deployment while the calibration tool is only an occasional operator tool.

## Main Components

Recognition entry and orchestration:

- [C:\Users\Maple_Rain\Documents\Items\splash_water\inspector\run_once_service.py](C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/run_once_service.py)
- [C:\Users\Maple_Rain\Documents\Items\splash_water\inspector\config.py](C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/config.py)
- [C:\Users\Maple_Rain\Documents\Items\splash_water\inspector\models.py](C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/models.py)

Sequence processing:

- [C:\Users\Maple_Rain\Documents\Items\splash_water\inspector\flv_sampler.py](C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/flv_sampler.py)
- [C:\Users\Maple_Rain\Documents\Items\splash_water\inspector\frame_alignment.py](C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/frame_alignment.py)
- [C:\Users\Maple_Rain\Documents\Items\splash_water\inspector\frame_features.py](C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/frame_features.py)
- [C:\Users\Maple_Rain\Documents\Items\splash_water\inspector\frame_scoring.py](C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/frame_scoring.py)
- [C:\Users\Maple_Rain\Documents\Items\splash_water\inspector\temporal_voting.py](C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/temporal_voting.py)
- [C:\Users\Maple_Rain\Documents\Items\splash_water\inspector\scene_mode_resolver.py](C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/scene_mode_resolver.py)

Evidence and repeatability tools:

- [C:\Users\Maple_Rain\Documents\Items\splash_water\inspector\replay_store.py](C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/replay_store.py)
- [C:\Users\Maple_Rain\Documents\Items\splash_water\inspector\replay_worker.py](C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/replay_worker.py)
- [C:\Users\Maple_Rain\Documents\Items\splash_water\inspector\pseudo_multi_point_test.py](C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/pseudo_multi_point_test.py)

Reference docs:

- [C:\Users\Maple_Rain\Documents\Items\splash_water\docs\single-point-recognition-completion.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/single-point-recognition-completion.md)
- [C:\Users\Maple_Rain\Documents\Items\splash_water\docs\daytime-recognition-baseline.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/daytime-recognition-baseline.md)
- [C:\Users\Maple_Rain\Documents\Items\splash_water\docs\night-ir-baseline.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/night-ir-baseline.md)
- [C:\Users\Maple_Rain\Documents\Items\splash_water\docs\auto-scene-mode-switch-design.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/auto-scene-mode-switch-design.md)
- [C:\Users\Maple_Rain\Documents\Items\splash_water\docs\pseudo-multi-point-test-design.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/pseudo-multi-point-test-design.md)

## Delivered Phase-2 Capability

The current phase-2 runtime supports:

- single-point recognition CLI entry
- config consumption from phase-1 calibration JSON
- preset turn and settle wait
- remote FLV short-sequence sampling
- full-frame alignment before ROI feature extraction
- day/night split recognition logic
- auto scene-mode switching with conservative fallback
- structured JSON result output
- asynchronous replay material saving
- pseudo multi-point repeated switching test harness

## Phase-2 Runtime Chain

The actual execution order in `RunOnceService` is:

1. load and validate calibration JSON
2. verify the requested preset matches the calibration record
3. turn the camera to the calibrated preset
4. wait for:
   - preset settle
   - stream catch-up
5. sample a fixed short FLV sequence
6. resolve scene mode:
   - manual `day_visible`
   - manual `night_ir`
   - or `auto`
7. run one detection pass using the effective mode
8. make the final temporal vote
9. return structured result JSON immediately
10. save replay materials asynchronously in the background

The runtime is intentionally short-sequence based rather than single-frame based. That is a core design choice for reducing false positives.

## Phase-2 Algorithm Design

## 1. Short sequence sampling

Recognition does not rely on one snapshot.

It samples a fixed-length FLV sequence using global settings such as:

- `sampleDurationMs`
- `sampleFps`
- `sequenceFrameCount`

The purpose is to preserve small temporal evidence:

- residual motion
- shape continuity changes
- area changes
- later temporal voting

## 2. Full-frame alignment

Before ROI recognition, the runtime aligns frames at full-frame level.

Implementation:

- `FullFrameAligner` converts frames to grayscale
- estimates translation against the first frame by phase correlation
- applies bounded translation correction

Important engineering behavior:

- raw estimated shifts are preserved
- applied clamped shifts are preserved separately
- overflow frames are explicitly marked

This is important because wind or camera shake can create false local motion if the whole frame moves together.

The algorithm does not silently discard large shifts anymore. It preserves both:

- what the system estimated
- what correction was actually applied

## 3. ROI feature extraction

After alignment, recognition only analyzes the calibrated ROI.

The frame feature extractor computes:

Shared motion features:

- `localResidualMotion`
- `dynamicAreaRatio`
- `highlightDisturbance`

Shared structure features:

- `largestBrightComponentRatio`
- `brightComponentCount`
- `fragmentationScore`
- `centerBrightCoverage`
- `upperHalfBrightRatio`
- `lowerHalfBrightRatio`
- `verticalSpreadRatio`

Night-oriented structure and temporal features:

- `gapFillRatio`
- `temporalAreaVariance`
- `temporalShapeVariance`

### Why these features were chosen

The final feature set comes from the actual field problem:

Daytime false positives were mainly caused by:

- white glare
- water reflection
- ripple highlights

Night false positives were mainly caused by:

- bright aerator blades
- blur
- IR noise
- bar-like bright structure without true spray body

So the final design does not treat "bright pixels exist" as enough evidence.

It asks whether the ROI contains a central, continuous, vertically spread splash-like body, and whether that body is supported by at least weak dynamic evidence.

## 4. Bright-component-centered recognition

The core structure logic is built around bright connected components inside the ROI.

This is the most important phase-2 recognition decision.

The algorithm first extracts a bright mask, then connected components, then reasons about the dominant component.

For daytime:

- the bright threshold is more direct
- the goal is to detect a thick central white spray body

For night IR:

- the extractor uses a relative threshold inside the ROI
- the threshold is derived from ROI statistics after a light blur
- this avoids collapsing into all-zero structure on blurry IR frames

This night-specific branch was necessary because fixed bright thresholds were too brittle on real IR replay.

## 5. Hard gate before scoring

Frame scoring is not applied to every frame blindly.

Each frame must first pass a hard gate.

### Daytime hard gate

The daytime path requires:

- large enough dominant bright component
- enough center coverage
- enough vertical spread
- enough continuity
- at least one dynamic branch over minimum:
  - `localResidualMotion`
  - `dynamicAreaRatio`
  - `highlightDisturbance`

This is what blocks reflective water from passing only because it is bright and active.

### Night hard gate

The night path requires:

- large enough dominant bright component
- enough center coverage
- enough vertical spread
- enough continuity
- enough gap filling
- at least one weak dynamic branch over minimum:
  - `localResidualMotion`
  - `highlightDisturbance`
  - `temporalAreaVariance`
  - `temporalShapeVariance`

`gapFillRatio` is particularly important at night because true splash tends to fill the blade-gap structure, while no-splash IR frames often preserve separated bright bars.

If the hard gate fails:

- `weightedScore = 0`
- `framePass = false`

This keeps the later temporal vote from being polluted by frames that never formed a plausible splash body.

## 6. Weighted frame scoring

Only hard-gate-passed frames enter weighted scoring.

The scorer normalizes each feature by a feature scale, then combines them with configured weights.

Daytime scoring emphasizes:

- dominant bright component size
- center coverage
- continuity
- vertical spread

Dynamic features are still used, but they are not the main body of proof.

Night scoring emphasizes:

- dominant bright component size
- center coverage
- continuity
- gap filling
- temporal shape and area variation

This means both day and night paths are structure-dominant, but they use different structure clues because the images differ in nature.

## 7. Temporal voting

The recognition result is a sequence decision, not just the best frame decision.

The runtime counts how many sampled frames pass the frame-level criteria and computes:

- `framePassCount`
- `framePassRatio`
- `hardGatePassCount`
- `hardGatePassRatio`

Final vote:

- `passRatio >= sequenceVoteThreshold` -> `has_splash`
- `passRatio <= 1 - sequenceVoteThreshold` -> `no_splash`
- otherwise -> `undetermined`

This three-state result is intentional. The system does not force uncertain segments into a binary answer.

## 8. Reliability gating

Temporal voting is guarded by reliability checks.

The current reliability layer can suppress the result to `undetermined` when:

- too many frames exceeded alignment shift limits
- global motion exceeded limits and alignment did not reduce ROI motion enough

This is the protection against confusing global camera shake with splash motion.

## 9. Scene-mode switching

Phase 2 supports:

- manual `day_visible`
- manual `night_ir`
- `auto`

The auto resolver does not use a naive black-and-white test only.

It classifies a short early subset of the sequence using:

- `colorfulnessMean`
- `saturationP90`
- `channelDeltaMean`
- `channelCorrelation`
- plus supporting brightness statistics

Decision behavior:

- clear day -> `day_visible`
- clear IR -> `night_ir`
- ambiguous -> optional dual-path fallback

Dual-path fallback behavior:

- run daytime path
- run night path
- if both agree, accept the shared result
- if they disagree, return `undetermined`

This protects the system near day/night transition time and in other borderline scenes.

## 10. Evidence and replay

Each successful run can produce:

- structured result JSON
- replay sequence path
- replay metadata path
- representative frame path
- debug image path
- recognition-config snapshot path

Replay saving is asynchronous and status-driven:

- `pending`
- `ready`
- `failed`
- `disabled`

This preserves evidence without blocking the online recognition return path.

## Daytime Frozen Baseline

Reference:

- [C:\Users\Maple_Rain\Documents\Items\splash_water\docs\daytime-recognition-baseline.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/daytime-recognition-baseline.md)

Accepted preserved result:

- positive set: `10 / 10 has_splash`
- negative set: `10 / 10 no_splash`
- total: `20 / 20` correct

Current daytime algorithm version:

- `phase-2-v1-step4-center-gate`

Interpretation:

- daytime separation is now mainly coming from central continuous spray-body structure
- generic motion-only evidence is no longer the primary driver

## Night IR Frozen Baseline

Reference:

- [C:\Users\Maple_Rain\Documents\Items\splash_water\docs\night-ir-baseline.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/night-ir-baseline.md)

Accepted preserved result:

- positive set: `40 / 40 has_splash`
- negative set: `10 / 10 no_splash`
- total: `50 / 50` correct

Current night algorithm version:

- `phase-2-v1-night-relative-threshold`

Important operational lesson:

- night ROI must leave margin for small aerator drift and splash-body expansion

Interpretation:

- night recognition is now mainly separated by dominant central bright-mass structure plus gap filling
- weak dynamic evidence remains only a guard

## Auto Scene Switching Status

Auto switching is implemented in code and already part of the runtime capability.

Current behavior:

- manual override remains available permanently
- auto mode is available for one-command use
- ambiguous scenes can fall back conservatively instead of forcing a wrong mode

This is especially relevant for real field transitions between visible-light daytime and IR nighttime.

## Pseudo Multi-Point Test Status

Phase 2 also includes a pseudo multi-point scheduler test:

- [C:\Users\Maple_Rain\Documents\Items\splash_water\inspector\pseudo_multi_point_test.py](C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/pseudo_multi_point_test.py)

Its purpose is not to replace recognition.

Its purpose is to stress the orchestration pattern:

- move to transition preset 2
- wait a short transition settle
- call the existing `RunOnceService` for preset 1
- record per-round results and total summary
- repeat for multiple rounds

This validates:

- repeated preset leave-and-return behavior
- recognition trigger timing stability
- round cost and repeatability
- evidence traceability across repeated runs

The pseudo multi-point layer intentionally reuses the accepted single-point recognition body unchanged.

## Current Delivery Boundary

The current delivered recognition scope is:

- one camera
- one calibrated recognition preset
- one ROI
- one aerator target
- day visible and night IR
- optional auto scene switching
- repeatable single-point and pseudo multi-point testing tools

It is not yet claiming:

- final multi-camera orchestration
- database synchronization
- central management backend integration
- generalized model-based recognition across many camera geometries

## Residual Risks That Are Known But Not Blocking

These risks remain real, but they do not block the current stage delivery:

- difficult daytime glare at special times
- high-wind camera shake or chaotic water-surface disturbance
- unusual IR noise or weak-splash edge cases
- ambiguous scenes near day/night switching time
- preview latency on the calibration tool side

These should be treated as later regression topics, not as reasons to deny the current stage completion.

## Recommended Freeze Rule

For the current project stage:

- freeze the accepted daytime baseline unless a concrete regression appears
- freeze the accepted night IR baseline unless a concrete regression appears
- use the current phase-2 body as the reference runtime for later multi-point work
- avoid casual retuning that mixes algorithm change with orchestration change

## Final Delivery Statement

Phase 1 and phase 2 are now in a state suitable for current-stage delivery.

What can be claimed confidently:

- the project has a usable remote calibration workflow
- the calibration output is directly consumable by the recognition runtime
- the recognition runtime has working day and night lines
- the runtime has a concrete, traceable algorithm body rather than loose heuristics
- the runtime supports conservative auto scene switching
- the runtime has already crossed from one-off single-point execution into repeatability testing

The correct next-stage direction is no longer "keep tuning one point under the same condition".

The correct next-stage direction is:

- organize and deliver the frozen phase-1 / phase-2 baseline
- use that baseline as the body for later real multi-point orchestration and targeted regression testing
