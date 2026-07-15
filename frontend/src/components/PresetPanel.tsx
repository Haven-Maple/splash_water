import type { PresetItem } from "../types/preset";

interface PresetPanelProps {
  presetIndex: number | null;
  presetName: string;
  presets: PresetItem[];
  disabled: boolean;
  onPresetIndexChange: (value: string) => void;
  onPresetNameChange: (value: string) => void;
  onRefresh: () => void;
  onSave: () => void;
  onTurn: () => void;
  onPickPreset: (preset: PresetItem) => void;
}

export function PresetPanel(props: PresetPanelProps) {
  const { presetIndex, presetName, presets, disabled, onPresetIndexChange, onPresetNameChange, onRefresh, onSave, onTurn, onPickPreset } =
    props;

  return (
    <section className="panel">
      <div className="panelHeader">
        <h2>Step 3 · Preset Management</h2>
      </div>
      <label>
        <span>Preset Index</span>
        <input
          value={presetIndex ?? ""}
          onChange={(event) => onPresetIndexChange(event.target.value)}
          placeholder="10"
          inputMode="numeric"
        />
      </label>
      <label>
        <span>Preset Name</span>
        <input value={presetName} onChange={(event) => onPresetNameChange(event.target.value)} placeholder="Aerator Preset" />
      </label>
      <div className="buttonRow singleColumnOnTight">
        <button type="button" disabled={disabled} onClick={onRefresh}>
          Query Presets
        </button>
        <button type="button" disabled={disabled || presetIndex === null || !presetName} onClick={onSave}>
          Save Current View As Preset
        </button>
        <button type="button" disabled={disabled || presetIndex === null} onClick={onTurn}>
          Turn To Preset
        </button>
      </div>
      <div className="presetList">
        {presets.length === 0 ? <div className="emptySmall">No preset records loaded.</div> : null}
        {presets.map((preset) => (
          <button key={`${preset.presetIndex}-${preset.presetName ?? "preset"}`} type="button" className="presetItem" onClick={() => onPickPreset(preset)}>
            <strong>Index {preset.presetIndex}</strong>
            <span>{preset.presetName ?? ""}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

