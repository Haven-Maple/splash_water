import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  downloadCalibrationExport,
  getCalibration,
  getCalibrationHistory,
  getCalibrationToolRuntimeConfig,
  listCalibrations,
  restoreCalibration,
  saveCalibration,
  validateCalibrationDraft,
} from "../api/calibrationApi";
import { API_BASE_URL } from "../api/http";
import { getRecentLogs, type DebugLogEntry } from "../api/debugApi";
import { checkDeviceOnline } from "../api/deviceApi";
import { queryPresets, savePreset, turnPreset } from "../api/presetApi";
import { movePtz } from "../api/ptzApi";
import { getPreferredStream } from "../api/streamApi";
import { DevicePanel } from "../components/DevicePanel";
import { CalibrationWorkbench } from "../components/CalibrationWorkbench";
import { OperationLogPanel, type OperationLogItem } from "../components/OperationLogPanel";
import { PresetPanel } from "../components/PresetPanel";
import { PtzControlPanel } from "../components/PtzControlPanel";
import { RoiCanvas } from "../components/RoiCanvas";
import { SaveCalibrationPanel } from "../components/SaveCalibrationPanel";
import { StreamPreview } from "../components/StreamPreview";
import type { StreamPlaybackState } from "../hooks/useStreamPlayer";
import type { VisualStabilityState } from "../hooks/useVisualStability";
import { useCalibrationDraft } from "../hooks/useCalibrationDraft";
import {
  DEFAULT_CALIBRATION_TOOL_RUNTIME_CONFIG,
  type CalibrationDraft,
  type CalibrationHistoryItem,
  type CalibrationListItem,
  type CalibrationToolRuntimeConfig,
} from "../types/calibration";
import type { PresetItem } from "../types/preset";
import type { StepProfile } from "../types/ptz";
import type { StreamResponse, StreamType } from "../types/stream";
import { createAnnotatedSnapshot } from "../utils/annotatedSnapshot";

type CaptureGatePhase =
  | "idle"
  | "commandAccepted"
  | "mechanicalSettling"
  | "streamCatchingUp"
  | "streamUnreadyPending"
  | "visualStabilizing"
  | "readyForCapture";

interface StreamSession {
  deviceId: string;
  channelId: string;
  confirmedPresetIndex: number | null;
}

const DEFAULT_VISUAL_STATE: VisualStabilityState = {
  visualStable: false,
  rawMotionScore: null,
  smoothedMotionScore: null,
  stableCount: 0,
  graceCount: 0,
  failCount: 0,
};

const STREAM_REFRESH_COOLDOWN_MS = 3000;

function nowLabel() {
  return new Date().toLocaleTimeString();
}

function createEmptyDraft(deviceId = "", channelId = "0"): CalibrationDraft {
  return {
    deviceId,
    channelId,
    targetId: "",
    targetName: "",
    presetIndex: null,
    presetName: "",
    roi: null,
    focusAnchorRoi: null,
    notes: "",
    snapshotBase64: null,
  };
}

async function fetchSnapshotDataUrl(url: string | null | undefined): Promise<string | null> {
  if (!url) return null;
  const response = await fetch(url.startsWith("http") ? url : `${API_BASE_URL}${url}`);
  if (!response.ok) throw new Error("无法读取已保存的原始快照");
  const blob = await response.blob();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(typeof reader.result === "string" ? reader.result : null));
    reader.addEventListener("error", () => reject(reader.error ?? new Error("无法读取快照")));
    reader.readAsDataURL(blob);
  });
}

export function CalibrationPage() {
  const { draft, setDraft, setRoi, setSnapshot, updateField } = useCalibrationDraft();
  const [onlineStatus, setOnlineStatus] = useState("idle");
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [stream, setStream] = useState<StreamResponse | null>(null);
  const [streamSession, setStreamSession] = useState<StreamSession | null>(null);
  const [streamPreference, setStreamPreference] = useState<StreamType>("flv");
  const [streamRestartToken, setStreamRestartToken] = useState(0);
  const [stepProfile, setStepProfile] = useState<StepProfile>("small");
  const [presets, setPresets] = useState<PresetItem[]>([]);
  const [frozenFrame, setFrozenFrame] = useState<string | null>(null);
  const [naturalSize, setNaturalSize] = useState<{ width: number; height: number } | null>(null);
  const [saveResult, setSaveResult] = useState<string | null>(null);
  const [activityLogs, setActivityLogs] = useState<OperationLogItem[]>([]);
  const [vendorLogs, setVendorLogs] = useState<DebugLogEntry[]>([]);
  const [activeRoiKey, setActiveRoiKey] = useState<"roi" | "focusAnchorRoi">("roi");
  const [playbackState, setPlaybackState] = useState<StreamPlaybackState>("disconnected");
  const [captureGatePhase, setCaptureGatePhase] = useState<CaptureGatePhase>("idle");
  const [visualStability, setVisualStability] = useState<VisualStabilityState>(DEFAULT_VISUAL_STATE);
  const [runtimeConfig, setRuntimeConfig] = useState<CalibrationToolRuntimeConfig>(DEFAULT_CALIBRATION_TOOL_RUNTIME_CONFIG);
  const [runtimeConfigStatus, setRuntimeConfigStatus] = useState<"loading" | "ready" | "error">("loading");
  const [calibrationItems, setCalibrationItems] = useState<CalibrationListItem[]>([]);
  const [historyItems, setHistoryItems] = useState<CalibrationHistoryItem[]>([]);
  const [currentVersion, setCurrentVersion] = useState<number | null>(null);
  const [cleanDraftSignature, setCleanDraftSignature] = useState(() => JSON.stringify(createEmptyDraft()));

  const playbackStateRef = useRef<StreamPlaybackState>("disconnected");
  const gateSessionRef = useRef(0);
  const gateTimerRef = useRef<number | null>(null);
  const streamCatchupElapsedRef = useRef(false);
  const unreadyDebounceTimerRef = useRef<number | null>(null);
  const phaseBeforeUnreadyRef = useRef<CaptureGatePhase | null>(null);
  const visualStartLoggedRef = useRef(false);
  const streamRefreshLastRequestedAtRef = useRef(0);
  const streamRefreshInFlightRef = useRef<Promise<void> | null>(null);
  const pendingPresetIndexRef = useRef<number | null>(null);

  const validationErrors = useMemo(() => validateCalibrationDraft(draft), [draft]);
  const hasUnsavedChanges = useMemo(() => JSON.stringify(draft) !== cleanDraftSignature, [cleanDraftSignature, draft]);
  const runtimeConfigReady = runtimeConfigStatus === "ready";
  const evaluationWindowSize = useMemo(
    () => Math.max(5, Math.ceil(runtimeConfig.visualStableWindowMs / Math.max(runtimeConfig.visualStableSampleMs, 1))),
    [runtimeConfig.visualStableSampleMs, runtimeConfig.visualStableWindowMs],
  );
  const requiredStableCount = useMemo(() => Math.max(4, evaluationWindowSize - 1), [evaluationWindowSize]);
  const canFreezeFrame =
    runtimeConfigReady &&
    Boolean(stream?.streamUrl) &&
    streamSession?.deviceId === draft.deviceId &&
    streamSession?.channelId === draft.channelId &&
    draft.presetIndex !== null &&
    streamSession?.confirmedPresetIndex === draft.presetIndex &&
    captureGatePhase === "readyForCapture" &&
    playbackState === "ready";
  const visualStabilityEnabled = captureGatePhase === "visualStabilizing" && playbackState === "ready";

  const captureStatusLabel = useMemo(() => {
    if (!stream?.streamUrl) {
      return "未连接";
    }

    switch (captureGatePhase) {
      case "commandAccepted":
        return "命令已发送";
      case "mechanicalSettling":
        return "机械稳定中";
      case "streamCatchingUp":
        return "直播追帧中";
      case "streamUnreadyPending":
        return "直播短抖动恢复中";
      case "visualStabilizing":
        return "视觉判稳中";
      case "readyForCapture":
        return "可抓图";
      default:
        return playbackState === "ready" ? "播放中" : "等待播放";
    }
  }, [captureGatePhase, playbackState, stream?.streamUrl]);

  const addLog = useCallback((messageText: string, level: "info" | "error" = "info") => {
    setActivityLogs((current) =>
      [
        {
          id: `${Date.now()}-${Math.random()}`,
          timestamp: nowLabel(),
          message: messageText,
          level,
        },
        ...current,
      ].slice(0, 40),
    );
  }, []);

  const clearPreviewSession = useCallback(
    (reason: string) => {
      setStream(null);
      setStreamSession(null);
      setStreamRestartToken((current) => current + 1);
      setPlaybackState("disconnected");
      setCaptureGatePhase("idle");
      setVisualStability(DEFAULT_VISUAL_STATE);
      addLog(reason);
    },
    [addLog],
  );

  const applyStreamResponse = useCallback(
    (result: StreamResponse, session: StreamSession, successMessage: string, successLog: string) => {
      setStream(result);
      setStreamSession(session);
      setStreamRestartToken((current) => current + 1);
      setPlaybackState("loading");
      setMessage(successMessage);
      addLog(successLog);
    },
    [addLog],
  );

  const refreshPreviewStream = useCallback(
    async ({
      reason,
      honorCooldown,
    }: {
      reason: string;
      honorCooldown: boolean;
    }) => {
      if (!draft.deviceId || !draft.channelId) {
        const messageText = "Preview stream refresh failed: deviceId and channelId are required";
        addLog(messageText, "error");
        throw new Error(messageText);
      }

      const now = Date.now();
      if (honorCooldown && now - streamRefreshLastRequestedAtRef.current < STREAM_REFRESH_COOLDOWN_MS) {
        addLog("Preview stream refresh skipped: cooldown active");
        return;
      }

      if (streamRefreshInFlightRef.current) {
        addLog("Preview stream refresh skipped: already in progress");
        return streamRefreshInFlightRef.current;
      }

      streamRefreshLastRequestedAtRef.current = now;
      addLog(`Preview stream refresh requested: ${reason}`);

      const requestedSession = { deviceId: draft.deviceId, channelId: draft.channelId, confirmedPresetIndex: null };
      const refreshTask = (async () => {
        const result = await getPreferredStream(requestedSession.deviceId, requestedSession.channelId, streamPreference);
        applyStreamResponse(result, requestedSession, `Connected ${result.streamType.toUpperCase()} stream`, "Preview stream refresh succeeded");
      })()
        .catch((error: unknown) => {
          const messageText = error instanceof Error ? error.message : String(error);
          addLog(`Preview stream refresh failed: ${messageText}`, "error");
          throw error;
        })
        .finally(() => {
          streamRefreshInFlightRef.current = null;
        });

      streamRefreshInFlightRef.current = refreshTask;
      return refreshTask;
    },
    [addLog, applyStreamResponse, draft.channelId, draft.deviceId, streamPreference],
  );

  const clearGateTimer = useCallback(() => {
    if (gateTimerRef.current !== null) {
      window.clearTimeout(gateTimerRef.current);
      gateTimerRef.current = null;
    }
  }, []);

  const clearUnreadyDebounce = useCallback(() => {
    if (unreadyDebounceTimerRef.current !== null) {
      window.clearTimeout(unreadyDebounceTimerRef.current);
      unreadyDebounceTimerRef.current = null;
    }
    phaseBeforeUnreadyRef.current = null;
  }, []);

  const beginStreamCatchup = useCallback(
    (sessionId: number) => {
      if (sessionId !== gateSessionRef.current) {
        return;
      }

      clearGateTimer();
      clearUnreadyDebounce();
      streamCatchupElapsedRef.current = false;
      setCaptureGatePhase("streamCatchingUp");
      setMessage("直播追帧中");
      addLog(`Stream catch-up wait started: ${runtimeConfig.streamCatchupMs} ms`);

      gateTimerRef.current = window.setTimeout(() => {
        if (sessionId !== gateSessionRef.current) {
          return;
        }

        streamCatchupElapsedRef.current = true;
        gateTimerRef.current = null;
        addLog("Stream catch-up wait finished");

        if (playbackStateRef.current === "ready") {
          setCaptureGatePhase("visualStabilizing");
          setMessage("视觉判稳中");
          setVisualStability(DEFAULT_VISUAL_STATE);
          visualStartLoggedRef.current = false;
        }
      }, runtimeConfig.streamCatchupMs);
    },
    [addLog, clearGateTimer, clearUnreadyDebounce, runtimeConfig.streamCatchupMs],
  );

  const handlePlayerEvent = useCallback(
    (messageText: string, level: "info" | "error" = "info") => {
      addLog(messageText, level);
      const canFallbackToCatchup =
        captureGatePhase === "visualStabilizing" ||
        captureGatePhase === "readyForCapture" ||
        captureGatePhase === "streamUnreadyPending";
      if (messageText.includes("Preview auto recover: player reload") && canFallbackToCatchup) {
        beginStreamCatchup(gateSessionRef.current);
      }
    },
    [addLog, beginStreamCatchup, captureGatePhase],
  );

  const handleStreamRefreshNeeded = useCallback(
    (reason: string) => {
      void refreshPreviewStream({
        reason: `flv network error (${reason})`,
        honorCooldown: true,
      });
    },
    [refreshPreviewStream],
  );

  const beginMechanicalSettling = useCallback(
    (sessionId: number, durationMs: number) => {
      if (sessionId !== gateSessionRef.current) {
        return;
      }

      clearGateTimer();
      clearUnreadyDebounce();
      setCaptureGatePhase("mechanicalSettling");
      setMessage("机械稳定中");
      addLog(`Mechanical settle wait started: ${durationMs} ms`);

      gateTimerRef.current = window.setTimeout(() => {
        if (sessionId !== gateSessionRef.current) {
          return;
        }

        gateTimerRef.current = null;
        addLog("Mechanical settle wait finished");
        beginStreamCatchup(sessionId);
      }, durationMs);
    },
    [addLog, beginStreamCatchup, clearGateTimer, clearUnreadyDebounce],
  );

  const beginCaptureGate = useCallback(
    (commandAcceptedLog: string, mechanicalDurationMs: number, pendingPresetIndex: number | null = null) => {
      gateSessionRef.current += 1;
      const sessionId = gateSessionRef.current;
      pendingPresetIndexRef.current = pendingPresetIndex;
      setStreamSession((current) => (current ? { ...current, confirmedPresetIndex: null } : current));

      clearGateTimer();
      clearUnreadyDebounce();
      streamCatchupElapsedRef.current = false;
      setVisualStability(DEFAULT_VISUAL_STATE);
      setCaptureGatePhase("commandAccepted");
      setMessage("命令已发送");
      addLog(commandAcceptedLog);

      gateTimerRef.current = window.setTimeout(() => {
        if (sessionId !== gateSessionRef.current) {
          return;
        }

        gateTimerRef.current = null;
        beginMechanicalSettling(sessionId, mechanicalDurationMs);
      }, 0);
    },
    [addLog, beginMechanicalSettling, clearGateTimer, clearUnreadyDebounce],
  );

  async function runTask(task: () => Promise<void>) {
    setLoading(true);
    setMessage(null);
    try {
      await task();
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      setMessage(errorMessage);
      addLog(errorMessage, "error");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    playbackStateRef.current = playbackState;
  }, [playbackState]);

  useEffect(
    () => () => {
      clearGateTimer();
      clearUnreadyDebounce();
    },
    [clearGateTimer, clearUnreadyDebounce],
  );

  useEffect(() => {
    let cancelled = false;

    async function loadRuntimeConfig() {
      try {
        const result = await getCalibrationToolRuntimeConfig();
        if (cancelled) {
          return;
        }
        setRuntimeConfig(result);
        setRuntimeConfigStatus("ready");
        addLog("Loaded calibration runtime config");
      } catch (error) {
        if (cancelled) {
          return;
        }
        setRuntimeConfigStatus("error");
        const errorMessage = error instanceof Error ? error.message : String(error);
        addLog(`Calibration runtime config load failed, using defaults: ${errorMessage}`, "error");
        setMessage("Calibration runtime config load failed. PTZ / preset / capture remain disabled until config is available.");
      }
    }

    void loadRuntimeConfig();

    return () => {
      cancelled = true;
    };
  }, [addLog]);

  useEffect(() => {
    clearGateTimer();
    clearUnreadyDebounce();
    streamCatchupElapsedRef.current = false;
    setCaptureGatePhase("idle");
    setVisualStability(DEFAULT_VISUAL_STATE);
  }, [clearGateTimer, clearUnreadyDebounce, stream?.streamUrl]);

  useEffect(() => {
    if (captureGatePhase === "streamCatchingUp" && streamCatchupElapsedRef.current && playbackState === "ready") {
      setCaptureGatePhase("visualStabilizing");
      setMessage("视觉判稳中");
      setVisualStability(DEFAULT_VISUAL_STATE);
      visualStartLoggedRef.current = false;
    }
  }, [captureGatePhase, playbackState]);

  useEffect(() => {
    if (captureGatePhase !== "visualStabilizing" || playbackState !== "ready") {
      return;
    }
    if (!visualStartLoggedRef.current) {
      addLog("Visual stability check started");
      visualStartLoggedRef.current = true;
    }
  }, [addLog, captureGatePhase, playbackState]);

  useEffect(() => {
    const phaseNeedsDebounce = captureGatePhase === "visualStabilizing" || captureGatePhase === "readyForCapture";

    if (playbackState === "ready") {
      if (captureGatePhase === "streamUnreadyPending") {
        const restorePhase = phaseBeforeUnreadyRef.current ?? "visualStabilizing";
        addLog("Playback recovered within debounce");
        clearUnreadyDebounce();
        setCaptureGatePhase(restorePhase);
        setMessage(restorePhase === "readyForCapture" ? "可抓图" : "视觉判稳中");
      }
      return;
    }

    if (!phaseNeedsDebounce || unreadyDebounceTimerRef.current !== null) {
      return;
    }

    phaseBeforeUnreadyRef.current = captureGatePhase;
    setCaptureGatePhase("streamUnreadyPending");
    setMessage("直播短抖动恢复中");
    addLog("Playback left ready state, debounce started");

    unreadyDebounceTimerRef.current = window.setTimeout(() => {
      unreadyDebounceTimerRef.current = null;
      addLog("Playback unready debounce exceeded, return to stream catch-up");
      beginStreamCatchup(gateSessionRef.current);
    }, runtimeConfig.streamUnreadyDebounceMs);
  }, [
    addLog,
    beginStreamCatchup,
    captureGatePhase,
    clearUnreadyDebounce,
    playbackState,
    runtimeConfig.streamUnreadyDebounceMs,
  ]);

  useEffect(() => {
    if (captureGatePhase !== "visualStabilizing" || playbackState !== "ready" || !visualStability.visualStable) {
      return;
    }

    setCaptureGatePhase("readyForCapture");
    setStreamSession((current) =>
      current ? { ...current, confirmedPresetIndex: pendingPresetIndexRef.current } : current,
    );
    setMessage("可抓图");
    addLog("Visual stability passed");
    addLog("Capture gate opened");
  }, [addLog, captureGatePhase, playbackState, visualStability.visualStable]);

  function parsePresetIndex(value: string) {
    if (!value.trim()) {
      updateField("presetIndex", null);
      setStreamSession((current) => (current ? { ...current, confirmedPresetIndex: null } : current));
      return;
    }

    const next = Number.parseInt(value, 10);
    const nextPresetIndex = Number.isNaN(next) ? null : next;
    if (nextPresetIndex !== draft.presetIndex) {
      setStreamSession((current) => (current ? { ...current, confirmedPresetIndex: null } : current));
    }
    updateField("presetIndex", nextPresetIndex);
  }

  const activeRoiLabel = activeRoiKey === "roi" ? "识别 ROI" : "对焦 ROI";

  const invalidateFrozenCalibration = useCallback(
    (reason: string) => {
      setFrozenFrame(null);
      setNaturalSize(null);
      setSnapshot(null);
      setRoi("roi", null);
      setRoi("focusAnchorRoi", null);
      setCurrentVersion(null);
      setMessage(reason);
      addLog(reason);
    },
    [addLog, setRoi, setSnapshot],
  );

  const confirmDiscardDraft = useCallback(
    (nextAction: string) => !hasUnsavedChanges || window.confirm(`当前有未保存的标定改动。${nextAction}会丢弃这些改动，是否继续？`),
    [hasUnsavedChanges],
  );

  const refreshCalibrationItems = useCallback(async () => {
    setCalibrationItems(await listCalibrations());
  }, []);

  const loadExistingCalibration = useCallback(
    async (deviceId: string, presetIndex: number) => {
      if (!confirmDiscardDraft("加载已有标定")) return;
      const record = await getCalibration(deviceId, presetIndex);
      if (streamSession?.deviceId !== record.deviceId || streamSession?.channelId !== record.channelId) {
        clearPreviewSession("已加载其他设备或通道的标定，原视频预览已关闭；请重新连接匹配设备后再冻结画面。");
      } else {
        setStreamSession((current) => (current ? { ...current, confirmedPresetIndex: null } : current));
        setCaptureGatePhase("idle");
        addLog("Loaded calibration invalidated the confirmed preset; turn to the preset before freezing.");
      }
      const snapshot = await fetchSnapshotDataUrl(record.snapshotOriginalUrl ?? record.snapshotUrl);
      const nextDraft: CalibrationDraft = {
        deviceId: record.deviceId,
        channelId: record.channelId,
        targetId: record.targetId,
        targetName: record.targetName,
        presetIndex: record.presetIndex,
        presetName: record.presetName,
        roi: record.roi,
        focusAnchorRoi: record.focusAnchorRoi ?? null,
        notes: record.notes,
        snapshotBase64: snapshot,
      };
      setDraft(nextDraft);
      setFrozenFrame(snapshot);
      if (snapshot) {
        const image = new Image();
        image.src = snapshot;
        await image.decode();
        setNaturalSize({ width: image.naturalWidth, height: image.naturalHeight });
      } else {
        setNaturalSize(null);
      }
      setCurrentVersion(record.version ?? null);
      setCleanDraftSignature(JSON.stringify(nextDraft));
      setHistoryItems(await getCalibrationHistory(record.deviceId, record.presetIndex));
      setMessage(`已加载 ${record.targetName} 的标定${record.version ? ` v${String(record.version).padStart(4, "0")}` : "（旧版）"}`);
      addLog(`Loaded calibration: ${record.deviceId}/${record.presetIndex}`);
    },
    [addLog, clearPreviewSession, confirmDiscardDraft, setDraft, streamSession?.channelId, streamSession?.deviceId],
  );

  const startNextCalibration = useCallback(() => {
    if (!confirmDiscardDraft("新建下一条标定")) return;
    const nextDraft = createEmptyDraft(draft.deviceId, draft.channelId);
    setDraft(nextDraft);
    setFrozenFrame(null);
    setNaturalSize(null);
    setCurrentVersion(null);
    setSaveResult(null);
    setCleanDraftSignature(JSON.stringify(nextDraft));
    setMessage("已开始新标定，请先绑定预置点并重新冻结画面。");
    addLog("Started next calibration");
  }, [addLog, confirmDiscardDraft, draft.channelId, draft.deviceId, setDraft]);

  useEffect(() => {
    void refreshCalibrationItems().catch((error: unknown) => {
      const detail = error instanceof Error ? error.message : String(error);
      addLog(`Calibration list load failed: ${detail}`, "error");
    });
  }, [addLog, refreshCalibrationItems]);

  const handleRoiChange = useCallback(
    (key: "roi" | "focusAnchorRoi", nextRoi: { x: number; y: number; width: number; height: number } | null) => {
      setRoi(key, nextRoi);
      if (nextRoi) {
        const label = key === "roi" ? "识别 ROI" : "对焦 ROI";
        addLog(`${label} updated: ${nextRoi.x},${nextRoi.y},${nextRoi.width},${nextRoi.height}`);
      }
    },
    [addLog, setRoi],
  );

  const activateRoiEditor = useCallback((key: "roi" | "focusAnchorRoi") => {
    setActiveRoiKey(key);
  }, []);

  const redrawRoi = useCallback(
    (key: "roi" | "focusAnchorRoi") => {
      setActiveRoiKey(key);
      setRoi(key, null);
      addLog(`${key === "roi" ? "识别 ROI" : "对焦 ROI"} cleared for redraw`);
    },
    [addLog, setRoi],
  );

  const clearRoi = useCallback(
    (key: "roi" | "focusAnchorRoi") => {
      setRoi(key, null);
      addLog(`${key === "roi" ? "识别 ROI" : "对焦 ROI"} cleared`);
    },
    [addLog, setRoi],
  );

  async function refreshVendorLogs() {
    await runTask(async () => {
      const result = await getRecentLogs(12);
      setVendorLogs(result.items);
      addLog("Loaded recent backend logs");
    });
  }

  const devicePanel = (
    <DevicePanel
            deviceId={draft.deviceId}
            channelId={draft.channelId}
            streamPreference={streamPreference}
            loading={loading}
            onlineStatus={onlineStatus}
            compact
            onDeviceIdChange={(value) => {
              updateField("deviceId", value);
              if (streamSession && (streamSession.deviceId !== value || streamSession.channelId !== draft.channelId)) {
                clearPreviewSession("设备已变更，原视频预览已关闭；请重新加载视频。");
              }
            }}
            onChannelIdChange={(value) => {
              updateField("channelId", value);
              if (streamSession && (streamSession.deviceId !== draft.deviceId || streamSession.channelId !== value)) {
                clearPreviewSession("通道已变更，原视频预览已关闭；请重新加载视频。");
              }
            }}
            onStreamPreferenceChange={setStreamPreference}
            onCheckOnline={() =>
              runTask(async () => {
                const result = await checkDeviceOnline(draft.deviceId);
                setOnlineStatus(result.status);
                setMessage(`Device status: ${result.status}`);
                addLog(`Device online check finished: ${result.status}`);
              })
            }
            onLoadStream={() =>
              runTask(async () => {
                await refreshPreviewStream({
                  reason: `initial load (${streamPreference})`,
                  honorCooldown: false,
                });
              })
            }
    />
  );

  const ptzPanel = (
    <PtzControlPanel
            stepProfile={stepProfile}
            disabled={loading || !runtimeConfigReady || !draft.deviceId || !draft.channelId}
            onStepProfileChange={setStepProfile}
            onMove={(action) => {
              if (!confirmDiscardDraft("移动云台")) return;
              void runTask(async () => {
                const result = await movePtz({
                  deviceId: draft.deviceId,
                  channelId: draft.channelId,
                  action,
                  stepProfile,
                });
                const verificationLabel = result.operationVerified ? "verified" : "unverified";
                const verifiedDetail = result.verifiedOperation ? `, verifiedOperation=${result.verifiedOperation}` : "";
                addLog(`PTZ move detail: ${action} (${stepProfile}), operation=${result.command.operation}${verifiedDetail}, ${verificationLabel}`);
                invalidateFrozenCalibration("云台已移动，冻结画面和双 ROI 已失效，请重新判稳后取景。");
                beginCaptureGate("PTZ command accepted", result.command.duration + runtimeConfig.ptzExtraSettleMs);
              });
            }}
    />
  );

  const presetPanel = (
    <PresetPanel
            presetIndex={draft.presetIndex}
            presetName={draft.presetName}
            presets={presets}
            disabled={loading || !runtimeConfigReady || !draft.deviceId || !draft.channelId}
            onPresetIndexChange={parsePresetIndex}
            onPresetNameChange={(value) => updateField("presetName", value)}
            onRefresh={() =>
              runTask(async () => {
                const result = await queryPresets(draft.deviceId, draft.channelId);
                setPresets(result.presets.filter((item) => item.presetIndex >= 0));
                setMessage(`Loaded ${result.presets.length} presets`);
                addLog(`Preset query completed: ${result.presets.length} records`);
              })
            }
            onSave={() =>
              runTask(async () => {
                if (draft.presetIndex === null) {
                  throw new Error("presetIndex is required before saving preset");
                }

                await savePreset({
                  deviceId: draft.deviceId,
                  channelId: draft.channelId,
                  presetIndex: draft.presetIndex,
                  presetName: draft.presetName,
                });
                setMessage(`Preset saved: ${draft.presetIndex}`);
                addLog(`Preset saved: index=${draft.presetIndex}, name=${draft.presetName}`);
              })
            }
            onTurn={() => {
              if (!confirmDiscardDraft("转到预置点")) return;
              void runTask(async () => {
                if (draft.presetIndex === null) {
                  throw new Error("presetIndex is required before turning preset");
                }

                await turnPreset({
                  deviceId: draft.deviceId,
                  channelId: draft.channelId,
                  presetIndex: draft.presetIndex,
                });
                addLog(`Preset turned: index=${draft.presetIndex}`);
                invalidateFrozenCalibration("预置点已切换，冻结画面和双 ROI 已失效，请重新判稳后取景。");
                beginCaptureGate("Preset turn accepted", runtimeConfig.presetTurnSettleMs, draft.presetIndex);
              });
            }}
            onPickPreset={(preset) => {
              if (!confirmDiscardDraft("切换预置点")) return;
              updateField("presetIndex", preset.presetIndex);
              updateField("presetName", preset.presetName ?? "");
              setStreamSession((current) => (current ? { ...current, confirmedPresetIndex: null } : current));
              invalidateFrozenCalibration("已切换预置点，冻结画面和双 ROI 已失效，请重新判稳后取景。");
              addLog(`Preset selected from list: index=${preset.presetIndex}`);
            }}
    />
  );

  const preview = (
    <StreamPreview
            streamUrl={streamSession?.deviceId === draft.deviceId && streamSession?.channelId === draft.channelId ? stream?.streamUrl ?? null : null}
            streamType={streamSession?.deviceId === draft.deviceId && streamSession?.channelId === draft.channelId ? stream?.streamType ?? null : null}
            restartToken={streamRestartToken}
            frozenFrame={frozenFrame}
            captureDisabled={!canFreezeFrame}
            captureStatusLabel={captureStatusLabel}
            playbackState={playbackState}
            visualStabilityEnabled={visualStabilityEnabled}
            visualStableSampleMs={runtimeConfig.visualStableSampleMs}
            visualStableThreshold={runtimeConfig.visualStableThreshold}
            visualStableGraceThreshold={runtimeConfig.visualStableGraceThreshold}
            visualEvaluationWindowSize={evaluationWindowSize}
            visualRequiredStableCount={requiredStableCount}
            onPlayerEvent={handlePlayerEvent}
            onPlaybackStateChange={setPlaybackState}
            onStreamRefreshNeeded={handleStreamRefreshNeeded}
            onVisualStabilityChange={setVisualStability}
            onReconnectPreview={() =>
              void runTask(async () => {
                await refreshPreviewStream({
                  reason: "manual reconnect",
                  honorCooldown: false,
                });
              })
            }
            onCaptureFrame={({ dataUrl, naturalWidth, naturalHeight }) => {
              setFrozenFrame(dataUrl);
              setNaturalSize({ width: naturalWidth, height: naturalHeight });
              setSnapshot(dataUrl);
              setMessage("Frozen frame ready for ROI selection");
              addLog("Frozen frame captured");
            }}
    />
  );

  const roiControls = (
    <section className="panel roiControlsPanel">
            <div className="panelHeader">
              <h2>ROI 标定</h2>
              <span className="statusBadge">当前编辑: {activeRoiLabel}</span>
            </div>
            <div className="roiControlGrid">
              <button
                type="button"
                className={activeRoiKey === "roi" ? "ghostButton activeModeButton" : "ghostButton"}
                onClick={() => activateRoiEditor("roi")}
              >
                编辑识别 ROI
              </button>
              <button
                type="button"
                className={activeRoiKey === "focusAnchorRoi" ? "ghostButton activeModeButton" : "ghostButton"}
                onClick={() => activateRoiEditor("focusAnchorRoi")}
              >
                编辑对焦 ROI
              </button>
              <button type="button" className="ghostButton" onClick={() => redrawRoi("roi")}>
                重划识别 ROI
              </button>
              <button type="button" className="ghostButton" onClick={() => redrawRoi("focusAnchorRoi")}>
                重划对焦 ROI
              </button>
              <button type="button" className="ghostButton" onClick={() => clearRoi("roi")}>
                清除识别 ROI
              </button>
              <button type="button" className="ghostButton" onClick={() => clearRoi("focusAnchorRoi")}>
                清除对焦 ROI
              </button>
            </div>
            <div className="roiGuidance">
              <p>
                <strong>识别 ROI</strong>：框住水花主要出现区域，后续水花特征提取、打分和投票都只看这里。
              </p>
              <p>
                <strong>对焦 ROI</strong>：框住水花旁边最稳定、最固定、最清楚的机身或支架边缘。不要直接框在水花主体上。
              </p>
            </div>
    </section>
  );

  const roiCanvas = (
    <RoiCanvas
            title="冻结帧双 ROI 视图"
            activeHint="拖动鼠标重画当前激活的 ROI。"
            emptyHint="请先冻结当前画面，再分别标定识别 ROI 和对焦 ROI。"
            imageSrc={frozenFrame}
            roi={draft.roi}
            focusAnchorRoi={draft.focusAnchorRoi}
            activeRoiKey={activeRoiKey}
            naturalSize={naturalSize}
            onRoiChange={handleRoiChange}
    />
  );

  const savePanel = (
    <SaveCalibrationPanel
            draft={draft}
            disabled={loading}
            saveResult={saveResult}
            validationErrors={validationErrors}
            onFieldChange={updateField}
            currentVersion={currentVersion}
            onSave={() => {
              if (currentVersion && !window.confirm("更新会创建一个新的不可变历史版本，是否继续？")) return;
              void runTask(async () => {
                const annotatedSnapshot = await createAnnotatedSnapshot(draft.snapshotBase64 ?? "", draft);
                const result = await saveCalibration({
                  ...draft,
                  snapshotOriginalBase64: draft.snapshotBase64,
                  snapshotAnnotatedBase64: annotatedSnapshot,
                });
                setCurrentVersion(result.record.version ?? null);
                setCleanDraftSignature(JSON.stringify(draft));
                setSaveResult(`已保存 ${result.record.deviceId} / 预置点 ${result.record.presetIndex} / v${String(result.record.version ?? 0).padStart(4, "0")}`);
                setMessage("标定已保存，当前结果会保留在工作台中。");
                addLog(`Calibration saved for presetIndex=${result.record.presetIndex}`);
                await refreshCalibrationItems();
                setHistoryItems(await getCalibrationHistory(result.record.deviceId, result.record.presetIndex));
              });
            }}
            onNewNext={startNextCalibration}
            onExportCurrent={() => {
              if (draft.presetIndex !== null) {
                downloadCalibrationExport(`/api/calibration/export/current?deviceId=${encodeURIComponent(draft.deviceId)}&presetIndex=${draft.presetIndex}`);
              }
            }}
            onExportAll={() => downloadCalibrationExport("/api/calibration/export/all")}
            onExportArchive={() => downloadCalibrationExport("/api/calibration/export/archive")}
    />
  );

  const managementPanel = (
    <details className="existingCalibrationsDrawer">
      <summary>已有标定与历史版本</summary>
      <div className="existingCalibrationContent">
        <button type="button" className="ghostButton" onClick={() => void runTask(refreshCalibrationItems)}>刷新标定列表</button>
        <div className="existingCalibrationList">
          {calibrationItems.length === 0 ? <p className="emptySmall">当前没有已保存的标定。</p> : null}
          {calibrationItems.map((item) => (
            <button
              key={`${item.deviceId}-${item.presetIndex}`}
              type="button"
              className="existingCalibrationItem"
              onClick={() => void runTask(() => loadExistingCalibration(item.deviceId, item.presetIndex))}
            >
              <strong>{item.targetName}</strong>
              <span>{item.deviceId} / 预置点 {item.presetIndex}</span>
              <small>{item.legacy ? "旧版标定" : `v${String(item.version ?? 0).padStart(4, "0")}`} · {item.updatedAt}</small>
            </button>
          ))}
        </div>
        {historyItems.length > 0 ? (
          <div className="historyList">
            <h3>当前预置点历史</h3>
            <p className="metaText">恢复会保留历史记录，并基于所选版本创建新的当前版本。</p>
            {historyItems.map((item) => (
              <div key={item.version} className="historyItem">
                <span>v{String(item.version).padStart(4, "0")}{item.legacy ? " · 旧版" : ""}</span>
                <small>{item.updatedAt}</small>
                <button
                  type="button"
                  className="ghostButton"
                  onClick={() => {
                    if (draft.presetIndex === null || !window.confirm(`恢复 v${String(item.version).padStart(4, "0")} 会创建新版本，是否继续？`)) return;
                    void runTask(async () => {
                      const restored = await restoreCalibration(draft.deviceId, draft.presetIndex ?? 0, item.version);
                      await refreshCalibrationItems();
                      await loadExistingCalibration(restored.record.deviceId, restored.record.presetIndex);
                    });
                  }}
                >
                  基于 v{String(item.version).padStart(4, "0")} 创建新的当前版本
                </button>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    </details>
  );

  const logs = <OperationLogPanel activityLogs={activityLogs} vendorLogs={vendorLogs} onRefreshVendorLogs={refreshVendorLogs} loading={loading} />;

  return (
    <CalibrationWorkbench
      message={message}
      connectionLabel={onlineStatus === "online" ? "已连接" : onlineStatus === "idle" ? "未检查" : onlineStatus}
      captureStatusLabel={captureStatusLabel}
      devicePanel={devicePanel}
      presetPanel={presetPanel}
      ptzPanel={ptzPanel}
      managementPanel={managementPanel}
      preview={preview}
      roiControls={roiControls}
      roiCanvas={roiCanvas}
      savePanel={savePanel}
      logs={logs}
    />
  );
}
