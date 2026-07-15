from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

import numpy as np

from inspector.config import RecognitionGlobalConfig
from inspector.models import ResolvedSceneMode, SceneModeDiagnostics
from inspector.scene_mode_resolver import SceneModeDecision, SceneModeResolver


@dataclass(slots=True)
class SceneModeStabilityWindow:
    decision: SceneModeDecision
    startFrame: np.ndarray
    endFrame: np.ndarray
    frameCount: int


@dataclass(slots=True)
class SceneModeStabilityResult:
    enabled: bool
    stable: bool
    initialMode: ResolvedSceneMode | None
    finalMode: ResolvedSceneMode | None
    elapsedMs: int
    windowCount: int
    transitionObserved: bool
    relockCount: int
    relockReason: str | None
    transitionTimeout: bool
    reason: str
    finalDecision: SceneModeDecision | None
    streamType: str
    streamUrl: str
    startFrame: np.ndarray | None
    settledFrame: np.ndarray | None
    windows: list[SceneModeStabilityWindow]
    observedFrames: list[np.ndarray]
    frameTimestampsMs: list[int]


class SceneModeStabilityGuard:
    def __init__(
        self,
        global_config: RecognitionGlobalConfig,
        resolver: SceneModeResolver | None = None,
    ) -> None:
        self.global_config = global_config
        self.resolver = resolver or SceneModeResolver(global_config)

    def observe(
        self,
        session: object,
        *,
        relock_count: int = 0,
        relock_reason: str | None = None,
    ) -> SceneModeStabilityResult:
        if not self.global_config.sceneModeStabilityEnabled or self.global_config.sceneMode != "auto":
            return SceneModeStabilityResult(
                enabled=False,
                stable=False,
                initialMode=None,
                finalMode=None,
                elapsedMs=0,
                windowCount=0,
                transitionObserved=False,
                relockCount=relock_count,
                relockReason=relock_reason,
                transitionTimeout=False,
                reason="disabled",
                finalDecision=None,
                streamType=getattr(session, "streamType", "flv"),
                streamUrl=getattr(session, "streamUrl", ""),
                startFrame=None,
                settledFrame=None,
                windows=[],
                observedFrames=[],
                frameTimestampsMs=[],
            )

        started_at = monotonic()
        deadline = started_at + (self.global_config.sceneModeStabilityTimeoutMs / 1000.0)
        windows: list[SceneModeStabilityWindow] = []
        observed_frames: list[np.ndarray] = []
        frame_timestamps_ms: list[int] = []
        initial_mode: ResolvedSceneMode | None = None
        final_mode: ResolvedSceneMode | None = None
        start_frame: np.ndarray | None = None
        settled_frame: np.ndarray | None = None
        transition_observed = False
        stable_streak = 0
        previous_window: SceneModeStabilityWindow | None = None
        final_decision: SceneModeDecision | None = None
        incomplete_window_observed = False

        while monotonic() < deadline:
            window_frames: list[np.ndarray] = []
            frames_per_window = self.global_config.sceneModeStabilityFramesPerWindow
            while len(window_frames) < frames_per_window and monotonic() < deadline:
                frame_result = session.read_frame_until(deadline)
                if frame_result is None:
                    break
                frame, _ = frame_result
                frame_copy = frame.copy()
                if start_frame is None:
                    start_frame = frame_copy
                settled_frame = frame_copy
                observed_frames.append(frame_copy)
                frame_timestamps_ms.append(max(0, int(round((monotonic() - started_at) * 1000))))
                window_frames.append(frame_copy)

            if not window_frames:
                break
            if len(window_frames) < frames_per_window:
                incomplete_window_observed = True
                break

            decision = self.resolver.resolve_frames(window_frames)
            final_decision = decision
            final_mode = decision.suggestedMode
            if initial_mode is None:
                initial_mode = decision.suggestedMode

            current_window = SceneModeStabilityWindow(
                decision=decision,
                startFrame=window_frames[0],
                endFrame=window_frames[-1],
                frameCount=len(window_frames),
            )
            windows.append(current_window)

            if decision.classification == "ambiguous":
                stable_streak = 0
                previous_window = current_window
                continue

            if previous_window is None or previous_window.decision.classification == "ambiguous":
                stable_streak = 1
            else:
                if current_window.decision.suggestedMode != previous_window.decision.suggestedMode:
                    transition_observed = True
                if self._compatible_windows(previous_window, current_window):
                    stable_streak += 1
                else:
                    stable_streak = 1
                    if current_window.decision.suggestedMode != previous_window.decision.suggestedMode:
                        transition_observed = True
                    elif self._window_delta_exceeded(previous_window.decision.diagnostics, current_window.decision.diagnostics):
                        transition_observed = True

            previous_window = current_window
            if stable_streak >= self.global_config.sceneModeStabilityRequiredWindows:
                return SceneModeStabilityResult(
                    enabled=True,
                    stable=True,
                    initialMode=initial_mode,
                    finalMode=decision.suggestedMode,
                    elapsedMs=max(1, int(round((monotonic() - started_at) * 1000))),
                    windowCount=len(windows),
                    transitionObserved=transition_observed,
                    relockCount=relock_count,
                    relockReason=relock_reason,
                    transitionTimeout=False,
                    reason="scene_mode_stable",
                    finalDecision=decision,
                    streamType=getattr(session, "streamType", "flv"),
                    streamUrl=getattr(session, "streamUrl", ""),
                    startFrame=start_frame,
                    settledFrame=current_window.endFrame.copy(),
                    windows=windows,
                    observedFrames=observed_frames,
                    frameTimestampsMs=frame_timestamps_ms,
                )

        return SceneModeStabilityResult(
            enabled=True,
            stable=False,
            initialMode=initial_mode,
            finalMode=final_mode,
            elapsedMs=max(1, int(round((monotonic() - started_at) * 1000))) if observed_frames else 0,
            windowCount=len(windows),
            transitionObserved=transition_observed or self._saw_mode_switch(windows),
            relockCount=relock_count,
            relockReason=relock_reason,
            transitionTimeout=not incomplete_window_observed,
            reason="scene_mode_probe_incomplete" if incomplete_window_observed else "scene_mode_transition_timeout",
            finalDecision=final_decision,
            streamType=getattr(session, "streamType", "flv"),
            streamUrl=getattr(session, "streamUrl", ""),
            startFrame=start_frame,
            settledFrame=settled_frame,
            windows=windows,
            observedFrames=observed_frames,
            frameTimestampsMs=frame_timestamps_ms,
        )

    def _compatible_windows(
        self,
        previous_window: SceneModeStabilityWindow,
        current_window: SceneModeStabilityWindow,
    ) -> bool:
        previous_decision = previous_window.decision
        current_decision = current_window.decision
        if previous_decision.classification != current_decision.classification:
            return False
        if previous_decision.classification == "ambiguous":
            return False
        return not self._window_delta_exceeded(previous_decision.diagnostics, current_decision.diagnostics)

    def _window_delta_exceeded(
        self,
        previous_diagnostics: SceneModeDiagnostics,
        current_diagnostics: SceneModeDiagnostics,
    ) -> bool:
        brightness_delta = abs(current_diagnostics.brightnessMean - previous_diagnostics.brightnessMean)
        colorfulness_delta = abs(current_diagnostics.colorfulnessMean - previous_diagnostics.colorfulnessMean)
        return (
            brightness_delta > self.global_config.sceneModeStabilityMaxBrightnessDelta
            or colorfulness_delta > self.global_config.sceneModeStabilityMaxColorfulnessDelta
        )

    @staticmethod
    def _saw_mode_switch(windows: list[SceneModeStabilityWindow]) -> bool:
        if len(windows) < 2:
            return False
        previous_mode = windows[0].decision.suggestedMode
        for window in windows[1:]:
            if window.decision.suggestedMode != previous_mode:
                return True
            previous_mode = window.decision.suggestedMode
        return False
