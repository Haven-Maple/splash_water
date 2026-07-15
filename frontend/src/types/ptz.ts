export type PtzAction =
  | "up"
  | "down"
  | "left"
  | "right"
  | "upLeft"
  | "upRight"
  | "downLeft"
  | "downRight"
  | "zoomIn"
  | "zoomOut";

export type StepProfile = "small" | "medium" | "large";

export interface PtzMoveRequest {
  deviceId: string;
  channelId: string;
  action: PtzAction;
  stepProfile: StepProfile;
}

