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
        <h2>Operation Logs</h2>
        <button type="button" disabled={loading} onClick={onRefreshVendorLogs} className="ghostButton">
          Refresh Vendor Logs
        </button>
      </div>
      <div className="logsGrid">
        <div>
          <h3 className="subheading">Page Activity</h3>
          <div className="logList">
            {activityLogs.length === 0 ? <div className="emptySmall">No page activity yet.</div> : null}
            {activityLogs.map((item) => (
              <div key={item.id} className={`logItem ${item.level === "error" ? "logItemError" : ""}`}>
                <div className="logTimestamp">{item.timestamp}</div>
                <div>{item.message}</div>
              </div>
            ))}
          </div>
        </div>
        <div>
          <h3 className="subheading">Recent Backend Logs</h3>
          <div className="logList">
            {vendorLogs.length === 0 ? <div className="emptySmall">No backend logs loaded.</div> : null}
            {vendorLogs.map((item) => (
              <div key={`${item.traceId}-${item.timestamp}`} className={`logItem ${item.success ? "" : "logItemError"}`}>
                <div className="logTimestamp">{item.timestamp}</div>
                <div>{item.localEndpoint}</div>
                <div className="metaText">{item.success ? "success" : item.error ?? "failed"}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

