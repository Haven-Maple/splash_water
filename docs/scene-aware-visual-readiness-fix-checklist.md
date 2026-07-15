# Scene-Aware Visual Readiness 修复清单

日期：2026-07-09

## 1. 目标

本轮修复只解决伪多点调度场景下的视觉就绪门控问题，重点是：

- `sceneMode=auto` 时，readiness 能真正按已解析场景使用对应阈值
- readiness 不再因为窗口实现过短而误杀已经逐步清晰的夜间画面
- 失败原因能清楚区分“真糊”“窗口不够”“等待不够”等不同情况

本轮优先级高于 splash 主算法调参。

## 2. 已确认现象

以这组真实失败样本为代表：

- [summary.json](C:/Users/Maple_Rain/Documents/Items/splash_water/data/pseudo_multi_point_tests/AB00A7DPAJ00124_1_p1_t2_no_splash_2026-07-09T14-43-14.507485+00-00/summary.json)

当前 `sceneMode=auto` 的 `no_splash` 伪多点结果是：

- `10 / 10` 全部失败
- 全部失败都在 `visual_readiness`
- `3` 轮是 `visual_not_ready_blurry`
- `7` 轮是 `visual_not_ready_ready_window_short`

同时，日志与 round 结果表明：

- `effectiveSceneMode` 一直都能正确落到 `night_ir`
- 场景解析本身不是主故障
- 失败主要发生在正式采样前

## 3. 当前根因判断

### 3.1 sustained ready window 实现过短

关键证据：

- [round_02.json](C:/Users/Maple_Rain/Documents/Items/splash_water/data/pseudo_multi_point_tests/AB00A7DPAJ00124_1_p1_t2_no_splash_2026-07-09T14-43-14.507485+00-00/round_02.json)

该轮表现为：

- `framesChecked = 71`
- `elapsedMs = 3500`
- `sharpnessImprovementRatio = 5.7177`
- `sharpnessTrend = 585.27`
- 但 `readyWindowMsActual = 234`
- 最终失败原因是 `visual_not_ready_ready_window_short`

这说明当前 ready window 很可能只统计到了过短的一段局部窗口，而不是真正的持续清晰区间。

### 3.2 夜间 readiness 阈值没有独立覆盖

关键证据：

- [recognition-config.snapshot.json](C:/Users/Maple_Rain/Documents/Items/splash_water/data/recognition_replays/AB00A7DPAJ00124_1_2026-07-09T14-43-27.497433+00-00/recognition-config.snapshot.json)
- [backend/local_config.json](C:/Users/Maple_Rain/Documents/Items/splash_water/backend/local_config.json)

当前虽已解析到 `night_ir`，但 readiness 仍使用全局阈值，例如：

- `visualReadinessMinSharpness = 300.0`
- `visualReadinessMinReadyWindowMs = 400`
- `visualReadinessMinImprovementRatio = 1.2`

而 `nightIr` 配置块目前主要只覆盖了 splash 特征参数，没有显式覆盖 readiness 字段。

## 4. 修复范围

本轮只改以下内容：

- `visual_readiness.py`
- `run_once_service.py`
- `config.py`
- `backend/local_config.json`
- `backend/local_config.example.json`
- 对应回归测试

本轮不要改：

- 白天 splash 帧级特征阈值
- 夜间 splash 帧级特征阈值
- 时序投票阈值
- 静态亮物抑制逻辑
- ROI 标定流程
- 伪多点固定轮询模板

## 5. 修复项

### 5.1 修正 sustained ready window 的统计逻辑

目标：

- `readyWindowMsActual` 应该能表示真实持续清晰时段
- 不应只由最后 `visualReadinessMinFrames` 那几帧决定

建议做法：

- 把“满足基础清晰条件的连续区间”单独建模
- 该区间的起点应在首次进入可接受清晰区间时记录
- 后续只要未跌破清晰或稳定性条件，就持续累加窗口时长
- `readyWindowMsActual` 应按该连续区间的真实时间跨度计算

验收：

- 已经明显变清晰、且持续时间足够的夜间序列，不再因 `readyWindowMsActual` 只有两百多毫秒而被误杀

### 5.2 给 day/night readiness 加独立 override

目标：

- `dayVisible` 和 `nightIr` 都可独立覆盖 readiness 参数
- `sceneMode=auto` resolve 后，真正使用对应 override

至少支持覆盖这些字段：

- `visualReadinessMinSharpness`
- `visualReadinessMinReadyWindowMs`
- `visualReadinessMinImprovementRatio`
- `visualReadinessStableHighSharpnessMultiplier`
- `visualReadinessStableBlurMaxTrend`
- 如有需要，再补 `visualReadinessMaxStabilityScore`

验收：

- 配置快照中能清楚看到 `night_ir` 已使用夜间 readiness 配置
- 未配置 override 时仍能回退到全局默认值

### 5.3 保持 scene-aware probe 诊断链完整

目标：

- 即使 readiness 失败，也要保留 scene probe 的全部诊断

必须继续保留：

- `requestedSceneMode`
- `effectiveSceneMode`
- `sceneModeReason`
- `sceneModeDiagnostics`

验收：

- `auto` 下失败样本不再丢 scene 诊断上下文

### 5.4 继续细分 readiness 失败原因

内部至少区分：

- `visual_not_ready_blurry`
- `visual_not_ready_timeout`
- `visual_not_ready_ready_window_short`
- `visual_not_ready_min_elapsed`
- `visual_not_ready_blurry_and_unstable`

说明：

- 对外 execution result 可以继续保留兼容字段
- 但 replay metadata 和 round 顶层必须保留更细粒度原因

验收：

- 能一眼区分“真糊”和“只是窗口没等够”

## 6. 测试补齐

至少补以下回归测试：

- `sceneMode=auto` 下，night-like 早期帧会走 `night_ir` readiness 配置
- 起始偏糊、后续明显变清晰并持续足够长，应通过
- 起始偏糊、略有提升但窗口不够，不应误记为 stable blur
- 起始偏糊、提升不足且稳定，应命中 stable blur
- 起始就清晰且稳定，应直接通过
- `readyWindowMsActual` 不会被“只保留最后几帧”错误压短

## 7. 现场回归顺序

修复后按这个顺序验收：

1. 先回归 `no_splash` 伪多点
2. 保持 `sceneMode=auto`
3. 保持 `transitionSettleMs=1800`
4. 先看 readiness 是否还大面积失败
5. 再回归 `has_splash`

重点观察：

- 是否不再出现 `10 / 10` 全死在 readiness
- `visual_not_ready_ready_window_short` 是否明显下降
- 成功轮次是否能正常进入 `night_ir` 并完成识别
- 没有为了放宽 readiness 而重新放行明显模糊画面

## 8. 停止规则

出现以下任一情况，应先停下复盘，不要继续顺手调 splash 阈值：

- readiness 成功率上升，但明显模糊画面也被放行
- `no_splash` 改好后，`has_splash` 大幅退化
- 失败原因字段仍然不能区分 stable blur 与 window short
- 为了修 readiness，反而影响白天/夜间已冻结的单点基线

## 9. 完成标准

本轮完成，不等于“所有伪多点轮次全部成功”，而是满足以下条件：

- `auto` 下 readiness 与 resolved scene 的关系被理顺
- `readyWindowMsActual` 能真实反映持续清晰时段
- 夜间 readiness 阈值能独立覆盖
- `no_splash` 伪多点不再大面积死在 readiness 前
- 失败原因表达真实、可复盘

达到这一步后，再继续下一轮细调才有意义。
