import { useEffect, useMemo, useRef } from "react";

import { API_BASE_URL } from "../api/http";
import { type StreamPlaybackState, useStreamPlayer } from "../hooks/useStreamPlayer";
import { useVisualStability, type VisualStabilityState } from "../hooks/useVisualStability";
import type { StreamType } from "../types/stream";

interface StreamPreviewProps {
  streamUrl: string | null;
  streamType: StreamType | null;
  restartToken?: number;
  frozenFrame: string | null;
  captureDisabled: boolean;
  captureStatusLabel: string;
  playbackState: StreamPlaybackState;
  visualStabilityEnabled: boolean;
  visualStableSampleMs: number;
  visualStableThreshold: number;
  visualStableGraceThreshold: number;
  visualEvaluationWindowSize: number;
  visualRequiredStableCount: number;
  rawMotionScore: number | null;
  smoothedMotionScore: number | null;
  stableCount: number;
  graceCount: number;
  failCount: number;
  onPlayerEvent: (message: string, level?: "info" | "error") => void;
  onPlaybackStateChange: (state: StreamPlaybackState) => void;
  onStreamRefreshNeeded: (reason: string) => void;
  onVisualStabilityChange: (state: VisualStabilityState) => void;
  onCaptureFrame: (payload: { dataUrl: string; naturalWidth: number; naturalHeight: number }) => void;
  onReconnectPreview: () => void;
}

function nowStamp() {
  return new Date().toLocaleTimeString();
}

export function StreamPreview({
  streamUrl,
  streamType,
  restartToken = 0,
  frozenFrame,
  captureDisabled,
  captureStatusLabel,
  playbackState,
  visualStabilityEnabled,
  visualStableSampleMs,
  visualStableThreshold,
  visualStableGraceThreshold,
  visualEvaluationWindowSize,
  visualRequiredStableCount,
  rawMotionScore,
  smoothedMotionScore,
  stableCount,
  graceCount,
  failCount,
  onPlayerEvent,
  onPlaybackStateChange,
  onStreamRefreshNeeded,
  onVisualStabilityChange,
  onCaptureFrame,
  onReconnectPreview,
}: StreamPreviewProps) {
  const videoRef = useRef<HTMLVideoElement>(null);

  const resolvedStreamUrl = useMemo(() => {
    if (!streamUrl) {
      return null;
    }
    if (streamUrl.startsWith("http://") || streamUrl.startsWith("https://")) {
      return streamUrl;
    }
    return `${API_BASE_URL}${streamUrl}`;
  }, [streamUrl]);

  useStreamPlayer({
    videoRef,
    streamUrl: resolvedStreamUrl,
    streamType,
    restartToken,
    onPlaybackStateChange,
    onPlayerEvent,
    onStreamRefreshNeeded,
  });

  const visualStability = useVisualStability({
    videoRef,
    enabled: visualStabilityEnabled,
    sampleMs: visualStableSampleMs,
    threshold: visualStableThreshold,
    graceThreshold: visualStableGraceThreshold,
    evaluationWindowSize: visualEvaluationWindowSize,
    requiredStableCount: visualRequiredStableCount,
    onEvent: onPlayerEvent,
  });

  useEffect(() => {
    onVisualStabilityChange(visualStability);
  }, [
    onVisualStabilityChange,
    visualStability.failCount,
    visualStability.graceCount,
    visualStability.rawMotionScore,
    visualStability.smoothedMotionScore,
    visualStability.stableCount,
    visualStability.visualStable,
  ]);

  function handleReconnectPreview() {
    onPlayerEvent(`Preview manual reconnect requested at ${nowStamp()}`);
    onReconnectPreview();
  }

  function handleFreezeFrame() {
    const video = videoRef.current;
    if (!video || !video.videoWidth || !video.videoHeight) {
      return;
    }

    onPlayerEvent(`freeze requested at ${nowStamp()}`);

    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const context = canvas.getContext("2d");
    if (!context) {
      return;
    }

    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    onPlayerEvent(`frame captured at ${nowStamp()}`);
    onCaptureFrame({
      dataUrl: canvas.toDataURL("image/png"),
      naturalWidth: canvas.width,
      naturalHeight: canvas.height,
    });
  }

  return (
    <section className="panel previewPanel">
      <div className="panelHeader">
        <h2>Step 4 Preview and ROI</h2>
        <span className="metaText">{streamType ? `${streamType.toUpperCase()} stream` : "disconnected"}</span>
      </div>
      <div className="videoFrame">
        {resolvedStreamUrl ? (
          <video ref={videoRef} muted playsInline autoPlay preload="auto" className="videoElement" />
        ) : (
          <div className="emptyState">Connect device and load preview stream first.</div>
        )}
      </div>
      <div className="buttonRow">
        <button type="button" className="ghostButton" disabled={!resolvedStreamUrl} onClick={handleReconnectPreview}>
          Reconnect Preview
        </button>
        <button type="button" disabled={!resolvedStreamUrl || captureDisabled} onClick={handleFreezeFrame}>
          Freeze Current Frame
        </button>
      </div>
      <div className="previewStatusRow">
        <span className="statusBadge">{captureStatusLabel}</span>
        <span className="metaText">player: {playbackState}</span>
      </div>
      <div className="previewStatusRow">
        <span className="metaText">raw motion: {rawMotionScore === null ? "-" : rawMotionScore.toFixed(2)}</span>
        <span className="metaText">smoothed: {smoothedMotionScore === null ? "-" : smoothedMotionScore.toFixed(2)}</span>
      </div>
      <div className="previewStatusRow">
        <span className="metaText">stable/grace/fail: {stableCount}/{graceCount}/{failCount}</span>
        <span className="metaText">stability mode: full frame</span>
      </div>
      {frozenFrame ? <div className="snapshotHint">Frozen frame ready. Draw ROI below.</div> : null}
    </section>
  );
}
