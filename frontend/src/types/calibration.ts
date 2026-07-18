export interface Roi {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface CalibrationDraft {
  deviceId: string;
  channelId: string;
  targetId: string;
  targetName: string;
  presetIndex: number | null;
  presetName: string;
  roi: Roi | null;
  focusAnchorRoi: Roi | null;
  notes: string;
  snapshotBase64: string | null;
}

export interface CalibrationRecord {
  deviceId: string;
  channelId: string;
  targetId: string;
  targetName: string;
  presetIndex: number;
  presetName: string;
  roi: Roi;
  focusAnchorRoi?: Roi | null;
  notes: string;
  snapshotPath?: string | null;
  snapshotUrl?: string | null;
  snapshotOriginalPath?: string | null;
  snapshotOriginalUrl?: string | null;
  snapshotAnnotatedPath?: string | null;
  snapshotAnnotatedUrl?: string | null;
  version?: number | null;
  legacy: boolean;
  restoredFromVersion?: number | null;
  updatedAt: string;
}

export interface CalibrationListItem {
  deviceId: string;
  presetIndex: number;
  targetName: string;
  updatedAt: string;
  path: string;
  version?: number | null;
  legacy: boolean;
}

export interface CalibrationHistoryItem {
  version: number;
  updatedAt: string;
  targetName: string;
  legacy: boolean;
  restoredFromVersion?: number | null;
  snapshotOriginalUrl?: string | null;
  snapshotAnnotatedUrl?: string | null;
}

export interface CalibrationToolRuntimeConfig {
  ptzExtraSettleMs: number;
  presetTurnSettleMs: number;
  streamCatchupMs: number;
  streamUnreadyDebounceMs: number;
  visualStableWindowMs: number;
  visualStableSampleMs: number;
  visualStableThreshold: number;
  visualStableGraceThreshold: number;
}

export const DEFAULT_CALIBRATION_TOOL_RUNTIME_CONFIG: CalibrationToolRuntimeConfig = {
  ptzExtraSettleMs: 800,
  presetTurnSettleMs: 1800,
  streamCatchupMs: 1000,
  streamUnreadyDebounceMs: 800,
  visualStableWindowMs: 800,
  visualStableSampleMs: 200,
  visualStableThreshold: 6,
  visualStableGraceThreshold: 8,
};

export interface CalibrationOperationResponse {
  saved: boolean;
  record: CalibrationRecord;
}
