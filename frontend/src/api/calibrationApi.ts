import { getJson, postJson } from "./http";
import type {
  CalibrationDraft,
  CalibrationOperationResponse,
  CalibrationToolRuntimeConfig,
} from "../types/calibration";

export function validateCalibrationDraft(payload: CalibrationDraft): string[] {
  const missing: string[] = [];
  if (!payload.deviceId) missing.push("设备 ID 未设置");
  if (!payload.channelId) missing.push("通道未设置");
  if (payload.presetIndex === null || Number.isNaN(payload.presetIndex)) missing.push("预置点索引未设置");
  if (!payload.presetName) missing.push("预置点名称未设置");
  if (!payload.targetId) missing.push("目标 ID 未设置");
  if (!payload.targetName) missing.push("目标名称未设置");
  if (!payload.roi) missing.push("识别 ROI 未设置");
  if (!payload.focusAnchorRoi) missing.push("对焦 ROI 未设置");
  if (!payload.snapshotBase64) missing.push("冻结截图未准备好");
  return missing;
}

export function getCalibrationToolRuntimeConfig(): Promise<CalibrationToolRuntimeConfig> {
  return getJson<CalibrationToolRuntimeConfig>("/api/calibration/runtime-config");
}

export function saveCalibration(payload: CalibrationDraft): Promise<CalibrationOperationResponse> {
  const missing = validateCalibrationDraft(payload);
  if (missing.length > 0) {
    throw new Error(`Missing required calibration fields: ${missing.join(", ")}`);
  }
  return postJson<CalibrationOperationResponse>("/api/calibration/save", payload);
}
