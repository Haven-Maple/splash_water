# 夜间 IR 识别基线

日期：2026-07-15

## 状态

`night_ir` 是当前已经验证可运行、可复盘的夜间识别基线，但还不能写成“彻底稳定验收完成”。

- 摄像头前提：夜间必须固定红外黑白模式。
- 不支持识别过程中在彩色与 IR 之间切换。
- `focusAnchorRoi` 只服务于判稳与 sample-quality，识别仍只看 splash `roi`。

## 识别方向

夜间水花以结构证据为主，以弱动态证据为辅。真实水花应在识别 ROI 中呈现：

- 足够大的亮区主体
- 足够的中心覆盖
- 足够的纵向展开
- 对暗缝有足够填充

单纯存在亮物、白块或反光，不等于夜间水花。

## 当前已冻结参数

唯一可执行来源仍是 [backend/local_config.example.json](../backend/local_config.example.json)；本机 `backend/local_config.json` 只做本地覆盖。

| 参数 | 当前值 |
| --- | --- |
| `visualReadinessMinSharpness` | `50.0` |
| `visualReadinessMinSharpnessMargin` | `8.0` |
| `visualReadinessNightPostReadyRecheckFrames` | `2` |
| `visualReadinessNightPostReadyRecheckWindowMs` | `180` |
| `sampleQualityTimeoutMs` | `5700` |
| `sampleQualityMaxRecoveries` | `3` |
| `hardGateMinLargestBrightComponentRatio` | `0.25` |
| `hardGateMinCenterBrightCoverage` | `0.46` |
| `hardGateMinVerticalSpreadRatio` | `0.55` |
| `hardGateMinContinuousBrightRatio` | `0.60` |
| `hardGateMinGapFillRatio` | `0.76` |
| `sequenceVoteThreshold` | `0.60` |

## 2026-07-15 gapFill 扫描结论

2026-07-15 已完成一轮新的夜间 `gapFill` 离线扫描，使用样本：

- 问题批次：`AB00A7DPAJ00124_1_p1_t3_has_splash_2026-07-15T14-20-49.632721+00-00`
- 通过的夜间有水花基线：`AB00A7DPAJ00124_1_p1_t3_has_splash_2026-07-15T14-16-50.353115+00-00`
- 通过的夜间无水花基线：
  - `AB00A7DPAJ00124_1_p1_t3_no_splash_2026-07-15T14-13-08.289969+00-00`
  - `AB00A7DPAJ00124_1_p1_t3_no_splash_2026-07-15T14-10-10.579672+00-00`

粗扫 `0.81 -> 0.76` 与细扫 `0.780 -> 0.760` 结论一致：

- `0.765` 仍不够，问题批次仍是 `18/19`，且 `round_07` 仍停在 `pass_ratio_middle_band`
- `0.760` 是第一个同时满足：
  - 有水花 `19/19`
  - 无水花 `20/20`
  的最高安全值

因此本轮已将 `nightIr.hardGateMinGapFillRatio` 正式收敛到 `0.76`。

扫描产物：

- [night-ir-gap-fill-scan-2026-07-15.md](./night-ir-gap-fill-scan-2026-07-15.md)
- [night-ir-gap-fill-scan-2026-07-15.json](./night-ir-gap-fill-scan-2026-07-15.json)
- [night-ir-gap-fill-scan-2026-07-15-fine.json](./night-ir-gap-fill-scan-2026-07-15-fine.json)

## 当前已知边界

最新问题批次里要分成两类，不混调：

- `round_05 / 07 / 10`：`gapFill` 假阴性，已通过新阈值 `0.76` 在离线扫描中恢复
- `round_08`：`sample_quality_focus_regressed`，这是独立的对焦/样本质量问题，本轮不靠调 `gapFill` 去掩盖

## 下一步

下一步不是继续扫 `centerBrightCoverage`，而是：

1. 现场重跑夜间 `has_splash 10` 轮
2. 现场重跑夜间 `no_splash 10` 轮
3. 继续单独观察 `sample_quality_focus_regressed`

只有当 `sample_quality_focus_regressed` 在后续夜间测试里重复出现，才单独进入夜间焦点稳定性分析。

## 不在本基线内

- `night_visible` 第三套识别 profile
- 运行中 IR/彩色切换处理
- 构图守卫
- 按单个预置点维护专属夜间 splash 阈值
