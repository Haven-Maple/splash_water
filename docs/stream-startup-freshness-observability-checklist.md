# Stream Startup Freshness Observability Checklist

## 1. Goal

`stream startup freshness guard` is now active, but its result currently lives mostly in logs.
That makes field replay review slower than necessary.

This round should expose freshness results in structured outputs so we can quickly answer:

- did the startup guard actually detect a stale-to-live jump?
- how many frames did it consume?
- did it settle cleanly or just hit timeout?
- was the later problem a startup issue or a focus-quality issue?

## 2. Why This Matters

Without this data in `round_*.json` and replay metadata, field diagnosis still requires manual image inspection first.

With it exposed, we can classify failures much faster:

- startup stale-frame problem
- readiness pass too early
- sample-phase focus regression

## 3. Non-Goals

This round should not:

- change splash scoring
- change day/night routing
- change sample-quality acceptance rules
- change PTZ waiting strategy

It is an observability uplift, not an algorithm change.

## 4. Required Result Fields

Add structured freshness diagnostics into the recognition result model and replay metadata.

Recommended fields:

- `streamStartupFreshnessEnabled`
- `streamStartupFreshnessConsumedFrames`
- `streamStartupFreshnessElapsedMs`
- `streamStartupFreshnessJumpDetected`
- `streamStartupFreshnessStableAfterJump`
- `streamStartupFreshnessExitReason`

Recommended `streamStartupFreshnessExitReason` values:

- `disabled`
- `jump_and_stable`
- `timeout_no_jump`
- `timeout_after_jump_no_stable`
- `no_frames`

## 5. Evidence Files

Persist two dedicated evidence frames:

- `stream-startup-start.ppm`
- `stream-startup-settled.ppm`

These should be separate from:

- `scene-probe-start.ppm`
- `visual-readiness-start.ppm`
- `sample-quality-attempt-start.ppm`

That separation is important for fast visual timeline reconstruction.

## 6. Code Touch Points

- [inspector/run_once_service.py](/C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/run_once_service.py)
- [inspector/models.py](/C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/models.py)
- [inspector/replay_store.py](/C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/replay_store.py)
- [inspector/pseudo_multi_point_test.py](/C:/Users/Maple_Rain/Documents/Items/splash_water/inspector/pseudo_multi_point_test.py)

## 7. Model / Output Changes

### 7.1 Recognition result

Extend `RecognitionRunResult` with a structured freshness block or flat fields.

### 7.2 Replay metadata

Write the same freshness fields into replay `metadata.json`.

### 7.3 Pseudo multi-point round summary

Promote the most important fields to round top level:

- `streamStartupFreshnessExitReason`
- `streamStartupFreshnessConsumedFrames`
- `streamStartupFreshnessElapsedMs`
- `streamStartupFreshnessJumpDetected`
- `streamStartupFreshnessStableAfterJump`

This allows quick scanning without opening replay metadata.

### 7.4 Evidence paths

Extend `RecognitionEvidencePaths` and round JSON with:

- `streamStartupStartFramePath`
- `streamStartupSettledFramePath`

Keep target path and ready path semantics consistent with the existing replay save pattern.

## 8. Replay Store Changes

- Add output paths for startup freshness frames
- Save them from the freshness result object
- Keep them optional when freshness is disabled or no frames were read

## 9. Tests To Add

- freshness disabled should emit `disabled`
- stale-to-live transition should emit `jump_and_stable`
- timeout before any jump should emit `timeout_no_jump`
- jump detected but no stable window should emit `timeout_after_jump_no_stable`
- replay store should save startup freshness frames separately
- pseudo multi-point round result should surface freshness fields

## 10. Review Payoff

After this uplift, a single failed round should be diagnosable in this order:

1. check freshness exit reason
2. check startup start / settled frames
3. check scene probe frames
4. check readiness start / ready frames
5. check sample-quality evidence

That reduces manual ambiguity and speeds up field triage.

## 11. Acceptance Criteria

- freshness diagnostics appear in recognition result, replay metadata, and round JSON
- startup freshness frames are saved separately
- we can distinguish "startup stale frame not cleared" from "later blur regression" without relying only on raw logs
