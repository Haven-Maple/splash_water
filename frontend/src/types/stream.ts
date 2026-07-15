export type StreamType = "flv" | "hls";

export interface StreamResponse {
  streamType: StreamType;
  streamUrl: string;
  fallbackAvailable: boolean;
  raw: unknown;
}

