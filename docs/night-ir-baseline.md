# 夜间 IR 识别基线

日期：2026-07-15

## 状态

`night_ir` 是已验证可运行的基线，不是稳定验收结论。夜间摄像头必须固定为红外黑白成像；不支持采样期间在彩色与 IR 之间切换。

## 识别方向

夜间水花以结构证据为主、弱动态证据为辅。真实水花应在识别 ROI 中呈现中央主亮团、足够中心覆盖与垂直展开，并填补桨叶之间的暗缝；单纯存在白色亮物不是水花证据。

对焦锚点 ROI 仅用于视觉判稳和样本质量检查，不能替代识别 ROI。夜间识别 ROI 要为水花外扩和轻微目标位移保留边界余量。

## 已验证阈值

唯一可执行来源是 [backend/local_config.example.json](../backend/local_config.example.json)。当前验证过的关键夜间值如下：

| 参数 | 值 |
| --- | --- |
| `visualReadinessMinSharpness` | `50.0` |
| `visualReadinessMinSharpnessMargin` | `8.0` |
| `visualReadinessNightPostReadyRecheckFrames` | `2` |
| `visualReadinessNightPostReadyRecheckWindowMs` | `180` |
| `sampleQualityTimeoutMs` | `5200` |
| `sampleQualityMaxRecoveries` | `3` |
| `hardGateMinLargestBrightComponentRatio` | `0.25` |
| `hardGateMinCenterBrightCoverage` | `0.46` |
| `hardGateMinVerticalSpreadRatio` | `0.55` |
| `hardGateMinContinuousBrightRatio` | `0.60` |
| `hardGateMinGapFillRatio` | `0.81` |
| `sequenceVoteThreshold` | `0.60` |

`hardGateMinGapFillRatio=0.81` 来自同批夜间正负 replay 的离线扫描：更高值不能完整恢复真实水花，更低值没有额外安全收益。该结果曾在现场得到有水花 `10/10`、无水花 `10/10` 的回归支持。

## 已知边界

强制 IR 后仍出现过有水花 `6/10` 的回归，所有前置采样链均成功，失败集中在 `centerBrightCoverage` 硬门控。失败轮的 `centerBrightCoveragePassCount` 为 `1-3/20`，成功轮为 `13-20/20`。

因此下一项工作仅是以夜间正负 replay 离线扫描 `hardGateMinCenterBrightCoverage`，选取保持无水花拒绝能力的最高安全值，再回到现场验证。不得在该调查中同时调整 gap fill、时序投票或白天规则。

## 不在本基线内

- 夜间全彩或 `night_visible` 识别。
- 运行中 IR/彩色切换处理。
- 依赖日夜参考图的构图守卫。
- 以单个预置点为单位的专属夜间 splash 阈值。
