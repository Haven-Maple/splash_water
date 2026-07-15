import { postJson } from "./http";
import type { PresetQueryResponse } from "../types/preset";

export function queryPresets(deviceId: string, channelId: string): Promise<PresetQueryResponse> {
  return postJson<PresetQueryResponse>("/api/preset/query", { deviceId, channelId });
}

export function savePreset(params: {
  deviceId: string;
  channelId: string;
  presetIndex: number;
  presetName: string;
}): Promise<{ accepted: boolean; presetIndex: number }> {
  return postJson<{ accepted: boolean; presetIndex: number }>("/api/preset/save", params);
}

export function turnPreset(params: {
  deviceId: string;
  channelId: string;
  presetIndex: number;
}): Promise<{ accepted: boolean; presetIndex: number }> {
  return postJson<{ accepted: boolean; presetIndex: number }>("/api/preset/turn", params);
}
