import type { ReactNode } from "react";

interface CalibrationWorkbenchProps {
  message: string | null;
  connectionLabel: string;
  captureStatusLabel: string;
  devicePanel: ReactNode;
  presetPanel: ReactNode;
  ptzPanel: ReactNode;
  managementPanel: ReactNode;
  preview: ReactNode;
  roiControls: ReactNode;
  roiCanvas: ReactNode;
  savePanel: ReactNode;
  logs: ReactNode;
}

export function CalibrationWorkbench(props: CalibrationWorkbenchProps) {
  return (
    <main className="calibrationWorkbench">
      <header className="workbenchTopbar">
        <div>
          <p className="workbenchKicker">标定管理</p>
          <h1>增氧机标定工作台</h1>
        </div>
        <div className="workbenchConnection">{props.devicePanel}</div>
        <div className="workbenchStatus" aria-live="polite">
          <span className="statusDot" aria-hidden="true" />
          <span>连接状态：{props.connectionLabel}</span>
          <span className="statusDivider" aria-hidden="true" />
          <span>取景：{props.captureStatusLabel}</span>
        </div>
      </header>

      {props.message ? <div className="workbenchMessage">{props.message}</div> : null}

      <div className="operatorWorkbenchGrid">
        <aside className="workbenchSidebar operatorControls">
          <div className="workbenchSectionHeading"><span>预置点与云台</span><small>01</small></div>
          {props.presetPanel}
          {props.ptzPanel}
          {props.managementPanel}
        </aside>

        <section className="workbenchStage operatorStage">
          <div className="workbenchSectionHeading"><span>稳定画面与标定</span><small>03</small></div>
          {props.preview}
          <div className="workbenchSectionHeading"><span>双 ROI 标定</span><small>04</small></div>
          {props.roiControls}
          {props.roiCanvas}
        </section>

        <aside className="workbenchSidebar operatorReview">
          <div className="workbenchSectionHeading"><span>当前标定</span><small>05</small></div>
          {props.savePanel}
          <details className="diagnosticsDrawer">
            <summary>打开诊断信息</summary>
            {props.logs}
          </details>
        </aside>
      </div>
    </main>
  );
}
