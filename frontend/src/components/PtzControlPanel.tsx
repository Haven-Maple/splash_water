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
  up: "上",
  down: "下",
  left: "左",
  right: "右",
  upLeft: "左上",
  upRight: "右上",
  downLeft: "左下",
  downRight: "右下",
  zoomIn: "放大",
  zoomOut: "缩小",
};

const profileLabelMap: Record<StepProfile, string> = {
  small: "微调",
  medium: "标准",
  large: "大步长",
};

export function PtzControlPanel({ stepProfile, disabled, onStepProfileChange, onMove }: PtzControlPanelProps) {
  return (
    <section className="panel">
      <div className="panelHeader">
        <h2>云台微调</h2>
      </div>
      <label>
        <span>移动步长</span>
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
          放大
        </button>
        <button type="button" disabled={disabled} onClick={() => onMove("zoomOut")}>
          缩小
        </button>
      </div>
    </section>
  );
}
