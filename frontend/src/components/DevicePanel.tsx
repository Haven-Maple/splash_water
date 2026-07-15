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
  } = props;

  return (
    <section className="panel">
      <div className="panelHeader">
        <h2>Step 1 · Device Connection</h2>
        <span className={`statusBadge status-${onlineStatus === "online" ? "ok" : "idle"}`}>{onlineStatus}</span>
      </div>
      <label>
        <span>Device ID</span>
        <input value={deviceId} onChange={(event) => onDeviceIdChange(event.target.value)} placeholder="DEVICE_ID" />
      </label>
      <label>
        <span>Channel ID</span>
        <input value={channelId} onChange={(event) => onChannelIdChange(event.target.value)} placeholder="0" />
      </label>
      <label>
        <span>Preview Preference</span>
        <select value={streamPreference} onChange={(event) => onStreamPreferenceChange(event.target.value as StreamType)}>
          <option value="flv">FLV First</option>
          <option value="hls">HLS First</option>
        </select>
      </label>
      <div className="buttonRow">
        <button type="button" disabled={loading || !deviceId} onClick={onCheckOnline}>
          Check Device Online
        </button>
        <button type="button" disabled={loading || !deviceId || !channelId} onClick={onLoadStream}>
          Load Preview Stream
        </button>
      </div>
    </section>
  );
}

