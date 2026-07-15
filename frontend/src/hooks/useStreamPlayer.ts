import { useEffect, useRef } from "react";
import type { RefObject } from "react";
import flvjs from "flv.js";
import Hls from "hls.js";

import type { StreamType } from "../types/stream";

export type StreamPlaybackState = "disconnected" | "loading" | "ready" | "error";

interface UseStreamPlayerOptions {
  videoRef: RefObject<HTMLVideoElement>;
  streamUrl: string | null;
  streamType: StreamType | null;
  restartToken?: number;
  onPlaybackStateChange?: (state: StreamPlaybackState) => void;
  onPlayerEvent?: (message: string, level?: "info" | "error") => void;
  onStreamRefreshNeeded?: (reason: string) => void;
}

export function useStreamPlayer({
  videoRef,
  streamUrl,
  streamType,
  restartToken = 0,
  onPlaybackStateChange,
  onPlayerEvent,
  onStreamRefreshNeeded,
}: UseStreamPlayerOptions): void {
  const WAITING_RECOVER_THROTTLE_MS = 800;
  const WAITING_EVENT_LOG_THROTTLE_MS = 1000;
  const playbackStateChangeRef = useRef(onPlaybackStateChange);
  const playerEventRef = useRef(onPlayerEvent);
  const streamRefreshNeededRef = useRef(onStreamRefreshNeeded);

  useEffect(() => {
    playbackStateChangeRef.current = onPlaybackStateChange;
  }, [onPlaybackStateChange]);

  useEffect(() => {
    playerEventRef.current = onPlayerEvent;
  }, [onPlayerEvent]);

  useEffect(() => {
    streamRefreshNeededRef.current = onStreamRefreshNeeded;
  }, [onStreamRefreshNeeded]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) {
      return;
    }

    let disposed = false;
    let recoverTimer: number | null = null;
    let reloadTimer: number | null = null;
    let isSoftReloading = false;
    let backgroundSuspended = false;
    let lastSoftReloadAt = 0;
    let lastRefreshRequestAt = 0;
    let lastWaitingRecoverAt = 0;
    let lastWaitingEventLoggedAt = 0;
    let deferredRefreshReason: string | null = null;
    video.preload = "auto";
    video.autoplay = true;

    if (!streamUrl || !streamType) {
      playbackStateChangeRef.current?.("disconnected");
      return;
    }

    let flvPlayer: flvjs.Player | null = null;
    let hls: Hls | null = null;
    playbackStateChangeRef.current?.("loading");

    const clearRecoverTimers = () => {
      if (recoverTimer !== null) {
        window.clearTimeout(recoverTimer);
        recoverTimer = null;
      }
      if (reloadTimer !== null) {
        window.clearTimeout(reloadTimer);
        reloadTimer = null;
      }
    };

    const isDocumentHidden = () => typeof document !== "undefined" && document.visibilityState !== "visible";

    const isBackgroundAbort = (message: string) =>
      message.includes("video-only background media was paused to save power");
    const isPauseInterruptedAbort = (message: string) =>
      message.includes("The play() request was interrupted by a call to pause()");
    const isRefreshCriticalError = (message: string) => {
      const lowered = message.toLowerCase();
      return (
        lowered.includes("err_empty_response") ||
        lowered.includes("failed to fetch") ||
        lowered.includes("networkerror") ||
        lowered.includes("ioexception") ||
        (lowered.includes("flv player error") && lowered.includes("exception"))
      );
    };

    const shouldLogWaitingEvent = () => {
      const now = Date.now();
      if (now - lastWaitingEventLoggedAt < WAITING_EVENT_LOG_THROTTLE_MS) {
        return false;
      }
      lastWaitingEventLoggedAt = now;
      return true;
    };

    const requestStreamRefresh = (reason: string) => {
      if (disposed) {
        return;
      }

      const now = Date.now();
      if (now - lastRefreshRequestAt < 1000) {
        return;
      }
      lastRefreshRequestAt = now;

      if (isDocumentHidden()) {
        deferredRefreshReason = reason;
        backgroundSuspended = true;
        playerEventRef.current?.(`Preview stream refresh deferred: ${reason} (document hidden)`);
        return;
      }

      deferredRefreshReason = null;
      streamRefreshNeededRef.current?.(reason);
    };

    const attemptPlay = (reason: string) => {
      if (disposed) {
        return;
      }

      if (isDocumentHidden()) {
        backgroundSuspended = true;
        playerEventRef.current?.(`Preview play deferred (${reason}): document hidden`);
        return;
      }

      void video
        .play()
        .then(() => {
          playerEventRef.current?.(`Preview play requested: ${reason}`);
        })
        .catch((error: unknown) => {
          const message = error instanceof Error ? `${error.name}: ${error.message}` : String(error);
          if (isBackgroundAbort(message)) {
            backgroundSuspended = true;
            playerEventRef.current?.(`Preview background playback suspended by browser (${reason})`);
            return;
          }
          if (isPauseInterruptedAbort(message) && isSoftReloading) {
            playerEventRef.current?.(`Preview play interrupted by internal reload (${reason})`);
            return;
          }
          playerEventRef.current?.(`Preview play failed (${reason}): ${message}`, "error");
          if (isRefreshCriticalError(message)) {
            requestStreamRefresh(`play failed: ${message}`);
          }
        });
    };

    const getLiveEdge = () => {
      if (video.buffered.length === 0) {
        return null;
      }
      return video.buffered.end(video.buffered.length - 1);
    };

    const seekToLiveEdge = () => {
      const liveEdge = getLiveEdge();
      if (liveEdge === null) {
        return false;
      }

      const targetTime = Math.max(0, liveEdge - 0.1);
      const lag = liveEdge - video.currentTime;
      if (lag <= 0.2) {
        return false;
      }

      video.currentTime = targetTime;
      playerEventRef.current?.(`Preview auto recover: seek to live edge (${targetTime.toFixed(2)})`);
      return true;
    };

    const softReloadPlayer = (reason: string) => {
      if (disposed || isSoftReloading || isDocumentHidden()) {
        if (!disposed && isDocumentHidden()) {
          backgroundSuspended = true;
          playerEventRef.current?.(`Preview reload deferred (${reason}): document hidden`);
        }
        return;
      }

      const now = Date.now();
      if (now - lastSoftReloadAt < 3000) {
        playerEventRef.current?.(`Preview reload skipped (${reason}): cooldown active`);
        return;
      }

      isSoftReloading = true;
      lastSoftReloadAt = now;
      playerEventRef.current?.(`Preview auto recover: player reload (${reason})`, "error");

      if (flvPlayer) {
        flvPlayer.unload();
        flvPlayer.load();
      } else if (hls) {
        hls.stopLoad();
        hls.startLoad(-1);
      } else {
        video.load();
        attemptPlay("generic-auto-reload");
      }

      window.setTimeout(() => {
        isSoftReloading = false;
      }, 1500);
    };

    const scheduleAutoRecover = (reason: string) => {
      if (disposed) {
        return;
      }

      if (isDocumentHidden()) {
        backgroundSuspended = true;
        clearRecoverTimers();
        playerEventRef.current?.(`Preview auto recover deferred (${reason}): document hidden`);
        return;
      }

      const now = Date.now();
      if (now - lastWaitingRecoverAt < WAITING_RECOVER_THROTTLE_MS) {
        return;
      }
      lastWaitingRecoverAt = now;

      clearRecoverTimers();
      recoverTimer = window.setTimeout(() => {
        if (disposed) {
          return;
        }
        seekToLiveEdge();
        if (video.paused) {
          attemptPlay(`${reason}-auto-recover`);
        } else {
          playerEventRef.current?.(`Preview auto recover: wait for data (${reason})`);
        }

        reloadTimer = window.setTimeout(() => {
          if (disposed) {
            return;
          }

          const liveEdge = getLiveEdge();
          const lag = liveEdge === null ? null : liveEdge - video.currentTime;
          const stillBlocked =
            video.readyState < HTMLMediaElement.HAVE_FUTURE_DATA ||
            video.paused ||
            (lag !== null && lag > 0.6);

          if (stillBlocked) {
            softReloadPlayer(reason);
          }
        }, 1200);
      }, 150);
    };

    const handleVisibilityChange = () => {
      if (disposed) {
        return;
      }

      if (isDocumentHidden()) {
        backgroundSuspended = true;
        clearRecoverTimers();
        playerEventRef.current?.("Preview hidden, suspend live auto recover");
        return;
      }

      const wasBackgroundSuspended = backgroundSuspended;
      backgroundSuspended = false;
      playerEventRef.current?.(
        wasBackgroundSuspended
          ? "Preview visible again, resume live playback"
          : "Preview visible, verify live playback state",
      );
      if (deferredRefreshReason) {
        const pendingReason = deferredRefreshReason;
        deferredRefreshReason = null;
        requestStreamRefresh(pendingReason);
        return;
      }
      playbackStateChangeRef.current?.("loading");
      scheduleAutoRecover("visibility-resume");
    };

    const handleLoadedMetadata = () => {
      playerEventRef.current?.("Preview loadedmetadata");
    };

    const handleCanPlay = () => {
      playerEventRef.current?.("Preview canplay");
      attemptPlay("canplay");
    };

    const handlePlaying = () => {
      clearRecoverTimers();
      isSoftReloading = false;
      lastWaitingRecoverAt = 0;
      lastWaitingEventLoggedAt = 0;
      playbackStateChangeRef.current?.("ready");
      playerEventRef.current?.("Preview playing");
    };

    const handleWaiting = () => {
      if (isSoftReloading) {
        if (shouldLogWaitingEvent()) {
          playerEventRef.current?.("Preview waiting during internal reload");
        }
        return;
      }
      playbackStateChangeRef.current?.("loading");
      if (shouldLogWaitingEvent()) {
        playerEventRef.current?.("Preview waiting");
      }
      if (isDocumentHidden()) {
        backgroundSuspended = true;
        return;
      }
      scheduleAutoRecover("waiting");
    };

    const handleStalled = () => {
      if (isSoftReloading) {
        if (shouldLogWaitingEvent()) {
          playerEventRef.current?.("Preview stalled during internal reload");
        }
        return;
      }
      playbackStateChangeRef.current?.("loading");
      if (shouldLogWaitingEvent()) {
        playerEventRef.current?.("Preview stalled", "error");
      }
      if (isDocumentHidden()) {
        backgroundSuspended = true;
        return;
      }
      scheduleAutoRecover("stalled");
    };

    const handlePause = () => {
      if (!disposed) {
        if (isSoftReloading) {
          playerEventRef.current?.("Preview paused by internal reload");
          return;
        }
        if (isDocumentHidden()) {
          backgroundSuspended = true;
          playerEventRef.current?.("Preview paused by background power saving");
          return;
        }
        playerEventRef.current?.("Preview paused");
      }
    };

    const handleError = () => {
      playbackStateChangeRef.current?.("error");
      playerEventRef.current?.("Preview error", "error");
    };

    video.addEventListener("loadedmetadata", handleLoadedMetadata);
    video.addEventListener("canplay", handleCanPlay);
    video.addEventListener("playing", handlePlaying);
    video.addEventListener("waiting", handleWaiting);
    video.addEventListener("stalled", handleStalled);
    video.addEventListener("pause", handlePause);
    video.addEventListener("error", handleError);
    document.addEventListener("visibilitychange", handleVisibilityChange);

    playerEventRef.current?.(`Preview player initializing (${streamType.toUpperCase()})`);

    if (streamType === "flv" && flvjs.isSupported()) {
      flvPlayer = flvjs.createPlayer(
        {
          type: "flv",
          url: streamUrl,
          isLive: true,
        },
        {
          enableStashBuffer: true,
          stashInitialSize: 128,
          lazyLoad: false,
          autoCleanupSourceBuffer: true,
          autoCleanupMaxBackwardDuration: 6,
          autoCleanupMinBackwardDuration: 3,
          fixAudioTimestampGap: true,
        },
      );
      flvPlayer.on(flvjs.Events.ERROR, (errorType, errorDetail) => {
        playbackStateChangeRef.current?.("error");
        const reason = `FLV player error: ${String(errorType)} / ${String(errorDetail)}`;
        playerEventRef.current?.(reason, "error");
        if (isRefreshCriticalError(reason)) {
          requestStreamRefresh(reason);
        }
      });
      flvPlayer.attachMediaElement(video);
      flvPlayer.load();
      attemptPlay("flv-load");
    } else if (streamType === "hls") {
      if (video.canPlayType("application/vnd.apple.mpegurl")) {
        video.src = streamUrl;
        attemptPlay("native-hls-src-set");
      } else if (Hls.isSupported()) {
        hls = new Hls();
        hls.on(Hls.Events.ERROR, (_, data) => {
          if (data.fatal) {
            playbackStateChangeRef.current?.("error");
          }
          const reason = `HLS player error: ${data.type} / ${data.details}`;
          playerEventRef.current?.(reason, "error");
          if (isRefreshCriticalError(reason)) {
            requestStreamRefresh(reason);
          }
        });
        hls.loadSource(streamUrl);
        hls.attachMedia(video);
        hls.on(Hls.Events.MANIFEST_PARSED, () => {
          playerEventRef.current?.("HLS manifest parsed");
          attemptPlay("hls-manifest-parsed");
        });
      } else {
        video.src = streamUrl;
        attemptPlay("generic-src-set");
      }
    } else {
      video.src = streamUrl;
      attemptPlay("generic-src-set");
    }

    return () => {
      disposed = true;
      clearRecoverTimers();
      video.removeEventListener("loadedmetadata", handleLoadedMetadata);
      video.removeEventListener("canplay", handleCanPlay);
      video.removeEventListener("playing", handlePlaying);
      video.removeEventListener("waiting", handleWaiting);
      video.removeEventListener("stalled", handleStalled);
      video.removeEventListener("pause", handlePause);
      video.removeEventListener("error", handleError);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      if (flvPlayer) {
        flvPlayer.pause();
        flvPlayer.unload();
        flvPlayer.detachMediaElement();
        flvPlayer.destroy();
      }
      if (hls) {
        hls.destroy();
      }
      video.pause();
      video.removeAttribute("src");
      video.load();
      playbackStateChangeRef.current?.("disconnected");
    };
  }, [restartToken, streamType, streamUrl, videoRef]);
}
