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
        <h2>预置点</h2>
      </div>
      <label>
        <span>预置点编号</span>
        <input
          value={presetIndex ?? ""}
          onChange={(event) => onPresetIndexChange(event.target.value)}
          placeholder="10"
          inputMode="numeric"
        />
      </label>
      <label>
        <span>预置点名称</span>
        <input value={presetName} onChange={(event) => onPresetNameChange(event.target.value)} placeholder="增氧机预置点" />
      </label>
      <div className="buttonRow singleColumnOnTight">
        <button type="button" disabled={disabled} onClick={onRefresh}>
          刷新预置点
        </button>
        <button type="button" disabled={disabled || presetIndex === null || !presetName} onClick={onSave}>
          保存当前位置为预置点
        </button>
        <button type="button" disabled={disabled || presetIndex === null} onClick={onTurn}>
          转到预置点
        </button>
      </div>
      <div className="presetList">
        {presets.length === 0 ? <div className="emptySmall">尚未加载预置点。</div> : null}
        {presets.map((preset) => (
          <button key={`${preset.presetIndex}-${preset.presetName ?? "preset"}`} type="button" className="presetItem" onClick={() => onPickPreset(preset)}>
            <strong>预置点 {preset.presetIndex}</strong>
            <span>{preset.presetName ?? ""}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
