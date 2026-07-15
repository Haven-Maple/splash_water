from __future__ import annotations

import argparse
import json
import sys

from inspector.config import resolve_config_path
from inspector.run_once_service import RunOnceService
from app.utils.logging_utils import setup_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run phase-2 single-point recognition once.")
    parser.add_argument("--config", required=True, help="Path to a phase-1 calibration JSON file.")
    parser.add_argument("--preset", type=int, default=None, help="Preset index to run. Must match the calibration JSON in step 1.")
    return parser


def emit_scene_mode_summary(result_json: dict[str, object]) -> None:
    requested = result_json.get("requestedSceneMode")
    effective = result_json.get("effectiveSceneMode")
    confidence = result_json.get("sceneModeConfidence")
    reason = result_json.get("sceneModeReason")
    fallback_resolution = result_json.get("fallbackResolution")
    visual_state = result_json.get("visualState")
    visual_readiness_passed = result_json.get("visualReadinessPassed")
    visual_readiness_reason = result_json.get("visualReadinessReason")

    parts = [
        "[scene-mode]",
        f"requested={requested}",
        f"effective={effective}",
        f"visualState={visual_state}",
        f"fallback={fallback_resolution}",
    ]
    if visual_readiness_passed is not None:
        parts.append(f"visualReady={visual_readiness_passed}")
    if visual_readiness_reason:
        parts.append(f"visualReadyReason={visual_readiness_reason}")
    if confidence is not None:
        parts.append(f"confidence={float(confidence):.3f}")
    if reason:
        parts.append(f"reason={reason}")
    print(" ".join(parts), file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    setup_logging("inspector", force=True)
    parser = build_parser()
    args = parser.parse_args(argv)

    config_path = resolve_config_path(args.config)
    service = RunOnceService()
    result = service.run(config_path=config_path, requested_preset_index=args.preset)
    result_json = result.model_dump()
    emit_scene_mode_summary(result_json)
    print(json.dumps(result_json, ensure_ascii=False, indent=2))
    return 0 if result.executionResult == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
