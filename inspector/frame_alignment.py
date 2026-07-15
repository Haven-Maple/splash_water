from __future__ import annotations

from math import hypot

import numpy as np

from inspector.config import RecognitionGlobalConfig
from inspector.models import AlignedSequence, SampledSequence


class FullFrameAligner:
    def __init__(self, global_config: RecognitionGlobalConfig) -> None:
        self.global_config = global_config

    def align(self, sequence: SampledSequence) -> AlignedSequence:
        frames = np.asarray(sequence.frames)
        if not self.global_config.alignmentEnabled or frames.shape[0] <= 1:
            zero_shifts = [(0, 0) for _ in range(frames.shape[0])]
            return AlignedSequence(
                alignedFrames=frames.copy(),
                globalShifts=zero_shifts,
                shiftMagnitudes=[0.0 for _ in zero_shifts],
                appliedGlobalShifts=zero_shifts.copy(),
                appliedShiftMagnitudes=[0.0 for _ in zero_shifts],
                overflowFlags=[False for _ in zero_shifts],
                alignmentApplied=False,
            )

        grayscale = self._to_grayscale(frames)
        downsample = max(1, self.global_config.alignmentDownsampleFactor)
        reference = grayscale[0][::downsample, ::downsample]
        max_shift_pixels = max(1.0, min(frames.shape[1], frames.shape[2]) * self.global_config.maxAlignmentShiftRatio)

        aligned_frames = [frames[0].copy()]
        raw_shifts: list[tuple[int, int]] = [(0, 0)]
        raw_magnitudes: list[float] = [0.0]
        applied_shifts: list[tuple[int, int]] = [(0, 0)]
        applied_magnitudes: list[float] = [0.0]
        overflow_flags: list[bool] = [False]

        for frame_index in range(1, frames.shape[0]):
            moving = grayscale[frame_index][::downsample, ::downsample]
            shift_y_small, shift_x_small = self._estimate_translation(reference, moving)
            shift_y = int(round(-shift_y_small * downsample))
            shift_x = int(round(-shift_x_small * downsample))
            raw_magnitude = hypot(shift_x, shift_y)
            overflow = raw_magnitude > max_shift_pixels
            applied_shift_x, applied_shift_y = self._clamp_shift(shift_x, shift_y, max_shift_pixels)
            aligned_frame = self._translate_frame(frames[frame_index], applied_shift_y, applied_shift_x)
            aligned_frames.append(aligned_frame)
            raw_shifts.append((shift_x, shift_y))
            raw_magnitudes.append(raw_magnitude)
            applied_shifts.append((applied_shift_x, applied_shift_y))
            applied_magnitudes.append(hypot(applied_shift_x, applied_shift_y))
            overflow_flags.append(overflow)

        return AlignedSequence(
            alignedFrames=np.stack(aligned_frames, axis=0),
            globalShifts=raw_shifts,
            shiftMagnitudes=raw_magnitudes,
            appliedGlobalShifts=applied_shifts,
            appliedShiftMagnitudes=applied_magnitudes,
            overflowFlags=overflow_flags,
            alignmentApplied=True,
        )

    @staticmethod
    def _to_grayscale(frames: np.ndarray) -> np.ndarray:
        rgb = frames.astype(np.float32)
        return 0.114 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.299 * rgb[..., 2]

    @staticmethod
    def _estimate_translation(reference: np.ndarray, moving: np.ndarray) -> tuple[int, int]:
        reference_centered = reference - reference.mean()
        moving_centered = moving - moving.mean()
        spectrum = np.fft.fft2(reference_centered) * np.conj(np.fft.fft2(moving_centered))
        spectrum /= np.maximum(np.abs(spectrum), 1e-9)
        correlation = np.fft.ifft2(spectrum)
        peak_y, peak_x = np.unravel_index(np.argmax(np.abs(correlation)), correlation.shape)

        if peak_y > reference.shape[0] // 2:
            peak_y -= reference.shape[0]
        if peak_x > reference.shape[1] // 2:
            peak_x -= reference.shape[1]

        return int(peak_y), int(peak_x)

    @staticmethod
    def _clamp_shift(shift_x: int, shift_y: int, max_shift_pixels: float) -> tuple[int, int]:
        magnitude = hypot(shift_x, shift_y)
        if magnitude <= max_shift_pixels or magnitude <= 0:
            return shift_x, shift_y

        scale = max_shift_pixels / magnitude
        clamped_shift_x = int(round(shift_x * scale))
        clamped_shift_y = int(round(shift_y * scale))
        return clamped_shift_x, clamped_shift_y

    @staticmethod
    def _translate_frame(frame: np.ndarray, shift_y: int, shift_x: int) -> np.ndarray:
        output = np.zeros_like(frame)
        height, width = frame.shape[:2]

        src_y_start = max(0, shift_y)
        src_y_end = min(height, height + shift_y)
        dst_y_start = max(0, -shift_y)
        dst_y_end = dst_y_start + (src_y_end - src_y_start)

        src_x_start = max(0, shift_x)
        src_x_end = min(width, width + shift_x)
        dst_x_start = max(0, -shift_x)
        dst_x_end = dst_x_start + (src_x_end - src_x_start)

        if src_y_end <= src_y_start or src_x_end <= src_x_start:
            return output

        output[dst_y_start:dst_y_end, dst_x_start:dst_x_end] = frame[src_y_start:src_y_end, src_x_start:src_x_end]
        return output
