# 白天可见光识别基线

日期：2026-07-15

## 状态

`day_visible` 是当前稳定验收的识别主线。现场多轮有水花、无水花和伪多点回归表明，现有结构门控、白色泡沫抑制、双 ROI 判稳和样本质量恢复预算能稳定完成白天识别。

这不是“任何白天条件都已覆盖”的声明。强反光、极端风浪和新的现场背景仍须以 replay 回归验证。

## 识别方向

白天水花以中央连续亮团和垂直展开等结构为主证据，动态证据为必要辅助。水面反光、波纹或附近的白色泡沫装置即使明亮或有扰动，也不应仅凭亮度或运动通过。

静态亮斑抑制只用于 `day_visible`：它用于压制白色泡沫等静态干扰，绝不能复用于 `night_ir`，以免抑制夜间真实水花。

## 已验证阈值

唯一可执行来源是 [backend/local_config.example.json](../backend/local_config.example.json)。当前验证过的关键白天值如下：

| 参数 | 值 |
| --- | --- |
| `visualReadinessMinObserveMs` | `1200` |
| `visualReadinessPostReadyRecheckFrames` | `2` |
| `visualReadinessPostReadyRecheckWindowMs` | `240` |
| `sampleQualityTimeoutMs` | `5200` |
| `sampleQualityMaxRecoveries` | `3` |
| `hardGateMinLargestBrightComponentRatio` | `0.05` |
| `hardGateMinCenterBrightCoverage` | `0.06` |
| `hardGateMinContinuousBrightRatio` | `0.40` |
| `staticBrightMiddleBandMinPassRatio` | `0.45` |
| `sequenceVoteThreshold` | `0.60` |

## 黄昏子档

当 `auto` 仍判为白天、但亮度下降且可见光特征仍存在时，运行时可使用 `day_visible_twilight` 等待预算。它不降低清晰度和 splash 通过规则，只使用更长的观察与恢复时间：最短观察 `1500 ms`、样本质量 timeout `6000 ms`、最多 `4` 次恢复。

## 不在本基线内

- 夜间红外或夜间全彩参数。
- 为单个预置点单独维护白天 splash 阈值。
- 通过无证据的大幅放宽 sample-quality 或时序投票来追求成功率。
