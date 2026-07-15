export interface PresetItem {
  presetIndex: number;
  presetName?: string | null;
  raw?: unknown;
}

export interface PresetQueryResponse {
  deviceId: string;
  channelId: string;
  presets: PresetItem[];
  raw: unknown;
}
