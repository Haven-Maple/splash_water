import { useRef, useState } from "react";
import type { PointerEvent } from "react";

import type { Roi } from "../types/calibration";
import { clampRoi, scaleRoiToNatural } from "../utils/roi";

type RoiKey = "roi" | "focusAnchorRoi";

interface RoiCanvasProps {
  title?: string;
  emptyHint?: string;
  activeHint?: string;
  imageSrc: string | null;
  roi: Roi | null;
  focusAnchorRoi: Roi | null;
  activeRoiKey: RoiKey;
  naturalSize: { width: number; height: number } | null;
  onRoiChange: (key: RoiKey, roi: Roi | null) => void;
}

interface DragState {
  startX: number;
  startY: number;
}

export function RoiCanvas({
  title = "ROI Selection",
  emptyHint = "Freeze a frame before selecting ROI.",
  activeHint = "Drag on frozen frame to create ROI.",
  imageSrc,
  roi,
  focusAnchorRoi,
  activeRoiKey,
  naturalSize,
  onRoiChange,
}: RoiCanvasProps) {
  const imageRef = useRef<HTMLImageElement>(null);
  const [dragState, setDragState] = useState<DragState | null>(null);

  function getRelativePoint(event: PointerEvent<HTMLDivElement>) {
    const image = imageRef.current;
    if (!image) {
      return null;
    }
    const rect = image.getBoundingClientRect();
    return {
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
      width: rect.width,
      height: rect.height,
    };
  }

  function handlePointerDown(event: PointerEvent<HTMLDivElement>) {
    const point = getRelativePoint(event);
    if (!point) {
      return;
    }
    setDragState({ startX: point.x, startY: point.y });
    onRoiChange(activeRoiKey, { x: Math.round(point.x), y: Math.round(point.y), width: 1, height: 1 });
  }

  function handlePointerMove(event: PointerEvent<HTMLDivElement>) {
    if (!dragState) {
      return;
    }
    const point = getRelativePoint(event);
    if (!point) {
      return;
    }
    const next = clampRoi(
      {
        x: Math.round(Math.min(dragState.startX, point.x)),
        y: Math.round(Math.min(dragState.startY, point.y)),
        width: Math.round(Math.abs(point.x - dragState.startX)),
        height: Math.round(Math.abs(point.y - dragState.startY)),
      },
      {
        width: Math.round(point.width),
        height: Math.round(point.height),
      },
    );

    if (naturalSize) {
      onRoiChange(activeRoiKey, scaleRoiToNatural(next, { width: point.width, height: point.height }, naturalSize));
      return;
    }

    onRoiChange(activeRoiKey, next);
  }

  function handlePointerUp() {
    setDragState(null);
  }

  function overlayStyleFor(targetRoi: Roi | null) {
    if (!targetRoi || !naturalSize) {
      return undefined;
    }
    return {
      left: `${(targetRoi.x / naturalSize.width) * 100}%`,
      top: `${(targetRoi.y / naturalSize.height) * 100}%`,
      width: `${(targetRoi.width / naturalSize.width) * 100}%`,
      height: `${(targetRoi.height / naturalSize.height) * 100}%`,
    };
  }

  const detectionOverlayStyle = overlayStyleFor(roi);
  const focusOverlayStyle = overlayStyleFor(focusAnchorRoi);
  const activeRoi = activeRoiKey === "roi" ? roi : focusAnchorRoi;
  const activeLabel = activeRoiKey === "roi" ? "识别 ROI" : "对焦 ROI";

  return (
    <section className="panel">
      <div className="panelHeader">
        <h2>{title}</h2>
      </div>
      {imageSrc ? (
        <>
          <div
            className="roiStage"
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerLeave={handlePointerUp}
          >
            <img ref={imageRef} src={imageSrc} alt="Frozen Frame" className="roiImage" />
            {detectionOverlayStyle ? (
              <div
                className={`roiOverlay detectionOverlay ${activeRoiKey === "roi" ? "active" : ""}`}
                style={detectionOverlayStyle}
              >
                <span className="roiOverlayLabel">识别 ROI</span>
              </div>
            ) : null}
            {focusOverlayStyle ? (
              <div
                className={`roiOverlay focusOverlay ${activeRoiKey === "focusAnchorRoi" ? "active" : ""}`}
                style={focusOverlayStyle}
              >
                <span className="roiOverlayLabel">对焦 ROI</span>
              </div>
            ) : null}
          </div>
          <div className="roiLegend">
            <span className={`roiLegendItem detection ${activeRoiKey === "roi" ? "active" : ""}`}>识别 ROI</span>
            <span className={`roiLegendItem focus ${activeRoiKey === "focusAnchorRoi" ? "active" : ""}`}>对焦 ROI</span>
          </div>
          <div className="metaText">
            {activeRoi
              ? `${activeLabel}: x=${activeRoi.x}, y=${activeRoi.y}, w=${activeRoi.width}, h=${activeRoi.height}`
              : `${activeLabel} 未设置。${activeHint}`}
          </div>
        </>
      ) : (
        <div className="emptyState">{emptyHint}</div>
      )}
    </section>
  );
}
