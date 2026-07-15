import { postJson } from "./http";
import type { DeviceOnlineResponse } from "../types/device";

export function checkDeviceOnline(deviceId: string): Promise<DeviceOnlineResponse> {
  return postJson<DeviceOnlineResponse>("/api/device/online", { deviceId });
}

