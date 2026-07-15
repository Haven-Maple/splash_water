import { useState } from "react";

import type { CalibrationDraft, Roi } from "../types/calibration";

const defaultDraft: CalibrationDraft = {
  deviceId: "",
  channelId: "0",
  targetId: "",
  targetName: "",
  presetIndex: null,
  presetName: "",
  roi: null,
  focusAnchorRoi: null,
  notes: "",
  snapshotBase64: null,
};

export function useCalibrationDraft() {
  const [draft, setDraft] = useState<CalibrationDraft>(defaultDraft);

  function updateField<K extends keyof CalibrationDraft>(key: K, value: CalibrationDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function setRoi(key: "roi" | "focusAnchorRoi", roi: Roi | null) {
    setDraft((current) => ({ ...current, [key]: roi }));
  }

  function setSnapshot(snapshotBase64: string | null) {
    setDraft((current) => ({ ...current, snapshotBase64 }));
  }

  return {
    draft,
    setDraft,
    setRoi,
    setSnapshot,
    updateField,
  };
}
