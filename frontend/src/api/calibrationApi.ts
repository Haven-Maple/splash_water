import { API_BASE_URL, getJson, postJson } from "./http";
import type {
  CalibrationDraft,
  CalibrationHistoryItem,
  CalibrationListItem,
  CalibrationOperationResponse,
  CalibrationRecord,
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

export function saveCalibration(
  payload: CalibrationDraft & { snapshotOriginalBase64?: string | null; snapshotAnnotatedBase64?: string | null },
): Promise<CalibrationOperationResponse> {
  const missing = validateCalibrationDraft(payload);
  if (missing.length > 0) {
    throw new Error(`Missing required calibration fields: ${missing.join(", ")}`);
  }
  return postJson<CalibrationOperationResponse>("/api/calibration/save", payload);
}

export function getCalibration(deviceId: string, presetIndex: number): Promise<CalibrationRecord> {
  return getJson<CalibrationRecord>(`/api/calibration/get?deviceId=${encodeURIComponent(deviceId)}&presetIndex=${presetIndex}`);
}

export async function listCalibrations(): Promise<CalibrationListItem[]> {
  const result = await getJson<{ items: CalibrationListItem[] }>("/api/calibration/list");
  return result.items;
}

export async function getCalibrationHistory(deviceId: string, presetIndex: number): Promise<CalibrationHistoryItem[]> {
  const result = await getJson<{ items: CalibrationHistoryItem[] }>(
    `/api/calibration/history?deviceId=${encodeURIComponent(deviceId)}&presetIndex=${presetIndex}`,
  );
  return result.items;
}

export function restoreCalibration(deviceId: string, presetIndex: number, version: number): Promise<CalibrationOperationResponse> {
  return postJson<CalibrationOperationResponse>("/api/calibration/restore", { deviceId, presetIndex, version });
}

export function downloadCalibrationExport(path: string) {
  window.open(`${API_BASE_URL}${path}`, "_blank", "noopener,noreferrer");
}
