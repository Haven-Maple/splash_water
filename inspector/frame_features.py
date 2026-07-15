from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.schemas.calibration import RoiModel

from inspector.config import RecognitionGlobalConfig
from inspector.models import FrameFeature


@dataclass(slots=True)
class _BrightComponent:
    area: int
    mask: np.ndarray
    minY: int
    maxY: int


class FrameFeatureExtractor:
    def __init__(self, global_config: RecognitionGlobalConfig) -> None:
        self.global_config = global_config
        self._last_component_mask: np.ndarray | None = None
        self._last_component_ratio = 0.0

    def extract(self, aligned_frames: np.ndarray, roi: RoiModel) -> list[FrameFeature]:
        self._last_component_mask = None
        self._last_component_ratio = 0.0
        grayscale = self._to_grayscale(aligned_frames)
        roi_slices = self._roi_slices(grayscale.shape[1], grayscale.shape[2], roi)

        features: list[FrameFeature] = []
        previous = grayscale[0][roi_slices]
        previous_component_mask: np.ndarray | None = None
        previous_component_ratio = 0.0
        features.append(
            self._extract_frame_feature(
                frame_index=0,
                current=previous,
                previous=previous,
                previous_component_mask=previous_component_mask,
                previous_component_ratio=previous_component_ratio,
            )
        )
        previous_component_mask = self._last_component_mask
        previous_component_ratio = self._last_component_ratio

        for frame_index in range(1, grayscale.shape[0]):
            current = grayscale[frame_index][roi_slices]
            features.append(
                self._extract_frame_feature(
                    frame_index=frame_index,
                    current=current,
                    previous=previous,
                    previous_component_mask=previous_component_mask,
                    previous_component_ratio=previous_component_ratio,
                )
            )
            previous = current
            previous_component_mask = self._last_component_mask
            previous_component_ratio = self._last_component_ratio

        return features

    def _extract_frame_feature(
        self,
        *,
        frame_index: int,
        current: np.ndarray,
        previous: np.ndarray,
        previous_component_mask: np.ndarray | None,
        previous_component_ratio: float,
    ) -> FrameFeature:
        diff = np.abs(current - previous)
        local_motion = float(np.mean(diff) / 255.0)
        dynamic_ratio = float(np.mean(diff >= self.global_config.dynamicPixelThreshold))
        roi_brightness_q99 = float(np.quantile(current, 0.99))
        roi_brightness_max = float(np.max(current))

        (
            filtered_bright_mask,
            largest_component,
            bright_component_count,
            fragmentation_score,
            bright_threshold,
        ) = self._bright_components(current)
        upper_half_ratio, lower_half_ratio = self._half_bright_ratios(filtered_bright_mask)
        center_coverage = self._center_coverage(
            largest_component.mask if largest_component is not None else None,
            current.shape[0],
            current.shape[1],
        )
        vertical_spread_ratio = self._vertical_spread_ratio(largest_component, current.shape[0])
        gap_fill_ratio = self._gap_fill_ratio(filtered_bright_mask, largest_component)

        if largest_component is not None and np.any(largest_component.mask):
            highlight_motion = float(np.mean(diff[largest_component.mask]) / 255.0)
            largest_component_ratio = float(largest_component.area / current.size)
            current_component_mask = largest_component.mask
        else:
            highlight_motion = 0.0
            largest_component_ratio = 0.0
            current_component_mask = None

        if frame_index == 0:
            temporal_area_variance = 0.0
            temporal_shape_variance = 0.0
        else:
            temporal_area_variance = self._temporal_area_variance(largest_component_ratio, previous_component_ratio)
            temporal_shape_variance = self._temporal_shape_variance(current_component_mask, previous_component_mask)
        self._last_component_mask = current_component_mask
        self._last_component_ratio = largest_component_ratio

        return FrameFeature(
            frameIndex=frame_index,
            brightThreshold=bright_threshold,
            roiBrightnessQ99=roi_brightness_q99,
            roiBrightnessMax=roi_brightness_max,
            localResidualMotion=local_motion,
            dynamicAreaRatio=dynamic_ratio,
            highlightDisturbance=highlight_motion,
            largestBrightComponentRatio=largest_component_ratio,
            brightComponentCount=bright_component_count,
            fragmentationScore=fragmentation_score,
            centerBrightCoverage=center_coverage,
            upperHalfBrightRatio=upper_half_ratio,
            lowerHalfBrightRatio=lower_half_ratio,
            verticalSpreadRatio=vertical_spread_ratio,
            gapFillRatio=gap_fill_ratio,
            temporalAreaVariance=temporal_area_variance,
            temporalShapeVariance=temporal_shape_variance,
        )

    def _bright_components(
        self,
        roi_grayscale: np.ndarray,
    ) -> tuple[np.ndarray, _BrightComponent | None, int, float, float]:
        bright_mask, bright_threshold = self._bright_mask(roi_grayscale)
        if not np.any(bright_mask):
            return np.zeros_like(bright_mask, dtype=bool), None, 0, 1.0, bright_threshold

        min_component_pixels = max(1, int(round(bright_mask.size * self.global_config.brightComponentMinAreaRatio)))
        components = [
            component
            for component in self._connected_components(bright_mask)
            if component.area >= min_component_pixels
        ]
        if not components:
            return np.zeros_like(bright_mask, dtype=bool), None, 0, 1.0, bright_threshold

        filtered_mask = np.zeros_like(bright_mask, dtype=bool)
        for component in components:
            filtered_mask |= component.mask

        largest_component = max(components, key=lambda item: item.area)
        total_bright_pixels = sum(component.area for component in components)
        fragmentation_score = 1.0 - (largest_component.area / total_bright_pixels)
        return filtered_mask, largest_component, len(components), float(fragmentation_score), bright_threshold

    def _bright_mask(self, roi_grayscale: np.ndarray) -> tuple[np.ndarray, float]:
        if self.global_config.sceneMode != "night_ir":
            threshold = float(self.global_config.highlightPixelThreshold)
            return roi_grayscale >= threshold, threshold

        smoothed = self._box_blur(roi_grayscale, self.global_config.nightBrightBlurRadius)
        mean_value = float(np.mean(smoothed))
        std_value = float(np.std(smoothed))
        quantile_threshold = float(np.quantile(smoothed, self.global_config.nightBrightQuantile))
        std_threshold = mean_value + self.global_config.nightBrightStdMultiplier * std_value
        threshold = max(
            float(self.global_config.nightBrightMinThreshold),
            min(quantile_threshold, std_threshold),
        )
        return smoothed >= threshold, threshold

    @staticmethod
    def _connected_components(mask: np.ndarray) -> list[_BrightComponent]:
        height, width = mask.shape
        visited = np.zeros_like(mask, dtype=bool)
        components: list[_BrightComponent] = []

        for start_y in range(height):
            for start_x in range(width):
                if not mask[start_y, start_x] or visited[start_y, start_x]:
                    continue

                stack = [(start_y, start_x)]
                visited[start_y, start_x] = True
                pixels: list[tuple[int, int]] = []
                min_y = max_y = start_y

                while stack:
                    current_y, current_x = stack.pop()
                    pixels.append((current_y, current_x))
                    if current_y < min_y:
                        min_y = current_y
                    if current_y > max_y:
                        max_y = current_y

                    for next_y in range(max(0, current_y - 1), min(height, current_y + 2)):
                        for next_x in range(max(0, current_x - 1), min(width, current_x + 2)):
                            if visited[next_y, next_x] or not mask[next_y, next_x]:
                                continue
                            visited[next_y, next_x] = True
                            stack.append((next_y, next_x))

                component_mask = np.zeros_like(mask, dtype=bool)
                ys = [item[0] for item in pixels]
                xs = [item[1] for item in pixels]
                component_mask[ys, xs] = True
                components.append(
                    _BrightComponent(
                        area=len(pixels),
                        mask=component_mask,
                        minY=min_y,
                        maxY=max_y,
                    )
                )

        return components

    @staticmethod
    def _box_blur(image: np.ndarray, radius: int) -> np.ndarray:
        if radius <= 0:
            return image

        kernel = np.ones((radius * 2) + 1, dtype=np.float32)
        kernel = kernel / np.sum(kernel)
        padded = np.pad(image.astype(np.float32), ((radius, radius), (radius, radius)), mode="edge")
        blurred_rows = np.apply_along_axis(lambda row: np.convolve(row, kernel, mode="valid"), 1, padded)
        return np.apply_along_axis(lambda col: np.convolve(col, kernel, mode="valid"), 0, blurred_rows)

    @staticmethod
    def _center_coverage(component_mask: np.ndarray | None, height: int, width: int) -> float:
        if component_mask is None:
            return 0.0

        start_y = max(0, int(round(height * 0.25)))
        end_y = max(start_y + 1, int(round(height * 0.75)))
        start_x = max(0, int(round(width * 0.25)))
        end_x = max(start_x + 1, int(round(width * 0.75)))
        center_mask = component_mask[start_y:end_y, start_x:end_x]
        if center_mask.size == 0:
            return 0.0
        return float(np.mean(center_mask))

    @staticmethod
    def _half_bright_ratios(filtered_bright_mask: np.ndarray) -> tuple[float, float]:
        height = filtered_bright_mask.shape[0]
        split_index = max(1, height // 2)
        upper_mask = filtered_bright_mask[:split_index]
        lower_mask = filtered_bright_mask[split_index:]
        upper_ratio = float(np.mean(upper_mask)) if upper_mask.size > 0 else 0.0
        lower_ratio = float(np.mean(lower_mask)) if lower_mask.size > 0 else 0.0
        return upper_ratio, lower_ratio

    @staticmethod
    def _vertical_spread_ratio(component: _BrightComponent | None, roi_height: int) -> float:
        if component is None or roi_height <= 0:
            return 0.0
        return float((component.maxY - component.minY + 1) / roi_height)

    @staticmethod
    def _gap_fill_ratio(filtered_bright_mask: np.ndarray, component: _BrightComponent | None) -> float:
        if component is None:
            return 0.0

        start_y = max(0, component.minY - 1)
        end_y = min(filtered_bright_mask.shape[0], component.maxY + 2)
        band = filtered_bright_mask[start_y:end_y]
        total_bright_pixels = 0
        total_span_width = 0
        for row in band:
            bright_columns = np.flatnonzero(row)
            if bright_columns.size < 2:
                continue
            total_bright_pixels += int(bright_columns.size)
            total_span_width += int(bright_columns[-1] - bright_columns[0] + 1)

        if total_span_width <= 0:
            return 0.0
        return float(total_bright_pixels / total_span_width)

    @staticmethod
    def _temporal_area_variance(current_ratio: float, previous_ratio: float) -> float:
        if current_ratio <= 0 and previous_ratio <= 0:
            return 0.0
        baseline = max(current_ratio, previous_ratio, 1e-9)
        return float(abs(current_ratio - previous_ratio) / baseline)

    @staticmethod
    def _temporal_shape_variance(
        current_component_mask: np.ndarray | None,
        previous_component_mask: np.ndarray | None,
    ) -> float:
        if current_component_mask is None and previous_component_mask is None:
            return 0.0
        if current_component_mask is None or previous_component_mask is None:
            return 1.0

        union = np.logical_or(current_component_mask, previous_component_mask)
        if not np.any(union):
            return 0.0
        intersection = np.logical_and(current_component_mask, previous_component_mask)
        iou = float(np.sum(intersection) / np.sum(union))
        return float(1.0 - iou)

    @staticmethod
    def _to_grayscale(frames: np.ndarray) -> np.ndarray:
        rgb = frames.astype(np.float32)
        return 0.114 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.299 * rgb[..., 2]

    @staticmethod
    def _roi_slices(height: int, width: int, roi: RoiModel) -> tuple[slice, slice]:
        start_y = max(0, min(roi.y, height - 1))
        start_x = max(0, min(roi.x, width - 1))
        end_y = max(start_y + 1, min(height, start_y + roi.height))
        end_x = max(start_x + 1, min(width, start_x + roi.width))
        return slice(start_y, end_y), slice(start_x, end_x)


def mean_roi_motion(frames: np.ndarray, roi: RoiModel) -> float:
    extractor = FrameFeatureExtractor(RecognitionGlobalConfig())
    grayscale = extractor._to_grayscale(frames)
    roi_slices = extractor._roi_slices(grayscale.shape[1], grayscale.shape[2], roi)
    if grayscale.shape[0] <= 1:
        return 0.0

    diffs: list[float] = []
    previous = grayscale[0][roi_slices]
    for frame_index in range(1, grayscale.shape[0]):
        current = grayscale[frame_index][roi_slices]
        diffs.append(float(np.mean(np.abs(current - previous)) / 255.0))
        previous = current
    return float(np.mean(diffs)) if diffs else 0.0
