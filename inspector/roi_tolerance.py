from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.schemas.calibration import RoiModel
from inspector.config import RecognitionGlobalConfig


OFFSET_RATIOS = (-0.08, 0.0, 0.08)
SCALES = (1.0, 1.1)


@dataclass(frozen=True, slots=True)
class RoiToleranceCandidate:
    key: str
    roi: RoiModel | None
    offsetXRatio: float
    offsetYRatio: float
    scale: float
    isBase: bool
    skipReason: str | None
    tieBreakRank: int


@dataclass(frozen=True, slots=True)
class RoiToleranceSequenceMetrics:
    framePassCount: int
    hardGatePassCount: int
    weightedFrameScoreMean: float
    dynamicEvidencePassCount: int


@dataclass(frozen=True, slots=True)
class RoiTolerancePrefilterMetrics:
    centerBrightCoverageMean: float
    brightCoverageMean: float
    dynamicAreaMean: float


def generate_night_roi_candidates(
    base_roi: RoiModel,
    frame_width: int,
    frame_height: int,
    *,
    offset_ratios: tuple[float, ...] = OFFSET_RATIOS,
    scales: tuple[float, ...] = SCALES,
) -> list[RoiToleranceCandidate]:
    """Build a deterministic, sequence-level candidate set without clipping variants."""
    base_center_x = base_roi.x + base_roi.width / 2
    base_center_y = base_roi.y + base_roi.height / 2
    candidates: list[RoiToleranceCandidate] = []

    for offset_y_ratio in offset_ratios:
        for offset_x_ratio in offset_ratios:
            for scale in scales:
                width = int(round(base_roi.width * scale))
                height = int(round(base_roi.height * scale))
                offset_x = int(round(base_roi.width * offset_x_ratio))
                offset_y = int(round(base_roi.height * offset_y_ratio))
                x = int(round(base_center_x - width / 2 + offset_x))
                y = int(round(base_center_y - height / 2 + offset_y))
                is_base = offset_x_ratio == 0.0 and offset_y_ratio == 0.0 and scale == 1.0
                out_of_bounds = x < 0 or y < 0 or x + width > frame_width or y + height > frame_height
                roi = RoiModel(x=x, y=y, width=width, height=height) if is_base or not out_of_bounds else None
                candidates.append(
                    RoiToleranceCandidate(
                        key=f"x{offset_x_ratio:+.2f}_y{offset_y_ratio:+.2f}_s{scale:.1f}",
                        # The saved base ROI remains evaluable for legacy compatibility even if malformed.
                        roi=roi,
                        offsetXRatio=offset_x_ratio,
                        offsetYRatio=offset_y_ratio,
                        scale=scale,
                        isBase=is_base,
                        skipReason=None if is_base or not out_of_bounds else "out_of_bounds",
                        tieBreakRank=0,
                    )
                )

    ordered = sorted(
        candidates,
        key=lambda item: (
            0 if item.isBase else 1,
            abs(item.offsetXRatio) + abs(item.offsetYRatio),
            abs(item.offsetXRatio),
            abs(item.offsetYRatio),
            item.scale,
            item.offsetYRatio,
            item.offsetXRatio,
        ),
    )
    return [
        RoiToleranceCandidate(
            key=item.key,
            roi=item.roi,
            offsetXRatio=item.offsetXRatio,
            offsetYRatio=item.offsetYRatio,
            scale=item.scale,
            isBase=item.isBase,
            skipReason=item.skipReason,
            tieBreakRank=index,
        )
        for index, item in enumerate(ordered)
    ]


def select_sequence_candidate(
    candidates: list[RoiToleranceCandidate],
    metrics_by_key: dict[str, RoiToleranceSequenceMetrics],
) -> RoiToleranceCandidate:
    """Choose one ROI from aggregate sequence evidence, never from individual frames."""
    evaluable = [candidate for candidate in candidates if candidate.roi is not None and candidate.key in metrics_by_key]
    if not evaluable:
        raise ValueError("No valid ROI tolerance candidates were evaluated.")
    return max(
        evaluable,
        key=lambda candidate: (
            metrics_by_key[candidate.key].framePassCount,
            metrics_by_key[candidate.key].hardGatePassCount,
            metrics_by_key[candidate.key].weightedFrameScoreMean,
            metrics_by_key[candidate.key].dynamicEvidencePassCount,
            -candidate.tieBreakRank,
        ),
    )


def prefilter_night_roi_candidates(
    candidates: list[RoiToleranceCandidate],
    aligned_frames: np.ndarray,
    config: RecognitionGlobalConfig,
    *,
    max_full_candidates: int,
) -> tuple[list[RoiToleranceCandidate], dict[str, RoiTolerancePrefilterMetrics]]:
    """Keep the base ROI plus the most promising alternatives before expensive component analysis."""
    evaluable = [candidate for candidate in candidates if candidate.roi is not None]
    base_candidate = next(candidate for candidate in evaluable if candidate.isBase)
    if max_full_candidates <= 1 or len(evaluable) <= 1:
        return [base_candidate], {}

    grayscale = (
        0.114 * aligned_frames[..., 0].astype(np.float32)
        + 0.587 * aligned_frames[..., 1].astype(np.float32)
        + 0.299 * aligned_frames[..., 2].astype(np.float32)
    )
    metrics_by_key: dict[str, RoiTolerancePrefilterMetrics] = {}
    for candidate in evaluable:
        roi = candidate.roi
        assert roi is not None
        roi_frames = grayscale[:, roi.y : roi.y + roi.height, roi.x : roi.x + roi.width]
        if roi_frames.size == 0:
            continue
        thresholds = np.maximum(
            float(config.nightBrightMinThreshold),
            np.minimum(
                np.quantile(roi_frames, config.nightBrightQuantile, axis=(1, 2)),
                np.mean(roi_frames, axis=(1, 2))
                + config.nightBrightStdMultiplier * np.std(roi_frames, axis=(1, 2)),
            ),
        )
        bright_masks = roi_frames >= thresholds[:, None, None]
        start_y = max(0, int(round(roi.height * 0.25)))
        end_y = max(start_y + 1, int(round(roi.height * 0.75)))
        start_x = max(0, int(round(roi.width * 0.25)))
        end_x = max(start_x + 1, int(round(roi.width * 0.75)))
        center_masks = bright_masks[:, start_y:end_y, start_x:end_x]
        differences = np.abs(np.diff(roi_frames, axis=0))
        metrics_by_key[candidate.key] = RoiTolerancePrefilterMetrics(
            centerBrightCoverageMean=float(np.mean(center_masks)) if center_masks.size else 0.0,
            brightCoverageMean=float(np.mean(bright_masks)),
            dynamicAreaMean=(
                float(np.mean(differences >= config.dynamicPixelThreshold)) if differences.size else 0.0
            ),
        )

    alternatives = [candidate for candidate in evaluable if not candidate.isBase and candidate.key in metrics_by_key]
    alternatives.sort(
        key=lambda candidate: (
            metrics_by_key[candidate.key].centerBrightCoverageMean,
            metrics_by_key[candidate.key].brightCoverageMean,
            metrics_by_key[candidate.key].dynamicAreaMean,
            -candidate.tieBreakRank,
        ),
        reverse=True,
    )
    return [base_candidate, *alternatives[: max(0, max_full_candidates - 1)]], metrics_by_key
