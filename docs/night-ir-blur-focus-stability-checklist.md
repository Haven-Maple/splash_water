# Night IR Blur / Focus Stability Checklist

## 1. Goal

After the startup stale-frame issue was reduced, the main remaining risk is no longer "wrong target position at start".
It is now:

- the target is already aligned
- `visual_readiness` may pass on a borderline blurry frame
- sampling may then hit a short autofocus regression and fail in `sample_quality_*`

This round should focus on blur and focus stability only.

## 2. Confirmed Current State

Based on recent pseudo multi-point night `no_splash` runs:

- `visual-readiness-start` is now mostly aligned with the aerator target
- remaining failures are mostly `sample_quality_timeout` or `sample_quality_degraded`
- the failure samples are usually target-aligned but still visibly blurry
- this means the primary problem has shifted from startup stale frames to focus-quality stability

## 3. Non-Goals

This round should not:

- retune splash algorithm thresholds
- retune day/night scene classification
- enlarge `presetTurnSettleMs` again
- add device-specific foam suppression
- weaken `sample_quality` just to force more passes

## 4. Repair Direction

### 4.1 Tighten borderline-ready acceptance for night IR

- Revisit the night IR readiness pass condition when `sharpness` only barely clears the minimum.
- The current problem pattern is:
  - target aligned
  - sharpness just above threshold
  - sample phase still unstable
- Add a lightweight night-only safeguard so "barely above threshold" is not treated the same as "clearly sharp".

Recommended options:

- require a small sharpness margin above `visualReadinessMinSharpness` before immediate pass
- or enable a short post-ready recheck for `night_ir`
- or require the lower quantile / sharp cell ratio to stay healthy, not just the median

The key is to harden the pass decision without making readiness much slower.

### 4.2 Separate "aligned but blurry" from "aligned and stable"

- Keep using target ROI quality metrics.
- Add a clearer distinction between:
  - frame aligned but blurry
  - frame sharp enough but focus regresses shortly after
  - frame stably clear
- This should affect readiness reasoning and sample-quality reasoning, not splash classification.

### 4.3 Improve sample-quality failure semantics

- `sample_quality_timeout` is currently too broad for field review.
- Split it into more useful reasons such as:
  - `sample_quality_focus_regressed`
  - `sample_quality_blurry_after_ready`
  - `sample_quality_recovery_budget_exhausted`
  - `sample_quality_near_complete_but_broken`
- The goal is faster diagnosis from `round_*.json` without opening every replay first.

### 4.4 Preserve the "continuous good sequence" rule

- Do not relax guarded sampling just because failures now happen near frame `19/20`.
- If a single blur regression breaks the accepted window, that should remain a sampling failure.
- The fix should improve clarity stability, not hide instability.

## 5. Suggested Code Changes

### 5.1 Night-only readiness refinement

Touch points:

- [inspector/config.py](/C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/config.py)
- [backend/local_config.json](/C:/Users/Maple_Rain/Documents/Items/splash_water/backend/local_config.json)
- [backend/local_config.example.json](/C:/Users/Maple_Rain/Documents/Items/splash_water/backend/local_config.example.json)
- [inspector/visual_readiness.py](/C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/visual_readiness.py)

Candidate additions:

- `visualReadinessMinSharpnessMargin`
- `visualReadinessRequireRobustScoreMargin`
- `visualReadinessNightPostReadyRecheckFrames`
- `visualReadinessNightPostReadyRecheckWindowMs`

Keep these scene-aware so only `nightIr` can tighten them if needed.

### 5.2 Sample-quality reason refinement

Touch points:

- [inspector/run_once_service.py](/C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/run_once_service.py)
- [inspector/models.py](/C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/models.py)
- [inspector/pseudo_multi_point_test.py](/C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/pseudo_multi_point_test.py)

Add structured reason mapping so field JSON can distinguish:

- timeout because quality never fully recovered
- timeout after one late blur regression
- degraded because restart budget was exhausted

### 5.3 Optional evidence enrichment

If the accepted sequence fails late, keep at least:

- accepted attempt start
- the blur-regression frame
- the last qualified frame before failure

This is more useful than only knowing the final result string.

## 6. Tests To Add

- night IR readiness should not pass a target-aligned but borderline blurry frame without the new margin/recheck requirement
- night IR readiness should still pass a clearly sharp stable frame
- sample quality should report a more specific reason when a late blur frame breaks an almost-complete sequence
- pseudo multi-point summary should surface the new sample-quality failure reasons

## 7. Field Validation Order

1. Night `no_splash` 10 rounds
2. Check whether failures still happen at target-aligned but blurry starts
3. If stable, night `has_splash` 10 rounds
4. Compare:
   - `visual-readiness-start.ppm`
   - `visual-readiness-ready.ppm`
   - `sample-quality-attempt-start.ppm`
   - `sample-quality-degraded.ppm`

## 8. Acceptance Criteria

- target-aligned but visibly blurry frames are less likely to pass readiness immediately
- remaining failures are explained by more specific sample-quality reasons
- no regression in target alignment
- no splash baseline thresholds are changed in this round
