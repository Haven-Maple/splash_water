# Pseudo Multi-Point Test Checklist

Date: 2026-07-08

## Goal

Implement a phase-2 pseudo multi-point validation CLI that repeatedly forces:

- transition to preset 2
- return to preset 1 through the existing recognition entry
- round-by-round result capture
- final timing and success summary

without changing the current single-point recognition body.

## 1. Add A New CLI Entry

Create a new standalone entry, for example:

- `inspector/pseudo_multi_point_test.py`

It should not be merged into:

- `run_once.py`

Acceptance:

- can be executed independently
- clearly separated from the single-point entry

## 2. Add A Dedicated Test Config Object

Create a small config model or dataclass for pseudo multi-point runtime parameters.

Minimum fields:

- `configPath`
- `transitionPresetIndex`
- `rounds`
- `expectedVisualState`
- `sceneModeOverride`
- `transitionPresetTimeoutSeconds`
- `roundTimeoutSeconds`
- `outputRoot`

Acceptance:

- parameters are explicit
- defaults are easy to adjust later

## 3. Reuse `RunOnceService` Directly

Inside the pseudo multi-point program:

- instantiate `RunOnceService`
- call `RunOnceService.run(...)` directly

Do not:

- spawn `python -m inspector.run_once` repeatedly

Acceptance:

- no subprocess parsing layer
- existing recognition body reused intact

## 4. Implement The Fixed Round Template

Every round must follow the same structure:

1. turn to transition preset 2
2. on transition success, call `RunOnceService.run(...)` for preset 1
3. collect structured result
4. move to next round

Important:

- first round must also start from preset 2

Acceptance:

- no round-specific branch logic
- all rounds have the same precondition

## 5. Use Preset 2 As Transition Only

Preset 2 input should only be:

- `presetIndex`

Do not require:

- calibration config
- ROI
- target metadata

Acceptance:

- preset 1 remains the only recognition config source
- preset 2 remains a pure scheduler concept

## 6. Implement Round Success / Failure Rules

A round succeeds only if:

- preset 2 turn succeeded
- `RunOnceService` returns `executionResult = success`
- `visualState == expectedVisualState`

Otherwise:

- mark the round failed

Acceptance:

- success definition is explicit
- no hidden heuristic pass logic

## 7. Continue After Failure

Implement:

- continue all configured rounds
- do not stop at first failure
- do not retry failed rounds
- do not insert special recovery logic

Acceptance:

- all started rounds are counted
- failure distribution remains analyzable

## 8. Implement Timing Rules

Apply these policies:

- no extra settle wait on preset 2
- no extra inter-round sleep
- do not wait for replay async save completion

Acceptance:

- total timing stays close to real future polling behavior

## 9. Add Scheduler-Level Timeouts

Add external timeouts:

- `transition_preset_timeout = 5s` default
- `round_timeout = 25s` default

Behavior:

- timeout -> mark current round failed
- continue next round

Acceptance:

- one stuck round cannot block the whole run forever

## 10. Add Output Directory Per Run

Each run should create one new directory under an output root.

Inside it write:

- `summary.json`
- `round_01.json`
- `round_02.json`
- ...

Acceptance:

- runs do not overwrite each other
- round evidence is easy to inspect

## 11. Add Partial Summary On Interrupt / Abort

If:

- user interrupts
- outer fatal error aborts the program

still write:

- completed round JSONs
- a partial `summary.json`

Summary should include:

- run status
- completed rounds
- failed rounds
- interruption or abort reason

Acceptance:

- partial evidence is preserved

## 12. Add Scene Mode Handling

Default:

- inherit current `local_config` scene mode behavior

Optional:

- support a manual override parameter

Acceptance:

- default behavior does not silently change current recognition semantics

## 13. Recommended Summary Fields

The final summary should include at least:

- run status
- start time
- end time
- total elapsed ms
- configured rounds
- attempted rounds
- successful rounds
- failed rounds
- expected visual state
- transition preset index
- scene mode override if any
- average round elapsed ms
- min round elapsed ms
- max round elapsed ms
- failure breakdown by step
- round result file list

## 14. Recommended Round Fields

Each round JSON should include at least:

- round index
- round status
- transition preset result
- transition elapsed ms
- `run_once` result payload
- round elapsed ms
- success/failure reason
- whether expected visual state matched

## 15. First Acceptance Target

The first accepted pseudo multi-point run should satisfy:

- `10` rounds configured
- all `10` rounds executed
- all `10` preset 1 recognition results match expected state
- outputs are fully written
- total elapsed time is observable and stable enough for future planning

## 16. Deferred Items

Do not include yet:

- real multi-point scheduler
- concurrent point processing
- visual proof that preset 2 truly departed
- transition-preset recognition
- automatic retry policy
- alarm/report upload logic

## 17. Documentation And Traceability

When implementation lands, update:

- dev log
- integration notes
- issues/risk list if new runtime risks appear

Acceptance:

- the next thread can implement or validate without re-explaining the design
