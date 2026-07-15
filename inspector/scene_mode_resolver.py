from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from inspector.config import RecognitionGlobalConfig
from inspector.models import (
    ResolvedSceneMode,
    SampledSequence,
    SceneModeClassification,
    SceneModeDiagnostics,
)


@dataclass(slots=True)
class SceneModeDecision:
    classification: SceneModeClassification
    suggestedMode: ResolvedSceneMode
    confidence: float
    reason: str
    diagnostics: SceneModeDiagnostics


class SceneModeResolver:
    def __init__(self, global_config: RecognitionGlobalConfig) -> None:
        self.global_config = global_config

    def resolve(self, sequence: SampledSequence) -> SceneModeDecision:
        frames = np.asarray(sequence.frames, dtype=np.float32)
        return self._resolve_frames(frames)

    def resolve_frames(self, frames: list[np.ndarray] | np.ndarray) -> SceneModeDecision:
        frames_array = np.asarray(frames, dtype=np.float32)
        if frames_array.ndim != 4 or frames_array.shape[0] == 0:
            raise ValueError("Scene mode probe requires at least one frame")
        return self._resolve_frames(frames_array)

    def _resolve_frames(self, frames: np.ndarray) -> SceneModeDecision:
        inspected_frame_count = max(
            1,
            min(
                self.global_config.sceneAutoFrameCount,
                frames.shape[0],
            ),
        )
        cropped_frames = self._center_crop(
            frames[:inspected_frame_count],
            self.global_config.sceneAutoCenterCropRatio,
        )

        colorfulness_values: list[float] = []
        saturation_values: list[float] = []
        channel_delta_values: list[float] = []
        channel_correlation_values: list[float] = []
        brightness_mean_values: list[float] = []
        brightness_std_values: list[float] = []

        for frame in cropped_frames:
            colorfulness_values.append(self._colorfulness(frame))
            saturation_values.append(self._saturation_p90(frame))
            channel_delta_values.append(self._channel_delta_mean(frame))
            channel_correlation_values.append(self._channel_correlation(frame))
            brightness_mean_values.append(self._brightness_mean(frame))
            brightness_std_values.append(self._brightness_std(frame))

        colorfulness_mean = float(np.mean(colorfulness_values))
        saturation_p90 = float(np.mean(saturation_values))
        channel_delta_mean = float(np.mean(channel_delta_values))
        channel_correlation = float(np.mean(channel_correlation_values))
        brightness_mean = float(np.mean(brightness_mean_values))
        brightness_std = float(np.mean(brightness_std_values))

        day_score = self._day_visible_score(
            colorfulness_mean=colorfulness_mean,
            saturation_p90=saturation_p90,
            channel_delta_mean=channel_delta_mean,
        )
        night_score = self._night_ir_score(
            colorfulness_mean=colorfulness_mean,
            saturation_p90=saturation_p90,
            channel_delta_mean=channel_delta_mean,
            channel_correlation=channel_correlation,
        )
        score_margin = abs(day_score - night_score)
        suggested_mode: ResolvedSceneMode = "day_visible" if day_score >= night_score else "night_ir"
        winner_score = max(day_score, night_score)
        confidence = self._clamp((winner_score * 0.75) + (score_margin * 0.25))

        if confidence < self.global_config.sceneAutoConfidenceThreshold:
            classification: SceneModeClassification = "ambiguous"
            if score_margin < 0.08:
                reason = "scene_scores_too_close_for_safe_switch"
            else:
                reason = "scene_mode_confidence_below_threshold"
        else:
            classification = suggested_mode
            reason = (
                "visible_colorfulness_supports_day_visible"
                if suggested_mode == "day_visible"
                else "low_color_delta_and_high_channel_correlation_support_night_ir"
            )

        diagnostics = SceneModeDiagnostics(
            classification=classification,
            suggestedMode=suggested_mode,
            inspectedFrameCount=inspected_frame_count,
            centerCropRatio=self.global_config.sceneAutoCenterCropRatio,
            colorfulnessMean=colorfulness_mean,
            saturationP90=saturation_p90,
            channelDeltaMean=channel_delta_mean,
            channelCorrelation=channel_correlation,
            brightnessMean=brightness_mean,
            brightnessStd=brightness_std,
            dayVisibleScore=day_score,
            nightIrScore=night_score,
            scoreMargin=score_margin,
        )
        return SceneModeDecision(
            classification=classification,
            suggestedMode=suggested_mode,
            confidence=confidence,
            reason=reason,
            diagnostics=diagnostics,
        )

    def _day_visible_score(
        self,
        *,
        colorfulness_mean: float,
        saturation_p90: float,
        channel_delta_mean: float,
    ) -> float:
        delta_scale = max(self.global_config.sceneAutoMaxChannelDeltaForIr * 2.0, 1.0)
        color_score = self._clamp(colorfulness_mean / max(self.global_config.sceneAutoMinColorfulness, 1e-6))
        saturation_score = self._clamp(saturation_p90 / max(self.global_config.sceneAutoMinSaturationP90, 1e-6))
        delta_score = self._clamp(channel_delta_mean / delta_scale)
        return self._clamp((color_score * 0.4) + (saturation_score * 0.35) + (delta_score * 0.25))

    def _night_ir_score(
        self,
        *,
        colorfulness_mean: float,
        saturation_p90: float,
        channel_delta_mean: float,
        channel_correlation: float,
    ) -> float:
        gray_score = self._clamp(
            1.0 - (channel_delta_mean / max(self.global_config.sceneAutoMaxChannelDeltaForIr, 1e-6))
        )
        saturation_low_score = self._clamp(
            1.0 - (saturation_p90 / max(self.global_config.sceneAutoMinSaturationP90, 1e-6))
        )
        color_low_score = self._clamp(
            1.0 - (colorfulness_mean / max(self.global_config.sceneAutoMinColorfulness, 1e-6))
        )
        correlation_score = self._clamp(
            (channel_correlation - self.global_config.sceneAutoMinChannelCorrelationForIr)
            / max(1.0 - self.global_config.sceneAutoMinChannelCorrelationForIr, 1e-6)
        )
        return self._clamp(
            (gray_score * 0.35)
            + (correlation_score * 0.35)
            + (saturation_low_score * 0.15)
            + (color_low_score * 0.15)
        )

    @staticmethod
    def _center_crop(frames: np.ndarray, crop_ratio: float) -> np.ndarray:
        if frames.ndim != 4 or crop_ratio >= 0.999:
            return frames

        _, height, width, _ = frames.shape
        crop_height = max(1, int(round(height * crop_ratio)))
        crop_width = max(1, int(round(width * crop_ratio)))
        start_y = max(0, (height - crop_height) // 2)
        start_x = max(0, (width - crop_width) // 2)
        end_y = min(height, start_y + crop_height)
        end_x = min(width, start_x + crop_width)
        return frames[:, start_y:end_y, start_x:end_x, :]

    @staticmethod
    def _colorfulness(frame: np.ndarray) -> float:
        blue = frame[..., 0]
        green = frame[..., 1]
        red = frame[..., 2]
        rg = red - green
        yb = (0.5 * (red + green)) - blue
        rg_std = float(np.std(rg))
        yb_std = float(np.std(yb))
        rg_mean = float(np.mean(rg))
        yb_mean = float(np.mean(yb))
        return float(np.sqrt((rg_std**2) + (yb_std**2)) + (0.3 * np.sqrt((rg_mean**2) + (yb_mean**2))))

    @staticmethod
    def _saturation_p90(frame: np.ndarray) -> float:
        max_channel = np.max(frame, axis=-1)
        min_channel = np.min(frame, axis=-1)
        saturation = np.zeros_like(max_channel, dtype=np.float32)
        valid_mask = max_channel > 1e-6
        saturation[valid_mask] = (max_channel[valid_mask] - min_channel[valid_mask]) / max_channel[valid_mask]
        return float(np.quantile(saturation, 0.9))

    @staticmethod
    def _channel_delta_mean(frame: np.ndarray) -> float:
        blue = frame[..., 0]
        green = frame[..., 1]
        red = frame[..., 2]
        return float(np.mean((np.abs(red - green) + np.abs(green - blue) + np.abs(blue - red)) / 3.0))

    def _channel_correlation(self, frame: np.ndarray) -> float:
        blue = frame[..., 0].reshape(-1)
        green = frame[..., 1].reshape(-1)
        red = frame[..., 2].reshape(-1)
        correlations = [
            self._safe_correlation(red, green),
            self._safe_correlation(green, blue),
            self._safe_correlation(blue, red),
        ]
        return float(np.mean(correlations))

    @staticmethod
    def _safe_correlation(channel_a: np.ndarray, channel_b: np.ndarray) -> float:
        std_a = float(np.std(channel_a))
        std_b = float(np.std(channel_b))
        if std_a < 1e-6 or std_b < 1e-6:
            delta_mean = float(np.mean(np.abs(channel_a - channel_b)))
            return 1.0 if delta_mean <= 1.0 else 0.0
        correlation = float(np.corrcoef(channel_a, channel_b)[0, 1])
        if np.isnan(correlation):
            return 0.0
        return SceneModeResolver._clamp(correlation)

    @staticmethod
    def _brightness_mean(frame: np.ndarray) -> float:
        blue = frame[..., 0]
        green = frame[..., 1]
        red = frame[..., 2]
        grayscale = (0.114 * blue) + (0.587 * green) + (0.299 * red)
        return float(np.mean(grayscale))

    @staticmethod
    def _brightness_std(frame: np.ndarray) -> float:
        blue = frame[..., 0]
        green = frame[..., 1]
        red = frame[..., 2]
        grayscale = (0.114 * blue) + (0.587 * green) + (0.299 * red)
        return float(np.std(grayscale))

    @staticmethod
    def _clamp(value: float) -> float:
        return max(0.0, min(1.0, float(value)))
