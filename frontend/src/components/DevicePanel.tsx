import type { StreamType } from "../types/stream";

interface DevicePanelProps {
  deviceId: string;
  channelId: string;
  streamPreference: StreamType;
  loading: boolean;
  onlineStatus: string;
  onDeviceIdChange: (value: string) => void;
  onChannelIdChange: (value: string) => void;
  onStreamPreferenceChange: (value: StreamType) => void;
  onCheckOnline: () => void;
  onLoadStream: () => void;
  compact?: boolean;
}

export function DevicePanel(props: DevicePanelProps) {
  const {
    deviceId,
    channelId,
    streamPreference,
    loading,
    onlineStatus,
    onDeviceIdChange,
    onChannelIdChange,
    onStreamPreferenceChange,
    onCheckOnline,
    onLoadStream,
    compact = false,
  } = props;

  return (
    <section className={compact ? "topbarConnectionPanel" : "panel"}>
      <div className="panelHeader">
        <h2>设备连接</h2>
        <span className={`statusBadge status-${onlineStatus === "online" ? "ok" : "idle"}`}>{onlineStatus === "online" ? "已连接" : "未检查"}</span>
      </div>
      <div className="deviceFieldGrid">
        <label>
          <span>设备 ID</span>
          <input value={deviceId} onChange={(event) => onDeviceIdChange(event.target.value)} placeholder="DEVICE_ID" />
        </label>
        <label>
          <span>通道</span>
          <input value={channelId} onChange={(event) => onChannelIdChange(event.target.value)} placeholder="0" />
        </label>
        <label>
          <span>优先拉流方式</span>
          <select value={streamPreference} onChange={(event) => onStreamPreferenceChange(event.target.value as StreamType)}>
            <option value="flv">优先 FLV</option>
            <option value="hls">优先 HLS</option>
          </select>
        </label>
      </div>
      <div className="buttonRow deviceActionRow">
        <button type="button" disabled={loading || !deviceId} onClick={onCheckOnline}>
          检查设备状态
        </button>
        <button type="button" disabled={loading || !deviceId || !channelId} onClick={onLoadStream}>
          加载视频
        </button>
      </div>
    </section>
  );
}
