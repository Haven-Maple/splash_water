# Night Visible / IR Transition Deferred Record

Date: 2026-07-14

## Status

Deferred deliberately. This is a documented field-environment risk, not an active recognition-algorithm defect to tune in the current single-aerator setup.

## Problem

At night, a camera can remain in color mode when one preset includes supplementary lighting and later switch to monochrome infrared after moving away from that light. The transition is camera-controlled and may be delayed or hysteretic:

- entering a darker target preset does not guarantee immediate IR switching
- returning to the lit preset does not guarantee immediate color switching
- the mode can change between recognition rounds rather than during the scene-stability probe

This makes a physical-time label such as "night" insufficient. The recognition path must use the actual current image profile.

## Confirmed Evidence

The following pseudo multi-point run alternated from preset 2 to recognition preset 1:

- [summary.json](C:/Users/Maple_Rain/Documents/Items/splash_water/data/pseudo_multi_point_tests/AB00A7DPAJ00124_1_p1_t2_no_splash_2026-07-13T17-35-37.416192+00-00/summary.json)
- [round_02.json](C:/Users/Maple_Rain/Documents/Items/splash_water/data/pseudo_multi_point_tests/AB00A7DPAJ00124_1_p1_t2_no_splash_2026-07-13T17-35-37.416192+00-00/round_02.json)
- [round_03.json](C:/Users/Maple_Rain/Documents/Items/splash_water/data/pseudo_multi_point_tests/AB00A7DPAJ00124_1_p1_t2_no_splash_2026-07-13T17-35-37.416192+00-00/round_03.json)

Observed pattern:

- rounds 1-2: stable color image, resolved as `day_visible_twilight`
- rounds 3-10: stable monochrome image, resolved as `night_ir`
- every individual round had `sceneModeStable=true` and `sceneModeTransitionObserved=false`

The switch therefore happened between rounds. This is useful evidence of slow mode response, but it is not a reproduction of an in-round transition.

Additional night-color samples show a distinct low-light color cluster:

- brightness mean: approximately 74-113
- colorfulness mean: approximately 16-20
- saturation P90: approximately 0.38-0.50
- channel delta mean: approximately 11-15
- channel correlation: approximately 0.97-0.98

The cluster is distinct from both reference daylight and true IR. It is suitable for later `night_visible` classifier work, but not yet for changing splash thresholds.

## Current Runtime Decision

Keep the current scene-stability guard enabled for `sceneMode=auto`:

1. Flush startup stream frames.
2. Require two complete, compatible scene-probe windows.
3. Use the resolved current profile for readiness and sampling.
4. On a blurry readiness failure, permit at most one scene re-lock.

When no profile change occurs during a round, selecting the image profile actually present at probe time is correct. The system must not force an expected profile merely because a different preset has supplementary lighting.

## Explicit Non-Changes While Deferred

- Do not add a fixed multi-second wait after every preset movement.
- Do not increase `sceneModeStabilityTimeoutMs` based on the current random observation.
- Do not lower readiness sharpness thresholds to make low-light color frames pass.
- Do not modify splash scoring, voting, white-foam suppression, or existing day/IR baselines.
- Do not introduce `night_visible` as an active detection profile until multi-aerator field samples include both `no_splash` and `has_splash` cases.

## Reopen Triggers

Resume this work when at least one condition is true:

- a multi-aerator environment has targets with materially different supplementary-light conditions
- an in-round color-to-IR or IR-to-color change is captured after the recognition preset is reached
- stable night-color targets become a routine production condition
- field failures show repeated `scene_mode_transition_timeout`, re-locks, or profile-dependent readiness failures

## Reopen Plan

Before changing recognition settings, build or run a dedicated scene-transition trace. It must not run readiness, sampling, or splash detection.

For each direction, record a full-frame scene decision every 200-300 ms:

1. Hold preset 2 for 0, 2, 5, 8, and 12 seconds.
2. Move to preset 1 and observe for 12 seconds.
3. Repeat the reverse direction.
4. Repeat each dwell duration at least three times.
5. Run one no-movement 30-second observation at each preset.

Persist for every window:

- preset identifier and elapsed time since movement completion
- resolved mode and confidence
- brightness, colorfulness, saturation, channel delta, and channel correlation
- full-frame evidence at trace start, every mode change, and trace end

This trace distinguishes the relevant causes:

- switching while stationary points to ambient-light threshold or exposure control
- switching only after a dwell interval points to camera hysteresis
- switching only after PTZ movement points to motion/focus/exposure coupling

## Future `night_visible` Candidate

Only after the trace and multi-aerator samples are available, consider a third image profile. Initial classifier candidates, derived from the current stable night-color sample cluster, are:

```json
{
  "nightVisibleMaxBrightnessMean": 118.0,
  "nightVisibleMinColorfulness": 14.0,
  "nightVisibleMaxColorfulness": 30.0,
  "nightVisibleMinSaturationP90": 0.32,
  "nightVisibleMinChannelDeltaMean": 9.5,
  "nightVisibleMaxChannelCorrelation": 0.982
}
```

The first `night_visible` profile should inherit the current `day_visible_twilight` readiness and sample-quality budgets. It must retain the current splash thresholds until separate positive and negative night-color validation exists.

## Acceptance Criteria For Reopening

- The trace captures at least one mode transition with timestamps and evidence frames.
- The expected transition direction and latency are measurable across repeated trials.
- `night_visible`, if enabled, is distinguishable from both stable daylight and stable IR with explicit diagnostics.
- No fixed waiting is added to stable runs.
- Existing `day_visible` and `night_ir` baselines remain unchanged and pass regression.
