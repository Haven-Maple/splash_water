# Sample Quality Guard Checklist

## 1. Problem Statement

Current `visual_readiness` only guards the period before formal sampling starts.
Once readiness passes, [run_once_service.py](/C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/run_once_service.py:288) immediately calls `sample_from_session(session)` and collects a fixed-length sequence.

This leaves a real gap:

- the image may become sharp, pass readiness, and then briefly lose focus again during formal sampling
- the image may pass readiness near the timeout edge, but the following 2-second sequence may still include blur
- adding more fixed settle time can increase average round time, but still cannot guarantee autofocus convergence

So the next repair target should be:

`readiness before sampling` -> `quality-controlled continuous sampling`

## 2. Repair Goal

Keep the existing splash algorithm baseline unchanged, but make sure the sequence handed to the recognizer is a complete, continuously qualified sequence.

The core rule is:

- do not accept a sequence just because readiness passed once
- only accept a full sampling window that stays qualified during sampling
- if focus is lost mid-sampling, discard the incomplete window and recover inside the same live session

## 3. Non-Goals

This round should not mix in the following changes:

- do not retune day/night splash thresholds
- do not add device-specific foam suppression rules
- do not solve the issue by blindly increasing `presetTurnSettleMs`
- do not add a second recognition algorithm path outside `RunOnceService`

## 4. Implementation Checklist

### 4.1 Extract shared frame-quality judgment

- Refactor [visual_readiness.py](/C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/visual_readiness.py:404) so the current ROI-based quality judgment can be reused after readiness passes.
- Reuse the same inputs already proven useful:
  - ROI-focused crop
  - grid-based robust sharpness
  - sharp cell ratio
  - stability score
- Avoid creating a second unrelated quality scoring method for sampling.

### 4.2 Add quality-controlled sampling after readiness

- Replace the direct fixed blind sampling at [run_once_service.py](/C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/run_once_service.py:288) with a new guarded sampling path.
- The guarded sampler should:
  - stay in the same live `session`
  - start collecting frames immediately after readiness confirmation
  - keep only frames that continue to satisfy sample-quality conditions
  - require a complete continuous window of `sequenceFrameCount`
- If a frame fails quality during collection:
  - discard the incomplete candidate sequence
  - remain in the same session
  - return to a recovery state and wait for quality to recover
  - restart collection from scratch instead of stitching old and new fragments

### 4.3 Add bounded recovery instead of fixed waiting

- Add a total quality-sampling budget, for example a separate `sampleQualityTimeoutMs`.
- Add a bounded restart count, for example `sampleQualityMaxRecoveries`.
- If recovery budget is exhausted, return `undetermined` instead of endlessly waiting.
- This keeps average runtime under control while still handling short autofocus oscillations.

### 4.4 Keep readiness and sampling tightly connected

- Make readiness confirmation frames reusable as the start of the guarded sample window when they still satisfy quality rules.
- Avoid a gap where readiness passes, then sampling starts too late and catches a new blur cycle.
- Keep everything inside one FLV session so timestamps remain comparable and no extra reconnect jitter is introduced.

### 4.5 Distinguish camera instability from splash motion

- Sample-quality checks should continue using camera-level stability evidence, but avoid treating splash ROI motion itself as camera shake.
- If needed, prefer:
  - ROI sharpness as the main gate
  - broader-frame or non-ROI stability as the auxiliary gate
- The goal is to reject focus loss and PTZ settling blur, not reject real splash dynamics.

### 4.6 Expand result semantics and diagnostics

- Add explicit execution results and/or readiness reasons for:
  - `sample_quality_recovered_and_passed`
  - `sample_quality_degraded`
  - `sample_quality_timeout`
  - `sample_quality_restarted`
- Extend [models.py](/C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/models.py:139) result models with sample-quality diagnostics such as:
  - recovery count
  - qualified frames collected
  - rejected frames during sampling
  - quality window elapsed time
  - whether readiness-confirm frames were reused

### 4.7 Expand replay evidence

- Preserve evidence not only for readiness, but also for sampling quality transitions:
  - first frame of a guarded sample attempt
  - first degraded frame that caused a restart
  - final accepted sample start frame
  - one middle frame from the accepted sequence
  - accepted sequence end frame
- Keep readiness evidence and sampling evidence clearly separated in metadata.
- This is important for distinguishing:
  - blurry before readiness
  - clear at readiness but blurred during sampling
  - repeated autofocus oscillation
  - accepted stable sequence

### 4.8 Make configuration scene-aware

- Add sample-quality guard parameters to [config.py](/C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/config.py:23) and expose them in `to_snapshot_dict()`.
- Allow `dayVisible` and `nightIr` to override these parameters separately in local config.
- Daytime likely needs stricter blur rejection because foam and bright distractors can look deceptively sharp in parts of the ROI.

### 4.9 Update pseudo multi-point outputs

- Add round-level summary fields in [pseudo_multi_point_test.py](/C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/pseudo_multi_point_test.py:61) for:
  - sample quality passed/failed
  - sample quality failure reason
  - recovery count
  - accepted sample frame count
  - whether the sequence was restarted during sampling
- This allows field review without opening every replay first.

### 4.10 Add regression tests before field rerun

- Add tests for:
  - readiness passes, sampling stays sharp, final sequence accepted
  - readiness passes, sampling blurs mid-way, sequence is discarded and restarted
  - readiness passes, repeated blur/recover loops exceed budget and return `undetermined`
  - initial readiness confirm frames are reused correctly
  - `sceneMode=auto` still preserves day/night sample-quality config selection
- Keep compile and unit-test verification as a mandatory gate before现场 retest.

## 5. Suggested Execution Order

1. Extract shared quality judgment from `visual_readiness`
2. Introduce guarded sampling in `RunOnceService`
3. Add config fields and scene-aware overrides
4. Extend result model and replay evidence
5. Update pseudo multi-point round summaries
6. Add regression tests
7. Run daytime `no_splash`
8. Run daytime `has_splash`
9. Then verify dusk transition behavior separately

## 6. Acceptance Criteria

This round should be considered successful only if:

- obvious blur no longer appears inside accepted `sample-start` / representative evidence
- failures move from vague randomness to explainable reasons such as `sample_quality_degraded`
- daytime `no_splash` false positives caused by blur and foam interference clearly decrease
- total round time does not grow only because of unconditional fixed waiting
- the splash recognition baseline itself remains unchanged
