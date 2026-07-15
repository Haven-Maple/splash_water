import type { PtzAction, StepProfile } from "../types/ptz";

interface PtzControlPanelProps {
  stepProfile: StepProfile;
  disabled: boolean;
  onStepProfileChange: (value: StepProfile) => void;
  onMove: (action: PtzAction) => void;
}

const grid: Array<Array<PtzAction | null>> = [
  ["upLeft", "up", "upRight"],
  ["left", null, "right"],
  ["downLeft", "down", "downRight"],
];

const labelMap: Record<PtzAction, string> = {
  up: "Up",
  down: "Down",
  left: "Left",
  right: "Right",
  upLeft: "Up Left",
  upRight: "Up Right",
  downLeft: "Down Left",
  downRight: "Down Right",
  zoomIn: "Zoom In",
  zoomOut: "Zoom Out",
};

const profileLabelMap: Record<StepProfile, string> = {
  small: "Fine",
  medium: "Standard",
  large: "Large",
};

export function PtzControlPanel({ stepProfile, disabled, onStepProfileChange, onMove }: PtzControlPanelProps) {
  return (
    <section className="panel">
      <div className="panelHeader">
        <h2>Step 2 · PTZ Control</h2>
      </div>
      <label>
        <span>Move Step</span>
        <select value={stepProfile} onChange={(event) => onStepProfileChange(event.target.value as StepProfile)}>
          {Object.entries(profileLabelMap).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <div className="ptzGrid">
        {grid.flat().map((action, index) =>
          action ? (
            <button key={action} type="button" disabled={disabled} onClick={() => onMove(action)} className="ptzButton">
              {labelMap[action]}
            </button>
          ) : (
            <div key={`empty-${index}`} className="ptzSpacer" />
          ),
        )}
      </div>
      <div className="buttonRow">
        <button type="button" disabled={disabled} onClick={() => onMove("zoomIn")}>
          Zoom In
        </button>
        <button type="button" disabled={disabled} onClick={() => onMove("zoomOut")}>
          Zoom Out
        </button>
      </div>
    </section>
  );
}

