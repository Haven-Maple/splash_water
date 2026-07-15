import { postJson } from "./http";
import type { PtzAction, StepProfile } from "../types/ptz";

export function movePtz(params: {
  deviceId: string;
  channelId: string;
  action: PtzAction;
  stepProfile: StepProfile;
  duration?: number;
}): Promise<{
  accepted: boolean;
  operationVerified: boolean;
  verifiedMap: Record<string, string>;
  verifiedOperation: string | null;
  command: {
    operation: string;
    duration: number;
  };
}> {
  return postJson<{
    accepted: boolean;
    operationVerified: boolean;
    verifiedMap: Record<string, string>;
    verifiedOperation: string | null;
    command: {
      operation: string;
      duration: number;
    };
  }>("/api/ptz/move", params);
}
