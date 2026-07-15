# Pseudo Multi-Point Test Design

Date: 2026-07-08

## Purpose

This document defines the phase-2 pseudo multi-point test program.

The goal is not to implement the final multi-point巡检任务 yet.

The goal is to create a transitional test program that:

- repeatedly exercises preset switching
- repeatedly returns to the real recognition preset
- reuses the accepted single-point recognition body unchanged
- exposes whether repeated switching breaks timing, triggering, or recognition stability
- provides realistic timing evidence for later multi-point orchestration work

## Why This Stage Exists

Single-point daytime and night-IR recognition are already basically complete for the current stage.

What still needs validation before real multi-point orchestration is:

- after leaving preset 1 and coming back again
- does recognition still trigger at the right time
- does the camera return stably enough for recognition
- does the repeated loop remain reliable over many rounds
- how much time does repeated巡检 actually cost

That is exactly what pseudo multi-point testing is meant to answer.

## Scope

This design is intentionally narrow:

- one real recognition preset
- one transition preset
- one camera
- one existing calibration config for preset 1
- no new recognition rules
- no replacement of the current recognition body

This is a scheduler-and-repeatability test, not a new recognition algorithm stage.

## Role Definition

### Preset 1

Preset 1 is the real recognition target.

It already has:

- calibration config
- ROI
- expected target
- existing single-point recognition path

### Preset 2

Preset 2 is only a transition preset.

It exists to force the camera to leave preset 1 and later return to preset 1.

Preset 2:

- is not a recognition point
- does not need calibration config
- does not need ROI
- only needs a preset index that already exists on the camera

## Core Runtime Principle

The pseudo multi-point test program must not re-implement recognition logic.

It must reuse the existing single-point recognition body as a black box.

That means:

- do not rebuild turn-settle-sample-detect manually
- do not add extra recognition gate logic
- do not insert new visual checks before recognition
- do not replace `RunOnceService`

The pseudo multi-point layer is only responsible for:

- round scheduling
- transition preset control
- result collection
- summary generation

## Program Shape

This should be a new CLI entry, separate from `run_once`.

Recommended shape:

- `python -m inspector.pseudo_multi_point_test ...`

Reason:

- `run_once` stays the minimal single-point recognition entry
- pseudo multi-point remains an explicit phase-2 validation tool
- no need to couple it to UI or web tools yet

## Main Flow

Each round should follow one fixed template.

Recommended sequence:

1. turn to transition preset 2
2. once preset 2 turn command returns success, immediately call single-point recognition for preset 1
3. collect the returned structured result
4. finish the round
5. continue directly to the next round

Important detail:

- the first round must also start by going to preset 2 first

This keeps all rounds under the same preconditions.

So the effective pattern becomes:

- `2 -> run_once(1)`
- `2 -> run_once(1)`
- `2 -> run_once(1)`

The return to preset 1 is handled inside the reused `RunOnceService`.

## Why `RunOnceService` Must Be Called Directly

The pseudo multi-point program should call `RunOnceService.run(...)` directly inside the same Python process.

It should not spawn repeated CLI subprocesses like:

- `python -m inspector.run_once ...`

Reason:

- subprocess startup adds noise to timing
- CLI parsing adds unnecessary wrapper overhead
- stdout/stderr parsing complicates repeated execution
- direct service reuse keeps the recognition body intact while making round orchestration cleaner

This does not skip the normal recognition chain.

Each `RunOnceService.run(...)` call still performs:

- config load
- preset turn
- settle wait
- stream sample
- scene-mode resolution
- alignment
- feature extraction
- scoring
- temporal vote
- replay dispatch

Only the CLI shell is skipped.

## Transition Preset Success Rule

Preset 2 success should be defined as:

- the vendor PTZ/preset interface returns success

No extra visual evidence is required at this stage.

Reason:

- current field trust in the preset API is acceptable
- no known practical case has been observed where the API reports success but the move did not happen
- adding visual departure proof would introduce a new weak heuristic without a solid design basis yet

## Recognition Expectation Rule

The pseudo multi-point program must not hardcode preset 1 as always `has_splash`.

Instead it should accept an explicit expected visual state parameter, for example:

- `expected_visual_state = has_splash`
- or later `expected_visual_state = no_splash`

This parameter does not affect recognition itself.

It is only used by the test program to decide whether a round result matches the intended scenario.

This keeps the test harness reusable for:

- splash-present stability tests
- no-splash return tests

## Round Success Definition

A round should be considered successful only if all of these hold:

1. transition preset 2 turn succeeded
2. preset 1 recognition returned `executionResult = success`
3. preset 1 `visualState` equals the expected visual state

If any of these fails, the round is a failed round.

## Failure Strategy

The program should not stop at first failure.

It should:

- continue all configured rounds
- keep the same round template after failure
- not insert special recovery logic
- not retry failed rounds automatically

Reason:

- this is a stability and distribution test
- failure concentration is more valuable than first-failure-only feedback
- changing the template after failure makes the run harder to compare and harder to replay

## Round Consumption Rule

As soon as a round starts trying to turn to preset 2, that round is considered consumed.

If the round later fails:

- it remains a failed round
- it is not re-run
- the next round starts fresh

This keeps timing and failure counts honest.

## Timing Rules

### No extra wait on preset 2

Do not add an extra settle wait after arriving at preset 2.

Only require:

- transition command success

Reason:

- preset 2 is not a recognition point
- the important settle wait is already part of `RunOnceService` when it turns back to preset 1
- extra waiting at preset 2 would distort total timing

### No extra inter-round sleep

Do not add a fixed pause between rounds.

Rounds should chain immediately.

Reason:

- this gives a more realistic estimate of future polling cost
- all essential waiting already exists inside the reused recognition chain

## Replay Rule

Do not block the next round on replay material async save completion.

Only wait for the main `RunOnceService.run(...)` result to return.

Still record replay status in round output.

This preserves the existing project rule:

- replay materials must not block online recognition flow

## Scene Mode Rule

The pseudo multi-point program should, by default, inherit the current `local_config` scene mode behavior.

It may additionally expose an optional override parameter, such as:

- `scene_mode_override`

But by default it must not silently change recognition mode.

Reason:

- keep the current accepted single-point behavior intact
- still allow targeted testing of `auto / day_visible / night_ir` later

## Timeout Strategy

The pseudo multi-point program should add scheduler-level timeouts without modifying inner recognition logic.

Recommended default values:

- `transition_preset_timeout = 5s`
- `round_timeout = 25s`

Timeout effect:

- if transition preset step exceeds its timeout, mark round failed
- if the whole round exceeds round timeout, mark round failed
- continue to the next round

This prevents one stuck round from destroying the whole test session.

## Output Structure

Each pseudo multi-point test run should create one new output directory.

Inside it:

- one summary JSON
- one JSON per round

Recommended layout:

- `summary.json`
- `round_01.json`
- `round_02.json`
- ...

This keeps runs isolated and easy to compare.

## Partial Output Rule

If the program is interrupted or aborted by a fatal outer exception:

- still write partial round outputs
- still write a partial summary

Summary should include status such as:

- `completed`
- `interrupted`
- `aborted`

Reason:

- partial evidence is still valuable for debugging
- this tool exists for replayability and diagnosis, not only for perfect green runs

## Acceptance Standard

The strict first-pass acceptance standard should be:

- configured `10` rounds all execute to completion
- no fatal process-level interruption
- all round outputs are present
- all `10` preset 1 recognitions match the expected visual state

Example for current splash-present test:

- expected visual state = `has_splash`
- required result = `10 / 10 has_splash`

## Recommended Inputs

The first implementation should support at least:

- single-point calibration config path for preset 1
- transition preset index
- rounds
- expected visual state
- optional scene mode override
- transition preset timeout
- round timeout
- output root directory

## Non-Goals

This program should not:

- replace the final multi-point巡检任务
- add new recognition logic
- add visual departure heuristics for preset 2
- add automatic retries or repair workflows
- wait for replay async save before continuing
- mutate the current accepted single-point recognition body

## Final Recommendation

Pseudo multi-point testing should be implemented as:

- a new CLI tool
- reusing `RunOnceService` directly
- one recognition preset plus one transition preset
- fixed repeated rounds
- no special recovery path
- strict output traceability
- timing and failure distribution preserved honestly

This gives the project a reliable bridge from accepted single-point recognition to later real multi-point scheduling.
