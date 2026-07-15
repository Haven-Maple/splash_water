from __future__ import annotations

from pathlib import Path

import numpy as np

from app.schemas.calibration import RoiModel


def write_full_frame_ppm(path: Path, frame: np.ndarray) -> None:
    rgb = frame[..., ::-1] if frame.ndim == 3 and frame.shape[2] == 3 else frame
    _write_ppm(path, rgb.astype(np.uint8))


def write_representative_roi_ppm(path: Path, frame: np.ndarray, roi: RoiModel) -> None:
    cropped = _crop_frame(frame, roi)
    rgb = cropped[..., ::-1] if cropped.ndim == 3 and cropped.shape[2] == 3 else cropped
    _write_ppm(path, rgb.astype(np.uint8))


def write_motion_debug_pgm(path: Path, previous_frame: np.ndarray, current_frame: np.ndarray, roi: RoiModel) -> None:
    previous_crop = _crop_frame(previous_frame, roi)
    current_crop = _crop_frame(current_frame, roi)
    previous_gray = _to_grayscale(previous_crop)
    current_gray = _to_grayscale(current_crop)
    diff = np.abs(current_gray.astype(np.int16) - previous_gray.astype(np.int16)).astype(np.uint8)
    _write_pgm(path, diff)


def _crop_frame(frame: np.ndarray, roi: RoiModel) -> np.ndarray:
    height, width = frame.shape[:2]
    start_y = max(0, min(roi.y, height - 1))
    start_x = max(0, min(roi.x, width - 1))
    end_y = max(start_y + 1, min(height, start_y + roi.height))
    end_x = max(start_x + 1, min(width, start_x + roi.width))
    return frame[start_y:end_y, start_x:end_x]


def _to_grayscale(frame: np.ndarray) -> np.ndarray:
    if frame.ndim == 2:
        return frame.astype(np.uint8)
    rgb = frame.astype(np.float32)
    gray = 0.114 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.299 * rgb[..., 2]
    return np.clip(gray, 0, 255).astype(np.uint8)


def _write_ppm(path: Path, rgb: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = rgb.shape[:2]
    header = f"P6\n{width} {height}\n255\n".encode("ascii")
    with path.open("wb") as file:
        file.write(header)
        file.write(rgb.tobytes())


def _write_pgm(path: Path, gray: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = gray.shape[:2]
    header = f"P5\n{width} {height}\n255\n".encode("ascii")
    with path.open("wb") as file:
        file.write(header)
        file.write(gray.tobytes())
