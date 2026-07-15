import type { Roi } from "../types/calibration";

export function clampRoi(roi: Roi, bounds: { width: number; height: number }): Roi {
  const x = Math.max(0, Math.min(bounds.width - 1, roi.x));
  const y = Math.max(0, Math.min(bounds.height - 1, roi.y));
  const width = Math.max(1, Math.min(bounds.width - x, roi.width));
  const height = Math.max(1, Math.min(bounds.height - y, roi.height));
  return { x, y, width, height };
}

export function scaleRoiToNatural(
  roi: Roi,
  displaySize: { width: number; height: number },
  naturalSize: { width: number; height: number },
): Roi {
  const ratioX = naturalSize.width / displaySize.width;
  const ratioY = naturalSize.height / displaySize.height;
  return {
    x: Math.round(roi.x * ratioX),
    y: Math.round(roi.y * ratioY),
    width: Math.round(roi.width * ratioX),
    height: Math.round(roi.height * ratioY),
  };
}

