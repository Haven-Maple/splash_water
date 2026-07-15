# Daytime Recognition Baseline

Date: 2026-07-05

## Purpose

This document freezes the current daytime recognition baseline for phase-2 splash detection so later night-IR work and future difficult-weather regression can compare against a stable reference.

## Scope

- Scenario: daytime visible-light preview
- Target: one calibrated preset / one ROI / one aerator splash area
- Runtime path:
  - turn preset
  - wait settle
  - sample short FLV sequence
  - full-frame alignment
  - ROI frame feature extraction
  - frame hard gate + weighted scoring
  - temporal vote
- Current algorithm version: `phase-2-v1-step4-center-gate`

## Final Daytime Direction

The daytime path no longer relies on generic "bright + moving" evidence.

Current recognition logic treats a true splash as:

- a large bright mass near the ROI center
- visually continuous instead of fragmented
- vertically spread like spray rather than water-surface glare
- dynamic in at least one branch:
  - local residual motion
  - dynamic area ratio
  - highlight disturbance

This redesign was necessary because daytime water glare and ripples can also be bright and dynamic, but they usually do not form a thick central continuous white spray body.

## Key Recognition Contract

### Frame level

A frame only enters scoring after the hard gate passes.

The hard gate now requires both:

1. Structure evidence
- sufficiently large bright component
- sufficient center coverage
- sufficient vertical spread
- sufficient continuity

2. Dynamic evidence
- `localResidualMotion >= hardGateMinLocalMotion`
  or
- `dynamicAreaRatio >= hardGateMinDynamicAreaRatio`
  or
- `highlightDisturbance >= hardGateMinHighlightMotion`

If the hard gate fails:

- `hardGatePassed = false`
- `weightedScore = 0`
- `framePass = false`

### Sequence level

- `anyHardGatePassed`: whether any frame passed the hard gate
- `hardGatePassed`: whether `hardGatePassRatio >= sequenceVoteThreshold`
- `framePassRatio`: final temporal-vote input
- final `visualState`:
  - `has_splash`
  - `no_splash`
  - `undetermined`

## Baseline Config Intent

Current defaults intentionally bias toward geometric splash evidence:

- lower trust in generic motion-only signals
- higher trust in central bright-mass shape
- dynamic evidence used as a required guard, not the main scoring body

This is important because some no-splash daytime samples still show non-trivial:

- `dynamicAreaRatio`
- `highlightMotionMean`

but they no longer pass once the central bright-mass gate is enforced.

## Baseline Sample Set

Latest acceptance sample set:

- positive samples: [run_once_result_has](C:/Users/Maple_Rain/Documents/Items/splash_water/run_once_result_has/)
- negative samples: [run_once_result_no](C:/Users/Maple_Rain/Documents/Items/splash_water/run_once_result_no/)

Both folders contain the latest 10-sample results after old results were cleared.

## Acceptance Result

### Positive set

- sample count: 10
- result: `10 / 10` classified as `has_splash`
- all samples: `executionResult = success`
- all samples: `hardGatePassRatio = 0.95`
- all samples: `framePassRatio = 0.95`
- weighted frame score mean: about `0.92 - 0.94`

Observed feature range:

- `largestBrightComponentRatio`: about `0.677 - 0.764`
- `centerBrightCoverage`: about `0.881 - 0.992`
- `highlightMotionMean`: about `0.0215 - 0.0276`
- `dynamicAreaRatio`: about `0.171 - 0.204`

### Negative set

- sample count: 10
- result: `10 / 10` classified as `no_splash`
- all samples: `executionResult = success`
- all samples: `hardGatePassRatio = 0`
- all samples: `framePassRatio = 0`
- all samples: `weightedFrameScoreMean = 0`

Observed feature range:

- `largestBrightComponentRatio`: about `0.0093 - 0.0161`
- `centerBrightCoverage`: about `0.0039 - 0.0437`
- `highlightMotionMean`: about `0.0091 - 0.0447`
- `dynamicAreaRatio`: about `0.135 - 0.290`

## Main Interpretation

The current daytime separation is not coming from motion alone.

In the negative set, `dynamicAreaRatio` and `highlightMotionMean` are sometimes still noticeable, but the sequence remains blocked because the ROI does not form a large central continuous spray-like white body.

This is the correct direction for daytime robustness.

## Notes For Later Review

- A positive set `hardGatePassRatio = 0.95` is currently acceptable and expected.
- In practice this usually means one frame in the sampled sequence does not pass, often because the first frame has no true previous frame for dynamic comparison.
- Later tuning should not blindly try to force daytime positives from `0.95` to `1.0` unless there is a clear error mechanism.

## Frozen Daytime Baseline

Until difficult daytime regression exposes a real issue, treat the current daytime line as frozen:

- do not retune daytime defaults just to chase more same-weather samples
- use this baseline as the comparison point for:
  - night IR recognition design
  - midday strong-glare regression
  - high-wind / chaotic ripple regression

## Deferred Daytime Risks

The following are intentionally deferred to later targeted regression, not current blocking items:

- midday strong reflection
- high wind shaking camera or water surface
- chaotic ripple / mixed reflective water
- special weather and temporary field anomalies

These should be collected as future regression samples and compared against this baseline instead of blocking night-IR work now.

## Recommended Next Step

Next priority is night infrared recognition design using the same overall pipeline:

- keep preset + ROI + sequence sampling architecture
- keep alignment + frame gate + temporal vote structure
- retune the feature emphasis for IR scenes only after collecting a small clear night sample set

