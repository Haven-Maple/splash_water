import type { CalibrationDraft } from "../types/calibration";

interface SaveCalibrationPanelProps {
  draft: CalibrationDraft;
  disabled: boolean;
  saveResult: string | null;
  validationErrors: string[];
  onFieldChange: <K extends keyof CalibrationDraft>(key: K, value: CalibrationDraft[K]) => void;
  onSave: () => void;
  currentVersion?: number | null;
  onNewNext: () => void;
  onExportCurrent: () => void;
  onExportAll: () => void;
  onExportArchive: () => void;
}

export function SaveCalibrationPanel({
  draft,
  disabled,
  saveResult,
  validationErrors,
  onFieldChange,
  onSave,
  currentVersion,
  onNewNext,
  onExportCurrent,
  onExportAll,
  onExportArchive,
}: SaveCalibrationPanelProps) {
  const checklist = [
    ["设备已连接", Boolean(draft.deviceId && draft.channelId)],
    ["已绑定预置点", draft.presetIndex !== null && Boolean(draft.presetName)],
    ["已冻结稳定画面", Boolean(draft.snapshotBase64)],
    ["识别 ROI 已完成", Boolean(draft.roi)],
    ["对焦锚点 ROI 已完成", Boolean(draft.focusAnchorRoi)],
    ["目标信息已填写", Boolean(draft.targetId && draft.targetName)],
  ] as const;

  return (
    <section className="panel">
      <div className="panelHeader">
        <h2>当前标定</h2>
        <span className="statusBadge">{currentVersion ? `当前 v${String(currentVersion).padStart(4, "0")}` : "待保存"}</span>
      </div>
      <div className="calibrationChecklist" aria-label="保存检查单">
        {checklist.map(([label, complete]) => (
          <div key={label} className={complete ? "checklistItem complete" : "checklistItem"}>
            <span aria-hidden="true">{complete ? "✓" : "○"}</span>
            <span>{label}</span>
            <small>{complete ? "已完成" : "未完成"}</small>
          </div>
        ))}
      </div>
      <label>
        <span>目标编号</span>
        <input value={draft.targetId} onChange={(event) => onFieldChange("targetId", event.target.value)} placeholder="AERATOR_001" />
      </label>
      <label>
        <span>目标名称</span>
        <input value={draft.targetName} onChange={(event) => onFieldChange("targetName", event.target.value)} placeholder="1 号增氧机" />
      </label>
      <label>
        <span>备注</span>
        <textarea value={draft.notes} onChange={(event) => onFieldChange("notes", event.target.value)} rows={4} />
      </label>
      <div className="summaryCard">
        <div>设备：{draft.deviceId || "-"}</div>
        <div>通道：{draft.channelId || "-"}</div>
        <div>预置点：{draft.presetIndex ?? "-"} {draft.presetName || ""}</div>
        <div>
          识别 ROI: {draft.roi ? `${draft.roi.x}, ${draft.roi.y}, ${draft.roi.width}, ${draft.roi.height}` : "-"}
        </div>
        <div>
          对焦 ROI:{" "}
          {draft.focusAnchorRoi
            ? `${draft.focusAnchorRoi.x}, ${draft.focusAnchorRoi.y}, ${draft.focusAnchorRoi.width}, ${draft.focusAnchorRoi.height}`
            : "-"}
        </div>
        <div>冻结快照：{draft.snapshotBase64 ? "已就绪" : "未准备"}</div>
      </div>
      {validationErrors.length > 0 ? (
        <div className="validationBox">
          <strong>尚不能保存</strong>
          <ul>
            {validationErrors.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <button type="button" disabled={disabled || validationErrors.length > 0} onClick={onSave}>
        {currentVersion ? "更新标定并创建新版本" : "保存标定"}
      </button>
      {saveResult ? <div className="metaText">{saveResult}</div> : null}
      <div className="secondaryActionStack">
        <button type="button" className="ghostButton" onClick={onNewNext}>新建下一条标定</button>
        <button type="button" className="ghostButton" disabled={draft.presetIndex === null} onClick={onExportCurrent}>导出当前配置</button>
        <button type="button" className="ghostButton" onClick={onExportAll}>导出全部当前配置</button>
        <button type="button" className="ghostButton" onClick={onExportArchive}>导出标定归档</button>
      </div>
    </section>
  );
}
