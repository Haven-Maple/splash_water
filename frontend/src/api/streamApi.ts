import { postJson } from "./http";
import type { StreamResponse, StreamType } from "../types/stream";

export function getPreferredStream(deviceId: string, channelId: string, prefer: StreamType): Promise<StreamResponse> {
  return postJson<StreamResponse>("/api/stream/preferred", { deviceId, channelId, prefer });
}

