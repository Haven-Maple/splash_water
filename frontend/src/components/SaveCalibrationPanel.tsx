import type { CalibrationDraft } from "../types/calibration";

interface SaveCalibrationPanelProps {
  draft: CalibrationDraft;
  disabled: boolean;
  saveResult: string | null;
  validationErrors: string[];
  onFieldChange: <K extends keyof CalibrationDraft>(key: K, value: CalibrationDraft[K]) => void;
  onSave: () => void;
}

export function SaveCalibrationPanel({
  draft,
  disabled,
  saveResult,
  validationErrors,
  onFieldChange,
  onSave,
}: SaveCalibrationPanelProps) {
  return (
    <section className="panel">
      <div className="panelHeader">
        <h2>Step 5 路 Save Calibration</h2>
      </div>
      <label>
        <span>Target ID</span>
        <input value={draft.targetId} onChange={(event) => onFieldChange("targetId", event.target.value)} placeholder="AERATOR_001" />
      </label>
      <label>
        <span>Target Name</span>
        <input value={draft.targetName} onChange={(event) => onFieldChange("targetName", event.target.value)} placeholder="Aerator 1" />
      </label>
      <label>
        <span>Notes</span>
        <textarea value={draft.notes} onChange={(event) => onFieldChange("notes", event.target.value)} rows={4} />
      </label>
      <div className="summaryCard">
        <div>Device: {draft.deviceId || "-"}</div>
        <div>Channel: {draft.channelId || "-"}</div>
        <div>Preset Index: {draft.presetIndex ?? "-"}</div>
        <div>Preset Name: {draft.presetName || "-"}</div>
        <div>Target ID: {draft.targetId || "-"}</div>
        <div>Target Name: {draft.targetName || "-"}</div>
        <div>
          识别 ROI: {draft.roi ? `${draft.roi.x}, ${draft.roi.y}, ${draft.roi.width}, ${draft.roi.height}` : "-"}
        </div>
        <div>
          对焦 ROI:{" "}
          {draft.focusAnchorRoi
            ? `${draft.focusAnchorRoi.x}, ${draft.focusAnchorRoi.y}, ${draft.focusAnchorRoi.width}, ${draft.focusAnchorRoi.height}`
            : "-"}
        </div>
        <div>Snapshot: {draft.snapshotBase64 ? "ready" : "missing"}</div>
      </div>
      {validationErrors.length > 0 ? (
        <div className="validationBox">
          <strong>Cannot save yet.</strong>
          <ul>
            {validationErrors.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <button
        type="button"
        disabled={disabled || validationErrors.length > 0}
        onClick={onSave}
      >
        Save Calibration Config
      </button>
      {saveResult ? <div className="metaText">{saveResult}</div> : null}
    </section>
  );
}
