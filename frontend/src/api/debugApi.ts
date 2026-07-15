import { getJson } from "./http";

export interface DebugLogEntry {
  timestamp: string;
  localEndpoint: string;
  vendorEndpoint: string;
  traceId: string;
  success: boolean;
  responseStatus: number | null;
  requestSummary: Record<string, unknown>;
  responsePayload: unknown;
  error: string | null;
}

export function getRecentLogs(limit = 20): Promise<{ items: DebugLogEntry[] }> {
  return getJson<{ items: DebugLogEntry[] }>(`/api/debug/recent-logs?limit=${limit}`);
}
