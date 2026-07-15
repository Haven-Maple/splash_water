# Night IR Recognition V1 Design

Date: 2026-07-05

## Purpose

This document defines the first implementation direction for night infrared splash recognition in phase 2.

The goal is not to finalize industrial-grade night recognition in one step. The goal is to establish a correct first direction that can:

- reuse the existing preset + ROI + sequence pipeline
- separate obvious `has_splash` from obvious `no_splash` under IR preview
- produce traceable structured evidence
- leave difficult night edge cases for later targeted regression

## Why Night IR Must Be Designed Separately

Night IR behaves differently from daytime visible light.

Observed difference from current screenshots:

- `no_splash`:
  - the aerator blades themselves appear white and bright
  - the blades are narrow, separated, and relatively stable in position
  - regular reflection may still exist below the blades
- `has_splash`:
  - the ROI becomes a large continuous white mass
  - the original blade-bar structure is mostly swallowed
  - the splash body looks blurrier, thicker, and more diffuse

Because of this, night IR cannot use "white highlight exists" as a primary signal.

If we keep brightness as the main evidence, static bright blades will be misclassified as splash.

## Core Night IR Recognition Principle

Night IR should detect:

- bright bar structure vs bright mass structure
- preserved dark gaps vs filled dark gaps
- stable rigid bright object vs unstable diffuse bright body
- weak shape change vs persistent shape disturbance over time

So the night question is not:

- "Is there a bright white area?"

The night question is:

- "Has the original bright blade structure turned into a central continuous disturbed bright spray body?"

## Reused Runtime Architecture

Night IR V1 should keep the same main runtime chain:

`turn preset -> wait settle -> sample short FLV sequence -> full-frame alignment -> ROI feature extraction -> frame hard gate -> temporal vote -> structured result -> async replay save`

This is important because we want:

- one shared runtime architecture
- one shared traceability pattern
- one shared replay workflow

Only the feature emphasis and thresholds should change for the IR mode.

## What Must Stay Global

Night IR should not become "one preset, one parameter set".

The project decision still holds:

- no per-preset recognition parameter set
- no manual threshold set for every target

But it is reasonable to allow two scene-level modes:

- `day_visible`
- `night_ir`

This is not a maintenance burden like per-preset tuning. It is a practical global scene split.

## Night IR V1 Recognition Direction

### 1. Brightness must be demoted

Brightness can still be used, but only as supporting evidence.

Reason:

- blades are also bright in IR
- reflections may also be bright in IR
- overexposed IR areas may produce false white blobs

So brightness alone must never pass the frame gate.

### 2. Shape must become primary

Night IR should primarily distinguish:

- separated vertical bright bars
vs
- one thick central continuous bright body

This is the single most important shape difference seen in the screenshots.

### 3. Time variation must remain required

A large bright central mass is still not enough by itself.

Night IR must also require at least one dynamic branch such as:

- main bright body area variation
- main bright body boundary disturbance
- local residual motion in the main bright body

This prevents static bright objects from passing.

## Recommended Night IR Feature Set

Night IR V1 should keep some existing daytime features and add or reweight IR-specific ones.

### Keep and reuse

- `largestBrightComponentRatio`
  - still useful because splash tends to form one dominant bright body
- `centerBrightCoverage`
  - still useful because splash should occupy the ROI center
- `verticalSpreadRatio`
  - still useful because splash should occupy meaningful vertical extent
- `highlightDisturbance`
  - still useful, but not enough by itself

### Add or strengthen

#### `gapFillRatio`

Definition:

- measure how much the dark gaps between blade-like bright bars are filled

Reason:

- `no_splash` IR should preserve separated bright bars with visible dark gaps
- `has_splash` IR should fill or hide these gaps with a continuous bright mass

This is likely the most valuable new IR-specific feature.

#### `brightComponentCount`

Definition:

- count of filtered bright components in the ROI

Reason:

- `no_splash` tends to look like several separated components
- `has_splash` tends to collapse toward one dominant component

Count alone is not enough, but it is useful together with component size and fragmentation.

#### `fragmentationScore`

Definition:

- how fragmented the bright area is after filtering

Reason:

- blades are more separated and fragmented
- splash is more continuous and dominated by one main body

#### `edgeSoftnessScore`

Definition:

- estimate whether the main bright body has soft blurry edges or rigid sharp edges

Reason:

- blades are narrow and rigid
- splash is diffuse and soft-edged under IR

This can be implemented later if the simpler shape features are not enough.

#### `temporalAreaVariance`

Definition:

- measure how much the dominant bright component area changes across the sampled sequence

Reason:

- splash breathes and fluctuates
- static blades remain much more stable

#### `temporalShapeVariance`

Definition:

- measure how much the dominant bright component shape changes over time

Reason:

- splash contour changes frame to frame
- blade structure is more rigid

This is strongly recommended for night IR.

## Night IR Hard Gate Proposal

The frame hard gate should be redesigned for IR scenes as:

### Structure branch

Require all or most of:

- dominant bright component exists
- dominant component is large enough
- dominant component covers the ROI center
- dominant component has enough vertical spread
- fragmentation is low enough
- dark-gap fill is high enough

### Dynamic branch

Require at least one:

- local residual motion above minimum
- highlight disturbance above minimum
- dominant bright area variance above minimum
- dominant shape variance above minimum

### Explicit rejection intuition

Reject frames that look like:

- several thin stable bright bars
- preserved blade gaps
- small rigid bright structures
- central bright object with very low temporal disturbance

## Temporal Vote Strategy

Night IR V1 should keep the existing temporal vote architecture.

That means:

- do not invent a separate night sequence classifier yet
- keep frame-level `framePass`
- keep sequence-level `framePassRatio`
- keep final `visualState`:
  - `has_splash`
  - `no_splash`
  - `undetermined`

However, the sequence summary should expose more IR-oriented diagnostics:

- `gapFillRatio`
- `temporalAreaVariance`
- `temporalShapeVariance`
- dominant-component stability summary

## Day vs Night Config Strategy

Recommendation:

- keep one shared algorithm framework
- allow two global recognition config profiles

Example:

- `recognition_v1.day_visible`
- `recognition_v1.night_ir`

This is better than forcing one threshold set to satisfy both scenes too early.

It also avoids the maintenance burden of per-preset tuning.

## First Implementation Priority

Night IR should be implemented in this order:

1. Keep the current runtime chain unchanged.
2. Introduce a mode split between day and night config.
3. Reweight the frame gate for IR scenes.
4. Add `gapFillRatio`.
5. Add at least one temporal dominant-body stability feature:
   - `temporalAreaVariance`
   or
   - `temporalShapeVariance`
6. Preserve the existing structured result and replay evidence path.

## First Acceptance Scope

Night IR V1 should first target only obvious cases:

- clear `has_splash`
- clear `no_splash`

It does not need to solve all hard night cases immediately.

This follows the same strategy used successfully in daytime work.

## Recommended Night Sample Set

Start with one preset and one ROI.

Collect at least:

- `5-10` clear `has_splash` samples
- `5-10` clear `no_splash` samples

Prefer samples where:

- the camera position is already stable
- the ROI is already confirmed correct
- the IR mode is clearly active

Do not block implementation on rare difficult conditions yet.

## Deferred Night Risks

These should be handled later as targeted night regression, not as V1 blockers:

- stronger IR bloom or local overexposure
- heavy noise in very dark scenes
- wind-driven water disturbance under IR
- mixed glare from nearby lamps
- weak or partial splash states

## Expected Night V1 Outcome

If the direction is correct, the system should learn this separation:

- `no_splash`:
  - separated bright blade-like bars
  - visible dark gaps
  - smaller rigid bright structures
  - limited dominant-body fluctuation
- `has_splash`:
  - one dominant central bright mass
  - blade-bar structure mostly swallowed
  - dark gaps reduced or filled
  - persistent temporal disturbance in the main bright body

## Next Step

After this design document, the next implementation work should be:

1. add night-IR config mode support
2. extend frame features for `gapFillRatio`
3. add one temporal dominant-body change feature
4. run a small clear night sample acceptance set

