from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

from app.config import settings
from app.schemas.calibration import RoiModel
from app.utils.logging_utils import logger
from app.utils.time_utils import iso_utc_now

from inspector.config import RecognitionGlobalConfig
from inspector.debug_artifacts import write_full_frame_ppm, write_motion_debug_pgm, write_representative_roi_ppm
from inspector.frame_alignment import FullFrameAligner
from inspector.models import RecognitionTarget, ReplaySaveState, SampledSequence


class ReplayStore:
    def __init__(self, global_config: RecognitionGlobalConfig) -> None:
        self.global_config = global_config

    def prepare_paths(self, *, target: RecognitionTarget) -> dict[str, Path]:
        replay_root = settings.data_root / self.global_config.replayDirName
        run_name = (
            f"{self._safe_name(target.deviceId)}_"
            f"{self._safe_name(str(target.presetIndex))}_"
            f"{iso_utc_now().replace(':', '-')}"
        )
        run_dir = replay_root / run_name
        return {
            "runDir": run_dir,
            "sequencePath": run_dir / "sequence.npz",
            "metadataPath": run_dir / "metadata.json",
            "statusPath": run_dir / "replay-status.json",
            "streamStartupStartFramePath": run_dir / "stream-startup-start.ppm",
            "streamStartupSettledFramePath": run_dir / "stream-startup-settled.ppm",
            "sceneModeStabilityStartFramePath": run_dir / "scene-mode-stability-start.ppm",
            "sceneModeStabilitySettledFramePath": run_dir / "scene-mode-stability-settled.ppm",
            "representativeFramePath": run_dir / "representative-frame.ppm",
            "roiToleranceSelectedFramePath": run_dir / "roi-tolerance-selected-frame.ppm",
            "sceneProbeStartFramePath": run_dir / "scene-probe-start.ppm",
            "sceneProbeEndFramePath": run_dir / "scene-probe-end.ppm",
            "visualReadinessStartFramePath": run_dir / "visual-readiness-start.ppm",
            "visualReadinessReadyFramePath": run_dir / "visual-readiness-ready.ppm",
            "visualReadinessConfirmFramePath": run_dir / "visual-readiness-confirm.ppm",
            "sampleStartFramePath": run_dir / "sample-start.ppm",
            "sampleQualityAttemptStartFramePath": run_dir / "sample-quality-attempt-start.ppm",
            "sampleQualityDegradedFramePath": run_dir / "sample-quality-degraded.ppm",
            "sampleQualityLastQualifiedFramePath": run_dir / "sample-quality-last-qualified.ppm",
            "sampleQualityAcceptedMiddleFramePath": run_dir / "sample-quality-accepted-middle.ppm",
            "sampleQualityAcceptedEndFramePath": run_dir / "sample-quality-accepted-end.ppm",
            "debugImagePath": run_dir / "motion-debug.pgm",
            "configSnapshotPath": run_dir / "recognition-config.snapshot.json",
            "handoffPath": run_dir / "replay-handoff.json",
            "rawFramesPath": run_dir / "replay-frames.bin",
            "rawTimestampsPath": run_dir / "replay-timestamps.bin",
            "rawReadinessKeyFramesPath": run_dir / "replay-readiness-keyframes.npz",
        }

    def persist_async(
        self,
        *,
        target: RecognitionTarget,
        sequence: SampledSequence,
        config_path: str,
        effective_config: RecognitionGlobalConfig,
        extra_metadata: dict[str, Any],
        readiness_key_frames: dict[str, np.ndarray] | None = None,
    ) -> tuple[dict[str, str], ReplaySaveState]:
        if not self.global_config.saveReplayMaterials:
            return {}, ReplaySaveState(status="disabled", message="Replay save disabled by config")

        paths = self.prepare_paths(target=target)
        paths["runDir"].mkdir(parents=True, exist_ok=True)
        self._write_status(paths["statusPath"], status="pending", message="Replay save is waiting for detached worker")

        if self.global_config.replayAsyncSave:
            try:
                handoff = self._create_handoff(paths, target, sequence, config_path, extra_metadata)
                handoff["effectiveRecognitionConfig"] = effective_config.snapshot()
                handoff["hasReadinessKeyFrames"] = self._write_readiness_keyframes_archive(
                    paths["rawReadinessKeyFramesPath"],
                    readiness_key_frames,
                )
                paths["handoffPath"].write_text(json.dumps(handoff, ensure_ascii=False, indent=2), encoding="utf-8")
                self._spawn_writer(paths["handoffPath"])
                return self._path_strings(paths), ReplaySaveState(
                    status="pending",
                    statusPath=str(paths["statusPath"]),
                    message="Replay save dispatched to detached child process",
                )
            except Exception as error:
                self._write_status(paths["statusPath"], status="failed", message=f"Replay dispatch failed: {error}")
                logger.exception("Replay dispatch failed for %s/%s", target.deviceId, target.presetIndex)
                return self._path_strings(paths), ReplaySaveState(
                    status="failed",
                    statusPath=str(paths["statusPath"]),
                    message=f"Replay dispatch failed: {error}",
                )

        try:
            self._persist_sync(
                paths,
                target,
                sequence,
                config_path,
                effective_config,
                extra_metadata,
                readiness_key_frames=readiness_key_frames,
            )
        except Exception as error:
            self._write_status(paths["statusPath"], status="failed", message=f"Replay save failed: {error}")
            logger.exception("Replay save failed for %s/%s", target.deviceId, target.presetIndex)
            return self._path_strings(paths), ReplaySaveState(
                status="failed",
                statusPath=str(paths["statusPath"]),
                message=f"Replay save failed: {error}",
            )

        return self._path_strings(paths), ReplaySaveState(
            status="ready",
            statusPath=str(paths["statusPath"]),
            message="Replay save completed inline",
        )

    def write_from_handoff(self, handoff_path: Path) -> None:
        handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
        run_dir = Path(handoff["paths"]["runDir"])
        paths = {
            "runDir": run_dir,
            "sequencePath": Path(handoff["paths"]["sequencePath"]),
            "metadataPath": Path(handoff["paths"]["metadataPath"]),
            "statusPath": Path(handoff["paths"]["statusPath"]),
            "streamStartupStartFramePath": Path(handoff["paths"]["streamStartupStartFramePath"]),
            "streamStartupSettledFramePath": Path(handoff["paths"]["streamStartupSettledFramePath"]),
            "sceneModeStabilityStartFramePath": Path(handoff["paths"]["sceneModeStabilityStartFramePath"]),
            "sceneModeStabilitySettledFramePath": Path(handoff["paths"]["sceneModeStabilitySettledFramePath"]),
            "representativeFramePath": Path(handoff["paths"]["representativeFramePath"]),
            "roiToleranceSelectedFramePath": Path(
                handoff["paths"].get("roiToleranceSelectedFramePath", run_dir / "roi-tolerance-selected-frame.ppm")
            ),
            "sceneProbeStartFramePath": Path(handoff["paths"]["sceneProbeStartFramePath"]),
            "sceneProbeEndFramePath": Path(handoff["paths"]["sceneProbeEndFramePath"]),
            "debugImagePath": Path(handoff["paths"]["debugImagePath"]),
            "visualReadinessStartFramePath": Path(handoff["paths"]["visualReadinessStartFramePath"]),
            "visualReadinessReadyFramePath": Path(handoff["paths"]["visualReadinessReadyFramePath"]),
            "visualReadinessConfirmFramePath": Path(handoff["paths"]["visualReadinessConfirmFramePath"]),
            "sampleStartFramePath": Path(handoff["paths"]["sampleStartFramePath"]),
            "sampleQualityAttemptStartFramePath": Path(handoff["paths"]["sampleQualityAttemptStartFramePath"]),
            "sampleQualityDegradedFramePath": Path(handoff["paths"]["sampleQualityDegradedFramePath"]),
            "sampleQualityLastQualifiedFramePath": Path(handoff["paths"]["sampleQualityLastQualifiedFramePath"]),
            "sampleQualityAcceptedMiddleFramePath": Path(handoff["paths"]["sampleQualityAcceptedMiddleFramePath"]),
            "sampleQualityAcceptedEndFramePath": Path(handoff["paths"]["sampleQualityAcceptedEndFramePath"]),
            "configSnapshotPath": Path(handoff["paths"]["configSnapshotPath"]),
            "handoffPath": handoff_path,
            "rawFramesPath": Path(handoff["paths"]["rawFramesPath"]),
            "rawTimestampsPath": Path(handoff["paths"]["rawTimestampsPath"]),
            "rawReadinessKeyFramesPath": Path(handoff["paths"]["rawReadinessKeyFramesPath"]),
        }

        try:
            frames = np.fromfile(
                paths["rawFramesPath"],
                dtype=np.dtype(handoff["rawSequence"]["frames"]["dtype"]),
            ).reshape(tuple(handoff["rawSequence"]["frames"]["shape"]))
            timestamps = np.fromfile(
                paths["rawTimestampsPath"],
                dtype=np.dtype(handoff["rawSequence"]["timestamps"]["dtype"]),
            )

            target = RecognitionTarget.model_validate(handoff["target"])
            effective_config = RecognitionGlobalConfig.model_validate(handoff["effectiveRecognitionConfig"])
            metadata = handoff["sequenceMetadata"]
            sequence = SampledSequence(
                streamType=metadata["streamType"],
                streamUrl=metadata["streamUrl"],
                frames=frames,
                frameTimestampsMs=timestamps.astype(np.int32).tolist(),
                targetFrameCount=int(metadata["targetFrameCount"]),
                sampledFrameCount=int(metadata["sampledFrameCount"]),
                configuredSampleFps=float(metadata["configuredSampleFps"]),
                actualSampleFps=float(metadata["actualSampleFps"]),
                configuredSampleDurationMs=int(metadata["configuredSampleDurationMs"]),
                actualSampleDurationMs=int(metadata["actualSampleDurationMs"]),
                frameWidth=int(metadata["frameWidth"]),
                frameHeight=int(metadata["frameHeight"]),
            )
            readiness_key_frames = self._load_readiness_keyframes_archive(
                paths["rawReadinessKeyFramesPath"],
                handoff.get("hasReadinessKeyFrames", False),
            )
            self._persist_sync(
                paths,
                target,
                sequence,
                handoff["configPath"],
                effective_config,
                handoff["extraMetadata"],
                readiness_key_frames=readiness_key_frames,
            )
        except Exception as error:
            self._write_status(paths["statusPath"], status="failed", message=f"Replay worker failed: {error}")
            logger.exception("Replay worker failed for handoff %s", handoff_path)
            raise
        finally:
            try:
                handoff_path.unlink(missing_ok=True)
            except Exception:
                logger.exception("Failed to remove replay handoff file %s", handoff_path)
            try:
                paths["rawFramesPath"].unlink(missing_ok=True)
            except Exception:
                logger.exception("Failed to remove replay raw frames file %s", paths["rawFramesPath"])
            try:
                paths["rawTimestampsPath"].unlink(missing_ok=True)
            except Exception:
                logger.exception("Failed to remove replay raw timestamps file %s", paths["rawTimestampsPath"])
            try:
                paths["rawReadinessKeyFramesPath"].unlink(missing_ok=True)
            except Exception:
                logger.exception(
                    "Failed to remove replay readiness keyframes file %s",
                    paths["rawReadinessKeyFramesPath"],
                )

    def _create_handoff(
        self,
        paths: dict[str, Path],
        target: RecognitionTarget,
        sequence: SampledSequence,
        config_path: str,
        extra_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        frames = np.ascontiguousarray(sequence.frames)
        timestamps = np.asarray(sequence.frameTimestampsMs, dtype=np.int32)
        frames.tofile(paths["rawFramesPath"])
        timestamps.tofile(paths["rawTimestampsPath"])

        return {
            "configPath": config_path,
            "extraMetadata": extra_metadata,
            "target": target.model_dump(),
            "sequenceMetadata": {
                "streamType": sequence.streamType,
                "streamUrl": sequence.streamUrl,
                "targetFrameCount": sequence.targetFrameCount,
                "sampledFrameCount": sequence.sampledFrameCount,
                "configuredSampleFps": sequence.configuredSampleFps,
                "actualSampleFps": sequence.actualSampleFps,
                "configuredSampleDurationMs": sequence.configuredSampleDurationMs,
                "actualSampleDurationMs": sequence.actualSampleDurationMs,
                "frameWidth": sequence.frameWidth,
                "frameHeight": sequence.frameHeight,
            },
            "paths": self._path_strings(paths),
            "rawSequence": {
                "frames": {
                    "shape": list(frames.shape),
                    "dtype": str(frames.dtype),
                },
                "timestamps": {
                    "shape": list(timestamps.shape),
                    "dtype": str(timestamps.dtype),
                },
            },
        }

    def _spawn_writer(self, handoff_path: Path) -> None:
        creationflags = 0
        popen_kwargs: dict[str, Any] = {}
        if hasattr(subprocess, "DETACHED_PROCESS"):
            creationflags |= subprocess.DETACHED_PROCESS
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags |= subprocess.CREATE_NO_WINDOW
        if creationflags:
            popen_kwargs["creationflags"] = creationflags
        if hasattr(subprocess, "DEVNULL"):
            popen_kwargs["stdin"] = subprocess.DEVNULL
            popen_kwargs["stdout"] = subprocess.DEVNULL
            popen_kwargs["stderr"] = subprocess.DEVNULL
        if hasattr(subprocess, "STARTUPINFO"):
            startup_info = subprocess.STARTUPINFO()
            startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            popen_kwargs["startupinfo"] = startup_info

        subprocess.Popen(
            [
                self._python_executable(),
                "-m",
                "inspector.replay_worker",
                "--handoff",
                str(handoff_path),
            ],
            cwd=str(settings.workspace_root),
            close_fds=True,
            **popen_kwargs,
        )

    def _persist_sync(
        self,
        paths: dict[str, Path],
        target: RecognitionTarget,
        sequence: SampledSequence,
        config_path: str,
        effective_config: RecognitionGlobalConfig,
        extra_metadata: dict[str, Any],
        *,
        readiness_key_frames: dict[str, np.ndarray] | None = None,
    ) -> None:
        run_dir = paths["runDir"]
        run_dir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            paths["sequencePath"],
            frames=sequence.frames,
            frame_timestamps_ms=np.asarray(sequence.frameTimestampsMs, dtype=np.int32),
        )
        metadata = {
            "savedAt": iso_utc_now(),
            "algorithmVersion": effective_config.algorithmVersion,
            "deviceId": target.deviceId,
            "channelId": target.channelId,
            "presetIndex": target.presetIndex,
            "presetName": target.presetName,
            "targetId": target.targetId,
            "targetName": target.targetName,
            "configPath": config_path,
            "streamType": sequence.streamType,
            "streamUrl": sequence.streamUrl,
            "sampledFrameCount": sequence.sampledFrameCount,
            "targetFrameCount": sequence.targetFrameCount,
            "configuredSampleFps": sequence.configuredSampleFps,
            "actualSampleFps": sequence.actualSampleFps,
            "configuredSampleDurationMs": sequence.configuredSampleDurationMs,
            "actualSampleDurationMs": sequence.actualSampleDurationMs,
            "frameWidth": sequence.frameWidth,
            "frameHeight": sequence.frameHeight,
            "roi": target.roi.model_dump(),
            "focusAnchorRoi": target.focusAnchorRoi.model_dump() if target.focusAnchorRoi is not None else None,
            "resolvedFocusAnchorRoi": self._focus_roi(target).model_dump(),
            "focusAnchorRoiSource": extra_metadata.get("focusAnchorRoiSource"),
            "focusAnchorRoiFallbackUsed": extra_metadata.get("focusAnchorRoiFallbackUsed"),
            "effectiveRecognitionConfigPath": str(paths["configSnapshotPath"]),
            "effectiveRecognitionConfigSummary": effective_config.summary(),
            "extra": extra_metadata,
        }
        self._write_effective_config_snapshot(paths["configSnapshotPath"], effective_config)
        paths["metadataPath"].write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_debug_artifacts_best_effort(
            paths=paths,
            target=target,
            sequence=sequence,
            effective_config=effective_config,
            extra_metadata=extra_metadata,
            readiness_key_frames=readiness_key_frames,
        )
        self._write_status(paths["statusPath"], status="ready", message="Replay materials are ready")
        logger.info("Replay materials saved for %s/%s at %s", target.deviceId, target.presetIndex, run_dir)

    def _path_strings(self, paths: dict[str, Path]) -> dict[str, str]:
        return {
            "runDir": str(paths["runDir"]),
            "sequencePath": str(paths["sequencePath"]),
            "metadataPath": str(paths["metadataPath"]),
            "statusPath": str(paths["statusPath"]),
            "streamStartupStartFramePath": str(paths["streamStartupStartFramePath"]),
            "streamStartupSettledFramePath": str(paths["streamStartupSettledFramePath"]),
            "sceneModeStabilityStartFramePath": str(paths["sceneModeStabilityStartFramePath"]),
            "sceneModeStabilitySettledFramePath": str(paths["sceneModeStabilitySettledFramePath"]),
            "representativeFramePath": str(paths["representativeFramePath"]),
            "roiToleranceSelectedFramePath": str(paths["roiToleranceSelectedFramePath"]),
            "sceneProbeStartFramePath": str(paths["sceneProbeStartFramePath"]),
            "sceneProbeEndFramePath": str(paths["sceneProbeEndFramePath"]),
            "visualReadinessStartFramePath": str(paths["visualReadinessStartFramePath"]),
            "visualReadinessReadyFramePath": str(paths["visualReadinessReadyFramePath"]),
            "visualReadinessConfirmFramePath": str(paths["visualReadinessConfirmFramePath"]),
            "sampleStartFramePath": str(paths["sampleStartFramePath"]),
            "sampleQualityAttemptStartFramePath": str(paths["sampleQualityAttemptStartFramePath"]),
            "sampleQualityDegradedFramePath": str(paths["sampleQualityDegradedFramePath"]),
            "sampleQualityLastQualifiedFramePath": str(paths["sampleQualityLastQualifiedFramePath"]),
            "sampleQualityAcceptedMiddleFramePath": str(paths["sampleQualityAcceptedMiddleFramePath"]),
            "sampleQualityAcceptedEndFramePath": str(paths["sampleQualityAcceptedEndFramePath"]),
            "debugImagePath": str(paths["debugImagePath"]),
            "configSnapshotPath": str(paths["configSnapshotPath"]),
            "rawFramesPath": str(paths["rawFramesPath"]),
            "rawTimestampsPath": str(paths["rawTimestampsPath"]),
            "rawReadinessKeyFramesPath": str(paths["rawReadinessKeyFramesPath"]),
        }

    def _write_debug_artifacts_best_effort(
        self,
        *,
        paths: dict[str, Path],
        target: RecognitionTarget,
        sequence: SampledSequence,
        effective_config: RecognitionGlobalConfig,
        extra_metadata: dict[str, Any],
        readiness_key_frames: dict[str, np.ndarray] | None = None,
    ) -> None:
        try:
            focus_roi = self._focus_roi(target)
            readiness_start_frame = None
            readiness_ready_frame = None
            if readiness_key_frames is not None:
                stream_startup_start_frame = readiness_key_frames.get("streamStartupStartFrame")
                if stream_startup_start_frame is not None:
                    write_representative_roi_ppm(
                        paths["streamStartupStartFramePath"],
                        stream_startup_start_frame,
                        focus_roi,
                    )
                stream_startup_settled_frame = readiness_key_frames.get("streamStartupSettledFrame")
                if stream_startup_settled_frame is not None:
                    write_representative_roi_ppm(
                        paths["streamStartupSettledFramePath"],
                        stream_startup_settled_frame,
                        focus_roi,
                    )
                scene_mode_stability_start_frame = readiness_key_frames.get("sceneModeStabilityStartFrame")
                if scene_mode_stability_start_frame is not None:
                    write_full_frame_ppm(
                        paths["sceneModeStabilityStartFramePath"],
                        scene_mode_stability_start_frame,
                    )
                scene_mode_stability_settled_frame = readiness_key_frames.get("sceneModeStabilitySettledFrame")
                if scene_mode_stability_settled_frame is not None:
                    write_full_frame_ppm(
                        paths["sceneModeStabilitySettledFramePath"],
                        scene_mode_stability_settled_frame,
                    )
                scene_probe_start_frame = readiness_key_frames.get("sceneProbeStartFrame")
                if scene_probe_start_frame is not None:
                    write_representative_roi_ppm(
                        paths["sceneProbeStartFramePath"],
                        scene_probe_start_frame,
                        focus_roi,
                    )
                scene_probe_end_frame = readiness_key_frames.get("sceneProbeEndFrame")
                if scene_probe_end_frame is not None:
                    write_representative_roi_ppm(
                        paths["sceneProbeEndFramePath"],
                        scene_probe_end_frame,
                        focus_roi,
                    )
                readiness_start_frame = readiness_key_frames.get("visualReadinessStartFrame")
                if readiness_start_frame is not None:
                    write_representative_roi_ppm(
                        paths["visualReadinessStartFramePath"],
                        readiness_start_frame,
                        focus_roi,
                    )
                readiness_ready_frame = readiness_key_frames.get("visualReadinessReadyFrame")
                if readiness_ready_frame is not None:
                    write_representative_roi_ppm(
                        paths["visualReadinessReadyFramePath"],
                        readiness_ready_frame,
                        focus_roi,
                    )
                readiness_confirm_frame = readiness_key_frames.get("visualReadinessConfirmFrame")
                if readiness_confirm_frame is not None:
                    write_representative_roi_ppm(
                        paths["visualReadinessConfirmFramePath"],
                        readiness_confirm_frame,
                        focus_roi,
                    )
                sample_quality_attempt_start_frame = readiness_key_frames.get("sampleQualityAttemptStartFrame")
                if sample_quality_attempt_start_frame is not None:
                    write_representative_roi_ppm(
                        paths["sampleQualityAttemptStartFramePath"],
                        sample_quality_attempt_start_frame,
                        focus_roi,
                    )
                sample_quality_degraded_frame = readiness_key_frames.get("sampleQualityDegradedFrame")
                if sample_quality_degraded_frame is not None:
                    write_representative_roi_ppm(
                        paths["sampleQualityDegradedFramePath"],
                        sample_quality_degraded_frame,
                        focus_roi,
                    )
                sample_quality_last_qualified_frame = readiness_key_frames.get("sampleQualityLastQualifiedFrame")
                if sample_quality_last_qualified_frame is not None:
                    write_representative_roi_ppm(
                        paths["sampleQualityLastQualifiedFramePath"],
                        sample_quality_last_qualified_frame,
                        focus_roi,
                    )
                sample_quality_accepted_middle_frame = readiness_key_frames.get("sampleQualityAcceptedMiddleFrame")
                if sample_quality_accepted_middle_frame is not None:
                    write_representative_roi_ppm(
                        paths["sampleQualityAcceptedMiddleFramePath"],
                        sample_quality_accepted_middle_frame,
                        focus_roi,
                    )
                sample_quality_accepted_end_frame = readiness_key_frames.get("sampleQualityAcceptedEndFrame")
                if sample_quality_accepted_end_frame is not None:
                    write_representative_roi_ppm(
                        paths["sampleQualityAcceptedEndFramePath"],
                        sample_quality_accepted_end_frame,
                        focus_roi,
                    )

            debug_written = False
            if readiness_start_frame is not None and readiness_ready_frame is not None:
                write_motion_debug_pgm(
                    paths["debugImagePath"],
                    readiness_start_frame,
                    readiness_ready_frame,
                    focus_roi,
                )
                debug_written = True

            sample_start_index = extra_metadata.get("sampleStartFrameIndex")
            if sample_start_index is not None and len(sequence.frames) > 0:
                write_representative_roi_ppm(
                    paths["sampleStartFramePath"],
                    sequence.frames[0],
                    target.roi,
                )

            representative_index = extra_metadata.get("representativeFrameIndex")
            if representative_index is None:
                return

            frame_index = int(representative_index)
            aligned_sequence = FullFrameAligner(effective_config).align(sequence)
            if frame_index < 0 or frame_index >= len(aligned_sequence.alignedFrames):
                logger.warning(
                    "Representative frame index %s is out of range for %s/%s",
                    frame_index,
                    target.deviceId,
                    target.presetIndex,
                )
                return

            write_representative_roi_ppm(
                paths["representativeFramePath"],
                aligned_sequence.alignedFrames[frame_index],
                target.roi,
            )
            selected_roi_payload = extra_metadata.get("roiToleranceSelectedRoi")
            selected_frame_index = extra_metadata.get("roiToleranceSelectedFrameIndex")
            if isinstance(selected_roi_payload, dict) and selected_frame_index is not None:
                selected_roi = RoiModel.model_validate(selected_roi_payload)
                selected_index = int(selected_frame_index)
                if 0 <= selected_index < len(aligned_sequence.alignedFrames):
                    write_representative_roi_ppm(
                        paths["roiToleranceSelectedFramePath"],
                        aligned_sequence.alignedFrames[selected_index],
                        selected_roi,
                    )
            if not debug_written:
                previous_index = max(0, frame_index - 1)
                write_motion_debug_pgm(
                    paths["debugImagePath"],
                    aligned_sequence.alignedFrames[previous_index],
                    aligned_sequence.alignedFrames[frame_index],
                    target.roi,
                )
        except Exception:
            logger.exception("Failed to write debug artifacts for %s/%s", target.deviceId, target.presetIndex)

    @staticmethod
    def _focus_roi(target: RecognitionTarget):
        return target.focusAnchorRoi or target.roi

    @staticmethod
    def _write_readiness_keyframes_archive(
        archive_path: Path,
        readiness_key_frames: dict[str, np.ndarray] | None,
    ) -> bool:
        if not readiness_key_frames:
            return False
        arrays = {
            key: np.ascontiguousarray(value)
            for key, value in readiness_key_frames.items()
            if value is not None
        }
        if not arrays:
            return False
        np.savez_compressed(archive_path, **arrays)
        return True

    @staticmethod
    def _load_readiness_keyframes_archive(
        archive_path: Path,
        has_key_frames: bool,
    ) -> dict[str, np.ndarray] | None:
        if not has_key_frames or not archive_path.exists():
            return None
        with np.load(archive_path) as payload:
            return {key: payload[key] for key in payload.files}

    @staticmethod
    def _write_effective_config_snapshot(config_snapshot_path: Path, effective_config: RecognitionGlobalConfig) -> None:
        config_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        config_snapshot_path.write_text(
            json.dumps(effective_config.snapshot(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _write_status(status_path: Path, *, status: str, message: str) -> None:
        status_path.parent.mkdir(parents=True, exist_ok=True)
        status_path.write_text(
            json.dumps(
                {
                    "updatedAt": iso_utc_now(),
                    "status": status,
                    "message": message,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @staticmethod
    def _python_executable() -> str:
        return sys.executable

    @staticmethod
    def _safe_name(value: str) -> str:
        return "".join(character if character.isalnum() or character in {"-", "_", "."} else "_" for character in value) or "unknown"
