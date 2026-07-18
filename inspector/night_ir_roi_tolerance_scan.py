from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from inspector.config import build_recognition_config
from inspector.night_ir_gap_fill_scan import _load_cases_from_run, _load_sequence, _load_snapshot_raw_config
from inspector.run_once_service import RunOnceService


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay night_ir ROI tolerance candidates using the same sequence-level runtime selection."
    )
    parser.add_argument("--has-splash-run", action="append", default=[], help="Pseudo-multi-point has_splash run directory.")
    parser.add_argument("--no-splash-run", action="append", default=[], help="Pseudo-multi-point no_splash run directory.")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path.")
    return parser


def _evaluate_case(case) -> dict[str, object]:  # noqa: ANN001
    sequence = _load_sequence(case)
    raw_config = _load_snapshot_raw_config(case.configSnapshotPath)
    effective_config = build_recognition_config(raw_config, "night_ir")
    service = RunOnceService(global_config=effective_config, raw_config=raw_config)
    detection = service._run_detection_pass(
        sequence=sequence,
        target=case.target,
        effective_config=effective_config,
    )
    tolerance = detection.roiTolerance
    candidate_results: list[dict[str, object]] = []
    if tolerance is not None:
        for candidate in tolerance.candidates:
            metrics = tolerance.candidateMetrics.get(candidate.key)
            candidate_results.append(
                {
                    "key": candidate.key,
                    "roi": candidate.roi.model_dump() if candidate.roi is not None else None,
                    "offsetXRatio": candidate.offsetXRatio,
                    "offsetYRatio": candidate.offsetYRatio,
                    "scale": candidate.scale,
                    "isBase": candidate.isBase,
                    "skipReason": candidate.skipReason,
                    "framePassCount": metrics.framePassCount if metrics is not None else None,
                    "hardGatePassCount": metrics.hardGatePassCount if metrics is not None else None,
                    "weightedFrameScoreMean": metrics.weightedFrameScoreMean if metrics is not None else None,
                    "dynamicEvidencePassCount": metrics.dynamicEvidencePassCount if metrics is not None else None,
                }
            )
    return {
        "runId": case.runId,
        "roundIndex": case.roundIndex,
        "expectedVisualState": case.expectedVisualState,
        "currentVisualState": case.currentVisualState,
        "baseRoi": case.target.roi.model_dump(),
        "candidateVisualState": detection.voteDecision.visualState,
        "candidateReason": detection.voteDecision.reason,
        "roiToleranceEnabled": tolerance.enabled if tolerance is not None else False,
        "roiToleranceCandidateCount": tolerance.candidateCount if tolerance is not None else 0,
        "roiToleranceEvaluatedCandidateCount": tolerance.evaluatedCandidateCount if tolerance is not None else 0,
        "roiToleranceSelectedRoi": (
            tolerance.selectedCandidate.roi.model_dump()
            if tolerance is not None and tolerance.selectedCandidate.roi is not None
            else case.target.roi.model_dump()
        ),
        "roiToleranceSelectedOffsetXRatio": tolerance.selectedCandidate.offsetXRatio if tolerance is not None else 0.0,
        "roiToleranceSelectedOffsetYRatio": tolerance.selectedCandidate.offsetYRatio if tolerance is not None else 0.0,
        "roiToleranceSelectedScale": tolerance.selectedCandidate.scale if tolerance is not None else 1.0,
        "roiToleranceBaseFramePassCount": tolerance.baseFramePassCount if tolerance is not None else None,
        "roiToleranceSelectedFramePassCount": tolerance.selectedFramePassCount if tolerance is not None else None,
        "roiToleranceRescued": tolerance.rescued if tolerance is not None else False,
        "candidates": candidate_results,
        "configSnapshotPath": str(case.configSnapshotPath),
    }


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    run_dirs = [Path(value) for value in args.has_splash_run + args.no_splash_run]
    if not run_dirs:
        raise SystemExit("At least one --has-splash-run or --no-splash-run is required.")

    cases = []
    skipped = []
    for run_dir in run_dirs:
        loaded_cases, loaded_skipped = _load_cases_from_run(run_dir)
        cases.extend(loaded_cases)
        skipped.extend(loaded_skipped)

    results = [_evaluate_case(case) for case in cases]
    report = {
        "scanType": "night_ir_roi_tolerance",
        "hasSplashRuns": args.has_splash_run,
        "noSplashRuns": args.no_splash_run,
        "caseCount": len(results),
        "skippedCount": len(skipped),
        "skippedRounds": [asdict(item) for item in skipped],
        "cases": results,
    }
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Night IR ROI tolerance scan: cases={len(results)} skipped={len(skipped)}")
    for result in results:
        print(
            f"round={result['roundIndex']} expected={result['expectedVisualState']} "
            f"state={result['candidateVisualState']} rescued={result['roiToleranceRescued']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
