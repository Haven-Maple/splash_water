import { useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";

interface UseVisualStabilityOptions {
  videoRef: RefObject<HTMLVideoElement>;
  enabled: boolean;
  sampleMs: number;
  threshold: number;
  graceThreshold: number;
  evaluationWindowSize: number;
  requiredStableCount: number;
  onEvent?: (message: string, level?: "info" | "error") => void;
}

export interface VisualStabilityState {
  visualStable: boolean;
  rawMotionScore: number | null;
  smoothedMotionScore: number | null;
  stableCount: number;
  graceCount: number;
  failCount: number;
}

const SAMPLE_WIDTH = 96;
const SAMPLE_HEIGHT = 54;
const PIXEL_DEADZONE = 3;
const EMA_ALPHA = 0.35;

type SampleStatus = "stable" | "grace" | "fail";

function boxBlur(input: Uint8ClampedArray, width: number, height: number) {
  const output = new Uint8ClampedArray(input.length);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      let sum = 0;
      let count = 0;
      for (let offsetY = -1; offsetY <= 1; offsetY += 1) {
        for (let offsetX = -1; offsetX <= 1; offsetX += 1) {
          const sampleX = x + offsetX;
          const sampleY = y + offsetY;
          if (sampleX < 0 || sampleX >= width || sampleY < 0 || sampleY >= height) {
            continue;
          }
          sum += input[sampleY * width + sampleX];
          count += 1;
        }
      }
      output[y * width + x] = Math.round(sum / Math.max(count, 1));
    }
  }
  return output;
}

function meanFrameDelta(currentFrame: Uint8ClampedArray, previousFrame: Uint8ClampedArray) {
  let total = 0;
  for (let index = 0; index < currentFrame.length; index += 1) {
    const diff = Math.abs(currentFrame[index] - previousFrame[index]);
    total += diff <= PIXEL_DEADZONE ? 0 : diff;
  }
  return total / currentFrame.length;
}

export function useVisualStability({
  videoRef,
  enabled,
  sampleMs,
  threshold,
  graceThreshold,
  evaluationWindowSize,
  requiredStableCount,
  onEvent,
}: UseVisualStabilityOptions): VisualStabilityState {
  const [rawMotionScore, setRawMotionScore] = useState<number | null>(null);
  const [smoothedMotionScore, setSmoothedMotionScore] = useState<number | null>(null);
  const [history, setHistory] = useState<SampleStatus[]>([]);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const previousFrameRef = useRef<Uint8ClampedArray | null>(null);
  const smoothedScoreRef = useRef<number | null>(null);
  const lastLoggedRoiKeyRef = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled) {
      setRawMotionScore(null);
      setSmoothedMotionScore(null);
      setHistory([]);
      previousFrameRef.current = null;
      smoothedScoreRef.current = null;
      return;
    }

    const video = videoRef.current;
    if (!video) {
      return;
    }

    if (!canvasRef.current) {
      canvasRef.current = document.createElement("canvas");
      canvasRef.current.width = SAMPLE_WIDTH;
      canvasRef.current.height = SAMPLE_HEIGHT;
    }

    const frameKey = `${video.videoWidth || 1}x${video.videoHeight || 1}`;
    if (lastLoggedRoiKeyRef.current !== frameKey) {
      onEvent?.("Visual stability active: full frame");
      lastLoggedRoiKeyRef.current = frameKey;
    }

    const canvas = canvasRef.current;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) {
      return;
    }

    const timerId = window.setInterval(() => {
      if (!video.videoWidth || !video.videoHeight || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
        return;
      }

      context.drawImage(video, 0, 0, video.videoWidth, video.videoHeight, 0, 0, SAMPLE_WIDTH, SAMPLE_HEIGHT);
      const imageData = context.getImageData(0, 0, SAMPLE_WIDTH, SAMPLE_HEIGHT).data;

      const grayscale = new Uint8ClampedArray(SAMPLE_WIDTH * SAMPLE_HEIGHT);
      for (let pixel = 0, gray = 0; pixel < imageData.length; pixel += 4, gray += 1) {
        grayscale[gray] = Math.round(imageData[pixel] * 0.299 + imageData[pixel + 1] * 0.587 + imageData[pixel + 2] * 0.114);
      }

      const blurred = boxBlur(grayscale, SAMPLE_WIDTH, SAMPLE_HEIGHT);
      const previousFrame = previousFrameRef.current;
      previousFrameRef.current = blurred;
      if (!previousFrame) {
        return;
      }

      const nextRawScore = meanFrameDelta(blurred, previousFrame);
      const previousSmoothed = smoothedScoreRef.current;
      const nextSmoothedScore = previousSmoothed === null ? nextRawScore : previousSmoothed * (1 - EMA_ALPHA) + nextRawScore * EMA_ALPHA;
      smoothedScoreRef.current = nextSmoothedScore;

      let sampleStatus: SampleStatus = "fail";
      if (nextSmoothedScore <= threshold) {
        sampleStatus = "stable";
      } else if (nextSmoothedScore <= graceThreshold) {
        sampleStatus = "grace";
        onEvent?.("Visual stability in grace band");
      } else {
        onEvent?.("Visual stability failed / continue waiting");
      }

      onEvent?.(`Visual stability raw score=${nextRawScore.toFixed(2)}`);
      onEvent?.(`Visual stability smoothed score=${nextSmoothedScore.toFixed(2)}`);

      setRawMotionScore(nextRawScore);
      setSmoothedMotionScore(nextSmoothedScore);
      setHistory((current) => {
        const next = [...current, sampleStatus].slice(-evaluationWindowSize);
        return next;
      });
    }, sampleMs);

    return () => {
      window.clearInterval(timerId);
      previousFrameRef.current = null;
      smoothedScoreRef.current = null;
      setRawMotionScore(null);
      setSmoothedMotionScore(null);
      setHistory([]);
    };
  }, [enabled, evaluationWindowSize, graceThreshold, onEvent, sampleMs, threshold, videoRef]);

  return useMemo(() => {
    const stableCount = history.filter((item) => item === "stable").length;
    const graceCount = history.filter((item) => item === "grace").length;
    const failCount = history.filter((item) => item === "fail").length;

    return {
      visualStable: stableCount >= requiredStableCount && failCount === 0,
      rawMotionScore,
      smoothedMotionScore,
      stableCount,
      graceCount,
      failCount,
    };
  }, [history, rawMotionScore, requiredStableCount, smoothedMotionScore]);
}
