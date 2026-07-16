from __future__ import annotations

import argparse
import copy
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np

from inspector.config import build_recognition_config
from inspector.frame_alignment import FullFrameAligner
from inspector.frame_features import FrameFeatureExtractor, mean_roi_motion
from inspector.frame_scoring import WeightedFrameScorer
from inspector.models import AlignedSequence, FrameFeature, RecognitionTarget, SampledSequence, VisualState
from inspector.run_once_service import RunOnceService
from inspector.temporal_voting import TemporalVoteResolver


ExpectedVisualState = Literal["has_splash", "no_splash"]


@dataclass(slots=True)
class ReplayCase:
    runId: str
    roundIndex: int
    expectedVisualState: ExpectedVisualState
    currentVisualState: VisualState | None
    currentExecutionResult: str | None
    sequencePath: Path
    configSnapshotPath: Path
    target: RecognitionTarget
    sampledFrameCount: int
    targetFrameCount: int
    configuredSampleFps: float
    actualSampleFps: float
    configuredSampleDurationMs: int
    actualSampleDurationMs: int


@dataclass(slots=True)
class SkippedRound:
    runId: str
    roundIndex: int
    expectedVisualState: str | None
    reason: str


@dataclass(slots=True)
class CachedReplayCase:
    case: ReplayCase
    sequence: SampledSequence
    baseRawConfig: dict[str, Any]
    alignedSequence: AlignedSequence
    frameFeatures: list[FrameFeature]
    preAlignmentRoiMotion: float
    postAlignmentRoiMotion: float


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Offline scan night_ir hardGateMinGapFillRatio against pseudo-multi-point replay samples."
        )
    )
    parser.add_argument("--has-splash-run", action="append", default=[], help="Pseudo-multi-point run dir for has_splash.")
    parser.add_argument("--no-splash-run", action="append", default=[], help="Pseudo-multi-point run dir for no_splash.")
    parser.add_argument("--threshold-start", type=float, default=0.81)
    parser.add_argument("--threshold-end", type=float, default=0.76)
    parser.add_argument("--threshold-step", type=float, default=0.01)
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path.")
    return parser


def _load_cases_from_run(run_dir: Path) -> tuple[list[ReplayCase], list[SkippedRound]]:
    summary_path = run_dir / "summary.json"
    if not summary_path.exists():
        raise FileNotFoundError(f"Missing summary.json in {run_dir}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    run_id = summary.get("runId", run_dir.name)
    expected_visual_state = summary.get("expectedVisualState")
    round_files = summary.get("roundResultFiles") or [path.name for path in sorted(run_dir.glob("round_*.json"))]
    cases: list[ReplayCase] = []
    skipped: list[SkippedRound] = []

    for round_file in round_files:
        round_path = run_dir / round_file
        if not round_path.exists():
            skipped.append(
                SkippedRound(
                    runId=run_id,
                    roundIndex=-1,
                    expectedVisualState=expected_visual_state,
                    reason=f"missing_round_file:{round_file}",
                )
            )
            continue
        round_payload = json.loads(round_path.read_text(encoding="utf-8"))
        round_index = int(round_payload.get("roundIndex", -1))
        recognition_result = round_payload.get("recognitionResult")
        execution_result = round_payload.get("recognitionExecutionResult")
        if recognition_result is None:
            skipped.append(
                SkippedRound(
                    runId=run_id,
                    roundIndex=round_index,
                    expectedVisualState=expected_visual_state,
                    reason="missing_recognition_result",
                )
            )
            continue
        if execution_result != "success":
            skipped.append(
                SkippedRound(
                    runId=run_id,
                    roundIndex=round_index,
                    expectedVisualState=expected_visual_state,
                    reason=f"execution_result:{execution_result}",
                )
            )
            continue
        if recognition_result.get("effectiveSceneMode") != "night_ir":
            skipped.append(
                SkippedRound(
                    runId=run_id,
                    roundIndex=round_index,
                    expectedVisualState=expected_visual_state,
                    reason=f"effective_scene_mode:{recognition_result.get('effectiveSceneMode')}",
                )
            )
            continue

        evidence_paths = recognition_result.get("evidencePaths") or {}
        sequence_path_value = evidence_paths.get("replaySequencePath")
        if not sequence_path_value:
            skipped.append(
                SkippedRound(
                    runId=run_id,
                    roundIndex=round_index,
                    expectedVisualState=expected_visual_state,
                    reason="missing_replay_sequence_path",
                )
            )
            continue
        sequence_path = Path(sequence_path_value)
        if not sequence_path.exists():
            skipped.append(
                SkippedRound(
                    runId=run_id,
                    roundIndex=round_index,
                    expectedVisualState=expected_visual_state,
                    reason=f"missing_replay_sequence_file:{sequence_path}",
                )
            )
            continue
        config_snapshot_value = evidence_paths.get("recognitionConfigSnapshotPath")
        if config_snapshot_value:
            config_snapshot_path = Path(config_snapshot_value)
        else:
            config_snapshot_path = sequence_path.parent / "recognition-config.snapshot.json"
        if not config_snapshot_path.exists():
            skipped.append(
                SkippedRound(
                    runId=run_id,
                    roundIndex=round_index,
                    expectedVisualState=expected_visual_state,
                    reason=f"missing_config_snapshot_file:{config_snapshot_path}",
                )
            )
            continue

        score_summary = recognition_result.get("scoreSummary") or {}
        target = RecognitionTarget.model_validate(recognition_result["target"])
        sampled_frame_count = int(score_summary.get("sampledFrameCount") or score_summary.get("targetFrameCount") or 0)
        target_frame_count = int(score_summary.get("targetFrameCount") or sampled_frame_count)
        configured_sample_fps = float(score_summary.get("configuredSampleFps") or 0.0)
        actual_sample_fps = float(score_summary.get("actualSampleFps") or configured_sample_fps or 0.0)
        configured_sample_duration_ms = int(score_summary.get("configuredSampleDurationMs") or 0)
        actual_sample_duration_ms = int(score_summary.get("actualSampleDurationMs") or configured_sample_duration_ms or 0)

        if expected_visual_state not in {"has_splash", "no_splash"}:
            raise ValueError(f"Unsupported expectedVisualState={expected_visual_state!r} in {summary_path}")

        cases.append(
            ReplayCase(
                runId=run_id,
                roundIndex=round_index,
                expectedVisualState=expected_visual_state,
                currentVisualState=round_payload.get("actualVisualState"),
                currentExecutionResult=execution_result,
                sequencePath=sequence_path,
                configSnapshotPath=config_snapshot_path,
                target=target,
                sampledFrameCount=sampled_frame_count,
                targetFrameCount=target_frame_count,
                configuredSampleFps=configured_sample_fps,
                actualSampleFps=actual_sample_fps,
                configuredSampleDurationMs=configured_sample_duration_ms,
                actualSampleDurationMs=actual_sample_duration_ms,
            )
        )

    return cases, skipped


def _load_sequence(case: ReplayCase) -> SampledSequence:
    with np.load(case.sequencePath) as archive:
        frames = archive["frames"]
        timestamps = archive["frame_timestamps_ms"].astype(np.int32).tolist()
    return SampledSequence(
        streamType="flv",
        streamUrl=str(case.sequencePath),
        frames=frames,
        frameTimestampsMs=timestamps,
        targetFrameCount=case.targetFrameCount or len(timestamps),
        sampledFrameCount=case.sampledFrameCount or len(timestamps),
        configuredSampleFps=case.configuredSampleFps,
        actualSampleFps=case.actualSampleFps,
        configuredSampleDurationMs=case.configuredSampleDurationMs,
        actualSampleDurationMs=case.actualSampleDurationMs,
        frameWidth=int(frames.shape[2]) if frames.ndim >= 3 else 0,
        frameHeight=int(frames.shape[1]) if frames.ndim >= 3 else 0,
    )


def _threshold_values(start: float, end: float, step: float) -> list[float]:
    if step <= 0:
        raise ValueError("threshold step must be positive")
    direction = -1 if start >= end else 1
    current = start
    values: list[float] = []
    while (current >= end - 1e-9) if direction < 0 else (current <= end + 1e-9):
        values.append(round(current, 6))
        current += direction * step
    return values


def _load_snapshot_raw_config(config_snapshot_path: Path) -> dict[str, Any]:
    raw_config = json.loads(config_snapshot_path.read_text(encoding="utf-8"))
    if not isinstance(raw_config, dict):
        raise TypeError(f"Config snapshot must contain a JSON object: {config_snapshot_path}")
    return raw_config


def _build_case_cache(case: ReplayCase) -> CachedReplayCase:
    sequence = _load_sequence(case)
    base_raw_config = _load_snapshot_raw_config(case.configSnapshotPath)
    effective_config = build_recognition_config(base_raw_config, "night_ir")
    aligner = FullFrameAligner(effective_config)
    feature_extractor = FrameFeatureExtractor(effective_config)
    aligned_sequence = aligner.align(sequence)
    frame_features = feature_extractor.extract(aligned_sequence.alignedFrames, case.target.roi)
    pre_alignment_roi_motion = mean_roi_motion(sequence.frames, case.target.roi)
    post_alignment_roi_motion = mean_roi_motion(aligned_sequence.alignedFrames, case.target.roi)
    return CachedReplayCase(
        case=case,
        sequence=sequence,
        baseRawConfig=base_raw_config,
        alignedSequence=aligned_sequence,
        frameFeatures=frame_features,
        preAlignmentRoiMotion=pre_alignment_roi_motion,
        postAlignmentRoiMotion=post_alignment_roi_motion,
    )


def _build_night_config(*, raw_config: dict[str, Any], threshold: float):
    config_payload = copy.deepcopy(raw_config)
    night_ir = config_payload.setdefault("nightIr", {})
    if not isinstance(night_ir, dict):
        raise TypeError("nightIr config block must be a dict")
    night_ir["hardGateMinGapFillRatio"] = threshold
    return build_recognition_config(config_payload, "night_ir"), config_payload


def _evaluate_threshold(cases: list[CachedReplayCase], *, threshold: float) -> dict[str, object]:
    case_results: list[dict[str, object]] = []
    has_splash_ok = 0
    no_splash_ok = 0

    for case in cases:
        effective_config, config_payload = _build_night_config(raw_config=case.baseRawConfig, threshold=threshold)
        service = RunOnceService(global_config=effective_config, raw_config=config_payload)
        frame_scorer = WeightedFrameScorer(effective_config)
        vote_resolver = TemporalVoteResolver(effective_config)
        min_vote_frames = int(math.ceil(effective_config.sequenceVoteThreshold * effective_config.sequenceFrameCount))
        frame_scores = frame_scorer.score(case.frameFeatures)
        score_summary = service._score_summary(
            effective_config=effective_config,
            sequence=case.sequence,
            aligned_sequence=case.alignedSequence,
            frame_features=case.frameFeatures,
            frame_scores=frame_scores,
            pre_alignment_roi_motion=case.preAlignmentRoiMotion,
            post_alignment_roi_motion=case.postAlignmentRoiMotion,
        )
        decision = vote_resolver.resolve(score_summary)
        score_summary.framePassRatio = decision.passRatio
        score_summary.overflowFrameRatio = decision.overflowFrameRatio
        score_summary.motionReductionRatio = decision.motionReductionRatio
        score_summary.reliabilityGateTriggered = decision.reliabilityGateTriggered
        score_summary.temporalVoteReason = decision.reason
        score_summary.staticBrightInterferenceSuppressed = decision.staticBrightInterferenceSuppressed
        matched_expected = decision.visualState == case.case.expectedVisualState
        if case.case.expectedVisualState == "has_splash" and matched_expected:
            has_splash_ok += 1
        if case.case.expectedVisualState == "no_splash" and matched_expected:
            no_splash_ok += 1
        case_results.append(
            {
                "runId": case.case.runId,
                "roundIndex": case.case.roundIndex,
                "expectedVisualState": case.case.expectedVisualState,
                "currentVisualState": case.case.currentVisualState,
                "candidateVisualState": decision.visualState,
                "candidateReason": decision.reason,
                "gapFillPassCount": score_summary.gapFillPassCount,
                "hardGatePassCount": score_summary.hardGatePassCount,
                "framePassCount": score_summary.framePassCount,
                "framePassRatio": score_summary.framePassRatio,
                "hardGatePassRatio": score_summary.hardGatePassRatio,
                "meetsTemporalVoteMinimum": (
                    (score_summary.framePassCount or 0) >= min_vote_frames
                    if score_summary.framePassCount is not None
                    else False
                ),
                "minTemporalVoteFrames": min_vote_frames,
                "largestBrightComponentPassCount": score_summary.largestBrightComponentPassCount,
                "centerBrightCoveragePassCount": score_summary.centerBrightCoveragePassCount,
                "verticalSpreadPassCount": score_summary.verticalSpreadPassCount,
                "continuousBrightPassCount": score_summary.continuousBrightPassCount,
                "dynamicEvidencePassCount": score_summary.dynamicEvidencePassCount,
                "gapFillRatioMean": score_summary.gapFillRatio,
                "matchedExpected": matched_expected,
                "configSnapshotPath": str(case.case.configSnapshotPath),
            }
        )

    has_splash_total = sum(1 for case in cases if case.case.expectedVisualState == "has_splash")
    no_splash_total = sum(1 for case in cases if case.case.expectedVisualState == "no_splash")
    return {
        "threshold": threshold,
        "hasSplashMatched": has_splash_ok,
        "hasSplashTotal": has_splash_total,
        "noSplashMatched": no_splash_ok,
        "noSplashTotal": no_splash_total,
        "allHasSplashMatched": has_splash_ok == has_splash_total,
        "allNoSplashMatched": no_splash_ok == no_splash_total,
        "selectedSafeThreshold": has_splash_ok == has_splash_total and no_splash_ok == no_splash_total,
        "cases": case_results,
    }


def _print_summary(report: dict[str, object]) -> None:
    print("Night IR gapFill threshold scan")
    print(f"cases={report['caseCount']} skipped={report['skippedCount']}")
    for threshold_result in report["thresholds"]:
        print(
            (
                f"threshold={threshold_result['threshold']:.3f} "
                f"has_splash={threshold_result['hasSplashMatched']}/{threshold_result['hasSplashTotal']} "
                f"no_splash={threshold_result['noSplashMatched']}/{threshold_result['noSplashTotal']} "
                f"safe={threshold_result['selectedSafeThreshold']}"
            )
        )
    selected = report.get("selectedThreshold")
    if selected is None:
        print("selected_threshold=None")
    else:
        print(f"selected_threshold={selected:.3f}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    run_dirs = [Path(path) for path in args.has_splash_run + args.no_splash_run]
    if not run_dirs:
        parser.error("At least one --has-splash-run or --no-splash-run is required.")

    cases: list[ReplayCase] = []
    skipped: list[SkippedRound] = []
    for run_dir in run_dirs:
        loaded_cases, loaded_skipped = _load_cases_from_run(run_dir)
        cases.extend(loaded_cases)
        skipped.extend(loaded_skipped)

    thresholds = _threshold_values(args.threshold_start, args.threshold_end, args.threshold_step)
    cached_cases = [_build_case_cache(case) for case in cases]
    threshold_results = [
        _evaluate_threshold(cached_cases, threshold=threshold)
        for threshold in thresholds
    ]
    safe_thresholds = [item["threshold"] for item in threshold_results if item["selectedSafeThreshold"]]
    selected_threshold = max(safe_thresholds) if safe_thresholds else None

    report = {
        "scanType": "night_ir_gap_fill_threshold",
        "hasSplashRuns": args.has_splash_run,
        "noSplashRuns": args.no_splash_run,
        "thresholds": threshold_results,
        "selectedThreshold": selected_threshold,
        "caseCount": len(cases),
        "skippedCount": len(skipped),
        "skippedRounds": [asdict(item) for item in skipped],
    }
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
