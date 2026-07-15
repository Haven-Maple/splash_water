from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class RoiModel(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class CalibrationSaveRequest(BaseModel):
    deviceId: str = Field(min_length=1)
    channelId: str = Field(default="0", min_length=1)
    targetId: str = Field(min_length=1)
    targetName: str = Field(min_length=1)
    presetIndex: int = Field(ge=0)
    presetName: str = Field(min_length=1)
    roi: RoiModel
    focusAnchorRoi: RoiModel
    notes: str = ""
    snapshotBase64: str | None = None


class CalibrationRecord(BaseModel):
    deviceId: str
    channelId: str
    targetId: str
    targetName: str
    presetIndex: int
    presetName: str
    roi: RoiModel
    focusAnchorRoi: RoiModel | None = None
    notes: str
    snapshotPath: str | None = None
    snapshotUrl: str | None = None
    updatedAt: str


class CalibrationToolRuntimeConfig(BaseModel):
    ptzExtraSettleMs: int = Field(default=800, ge=0)
    presetTurnSettleMs: int = Field(default=1800, ge=0)
    streamCatchupMs: int = Field(default=1000, ge=0)
    streamUnreadyDebounceMs: int = Field(default=800, ge=0)
    visualStableWindowMs: int = Field(default=800, ge=200)
    visualStableSampleMs: int = Field(default=200, ge=50)
    visualStableThreshold: float = Field(default=6.0, ge=0)
    visualStableGraceThreshold: float = Field(default=8.0, ge=0)

    @model_validator(mode="after")
    def validate_stability_thresholds(self) -> "CalibrationToolRuntimeConfig":
        if self.visualStableGraceThreshold < self.visualStableThreshold:
            raise ValueError("visualStableGraceThreshold must be greater than or equal to visualStableThreshold")
        return self


class CalibrationListItem(BaseModel):
    deviceId: str
    presetIndex: int
    targetName: str
    updatedAt: str
    path: str


class CalibrationOperationResponse(BaseModel):
    saved: bool
    record: CalibrationRecord


class CalibrationListResponse(BaseModel):
    items: list[CalibrationListItem]
