import type { DebugLogEntry } from "../api/debugApi";

export interface OperationLogItem {
  id: string;
  timestamp: string;
  message: string;
  level: "info" | "error";
}

interface OperationLogPanelProps {
  activityLogs: OperationLogItem[];
  vendorLogs: DebugLogEntry[];
  onRefreshVendorLogs: () => void;
  loading: boolean;
}

export function OperationLogPanel({ activityLogs, vendorLogs, onRefreshVendorLogs, loading }: OperationLogPanelProps) {
  return (
    <section className="panel">
      <div className="panelHeader">
        <h2>诊断信息</h2>
        <button type="button" disabled={loading} onClick={onRefreshVendorLogs} className="ghostButton">
          刷新后端日志
        </button>
      </div>
      <div className="logsGrid">
        <div>
          <h3 className="subheading">页面操作</h3>
          <div className="logList">
            {activityLogs.length === 0 ? <div className="emptySmall">暂无页面操作记录。</div> : null}
            {activityLogs.map((item) => (
              <div key={item.id} className={`logItem ${item.level === "error" ? "logItemError" : ""}`}>
                <div className="logTimestamp">{item.timestamp}</div>
                <div>{item.message}</div>
              </div>
            ))}
          </div>
        </div>
        <div>
          <h3 className="subheading">最近后端日志</h3>
          <div className="logList">
            {vendorLogs.length === 0 ? <div className="emptySmall">尚未加载后端日志。</div> : null}
            {vendorLogs.map((item) => (
              <div key={`${item.traceId}-${item.timestamp}`} className={`logItem ${item.success ? "" : "logItemError"}`}>
                <div className="logTimestamp">{item.timestamp}</div>
                <div>{item.localEndpoint}</div>
                <div className="metaText">{item.success ? "成功" : item.error ?? "失败"}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
