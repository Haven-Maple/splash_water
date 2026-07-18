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
        <h2>视频预览</h2>
        <span className="metaText">{streamType ? `${streamType.toUpperCase()} 流` : "未连接"}</span>
      </div>
      <div className="videoFrame">
        {resolvedStreamUrl ? (
          <video ref={videoRef} muted playsInline autoPlay preload="auto" className="videoElement" />
        ) : (
          <div className="emptyState">请先连接设备并加载视频。</div>
        )}
      </div>
      <div className="buttonRow">
        <button type="button" className="ghostButton" disabled={!resolvedStreamUrl} onClick={handleReconnectPreview}>
          重连视频
        </button>
        <button type="button" disabled={!resolvedStreamUrl || captureDisabled} onClick={handleFreezeFrame}>
          冻结当前画面
        </button>
      </div>
      <div className="previewStatusRow">
        <span className="statusBadge">{captureStatusLabel}</span>
        <span className="metaText">播放器：{playbackState}</span>
      </div>
      {frozenFrame ? <div className="snapshotHint">冻结画面已准备好，请继续标定双 ROI。</div> : null}
    </section>
  );
}
