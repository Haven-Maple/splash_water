import type { CalibrationDraft, Roi } from "../types/calibration";

function drawRoi(context: CanvasRenderingContext2D, roi: Roi, color: string, label: string) {
  context.save();
  context.lineWidth = 4;
  context.strokeStyle = color;
  context.strokeRect(roi.x, roi.y, roi.width, roi.height);
  context.font = "600 18px Microsoft YaHei, sans-serif";
  const text = `${label}  x:${roi.x} y:${roi.y} w:${roi.width} h:${roi.height}`;
  const width = context.measureText(text).width + 18;
  context.fillStyle = color;
  context.fillRect(roi.x, Math.max(0, roi.y - 30), width, 28);
  context.fillStyle = "#ffffff";
  context.fillText(text, roi.x + 9, Math.max(20, roi.y - 10));
  context.restore();
}

export async function createAnnotatedSnapshot(snapshot: string, draft: CalibrationDraft): Promise<string> {
  const image = new Image();
  image.src = snapshot;
  await image.decode();
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  const context = canvas.getContext("2d");
  if (!context) {
    throw new Error("无法创建标注快照画布");
  }
  context.drawImage(image, 0, 0);
  if (draft.roi) {
    drawRoi(context, draft.roi, "#d94a45", "识别 ROI");
  }
  if (draft.focusAnchorRoi) {
    drawRoi(context, draft.focusAnchorRoi, "#2374c6", "对焦 ROI");
  }
  const metadata = `设备 ${draft.deviceId}  通道 ${draft.channelId}  预置点 ${draft.presetIndex ?? "-"}  目标 ${draft.targetName}  ${new Date().toLocaleString()}`;
  context.font = "600 18px Microsoft YaHei, sans-serif";
  const width = Math.min(canvas.width - 24, context.measureText(metadata).width + 28);
  context.fillStyle = "rgba(12, 34, 30, 0.84)";
  context.fillRect(12, canvas.height - 52, width, 40);
  context.fillStyle = "#ffffff";
  context.fillText(metadata, 24, canvas.height - 26);
  return canvas.toDataURL("image/png");
}
