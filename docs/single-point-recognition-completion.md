# Single-Point Recognition Completion

Date: 2026-07-07

## Purpose

This document records the completion state of phase-2 single-point splash recognition.

At this stage, the project goal is no longer "make one branch work once", but:

- preserve the accepted daytime baseline
- preserve the accepted night-IR baseline
- confirm the shared single-point runtime is stable enough to move into pseudo multi-point testing

## Completion Conclusion

Single-point recognition is now considered basically complete for the current project stage.

This completion applies to:

- one camera
- one calibrated preset
- one ROI
- one aerator target
- daytime visible-light recognition
- night infrared recognition

The current result is good enough to stop single-point same-condition tuning and advance to the next testing stage.

## What Is Included In This Completion

The accepted single-point runtime now includes:

- preset turn
- settle wait
- FLV short-sequence sampling
- full-frame alignment
- ROI feature extraction
- frame hard gate
- weighted scoring
- temporal vote
- structured result output
- replay material saving
- day/night scene-mode split
- automatic scene-mode switching implementation
- manual day/night override retention

This means the recognition chain itself is no longer the current blocking item.

## Current Accepted Baselines

### Daytime visible-light line

Reference baseline:

- [daytime-recognition-baseline.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/daytime-recognition-baseline.md)

Current preserved acceptance set:

- positive samples folder: [run_once_result_has](C:/Users/Maple_Rain/Documents/Items/splash_water/run_once_result_has/)
- negative samples folder: [run_once_result_no](C:/Users/Maple_Rain/Documents/Items/splash_water/run_once_result_no/)

Current preserved result:

- daytime positive: `10 / 10 has_splash`
- daytime negative: `10 / 10 no_splash`
- daytime total: `20 / 20` correct

Interpretation:

- the daytime line is now stable enough for this phase
- further same-condition daytime tuning is not the best use of effort

### Night infrared line

Reference baseline:

- [night-ir-baseline.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/night-ir-baseline.md)

Current preserved acceptance set:

- positive samples folder: [run_once_result_night_has](C:/Users/Maple_Rain/Documents/Items/splash_water/run_once_result_night_has/)
- negative samples folder: [run_once_result_night_no](C:/Users/Maple_Rain/Documents/Items/splash_water/run_once_result_night_no/)

Current preserved result:

- night positive: `40 / 40 has_splash`
- night negative: `10 / 10 no_splash`
- night total: `50 / 50` correct

Interpretation:

- the night line is no longer blocked by extraction collapse or loose structure gating
- the current night ROI margin and structure-dominant logic are working acceptably for this phase

## Auto Scene Switching Status

Auto day/night switching has already been implemented in the single-point runtime.

Reference design:

- [auto-scene-mode-switch-design.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/auto-scene-mode-switch-design.md)
- [auto-scene-mode-switch-checklist.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/auto-scene-mode-switch-checklist.md)

Current project status:

- auto switching exists in code
- manual override still exists
- published default is intentionally still conservative until replay and field routing acceptance are replayed as needed

This means auto switching is part of the runtime capability, but it is not the reason to delay pseudo multi-point testing.

## Why This Stage Can End Here

The current limiting factor is no longer "can one point be recognized".

The more valuable next question is:

- can the same runtime architecture stay stable when we simulate multiple point tasks and switching behavior

That is a better next-stage risk than continuing to squeeze more same-condition single-point samples.

## Remaining Risks That Do Not Block The Next Step

These are real risks, but they no longer block leaving the single-point stage:

- difficult daytime glare and unusual weather
- high-wind disturbance
- more chaotic night water surface noise
- ambiguous day/night boundary scenes
- scene-auto fallback frequency under real field diversity

These should continue as regression items, not as a reason to hold the project at single-point.

## Next Step

The recommended next stage is:

- pseudo multi-point testing

Meaning:

- keep the current single-point recognition body unchanged as much as possible
- begin testing task scheduling / preset switching / repeated run behavior across multiple logical points
- verify that the runtime and evidence chain remain stable when recognition is no longer treated as a one-off single point call

## Freeze Rule

Until pseudo multi-point testing exposes a concrete regression:

- do not casually retune the accepted daytime baseline
- do not casually retune the accepted night baseline
- use the current day/night single-point baselines as the comparison anchor for later multi-point work
