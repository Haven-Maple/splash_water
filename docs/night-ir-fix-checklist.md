# Night IR Misclassification Fix Checklist

Date: 2026-07-06

## 1. Problem Summary

当前夜晚 `night_ir` 模式已经确认生效，但真实夜间 `has_splash` 样本仍被全部误判为 `no_splash`。

这次问题的根因不是：

- 没切到夜晚模式
- 时序投票阈值太严格
- 白天算法意外生效

这次问题的根因是：

- 夜晚链路在“亮团提取”第一步就几乎完全失效
- 后续 `gapFillRatio`、`temporalAreaVariance`、`temporalShapeVariance` 都失去输入基础

## 2. Root Cause

已确认以下事实：

### 2.1 `night_ir` mode is active

- `backend/local_config.json` 已设置 `recognition_v1.sceneMode = night_ir`
- `run_once` 输出结果中 `sceneMode = night_ir`
- replay 目录下的 `recognition-config.snapshot.json` 也已经冻结为夜间配置

所以本次不是模式切换失败。

### 2.2 Current night bright extraction threshold is unrealistic

夜间配置当前使用：

- `highlightPixelThreshold = 215`

但真实 ROI 的灰度统计大致只有：

- `night_has` ROI `q99` 约 `119 - 131`
- `night_no` ROI `q99` 约 `118 - 121`
- `night_has` ROI max 大约 `125 - 149`

这意味着：

- 夜晚 ROI 中真正的水花主体根本达不到 215
- 当前亮团提取会直接输出空结果

### 2.3 Current symptom pattern

在当前夜间样本里，`has` 和 `no` 都表现为：

- `largestBrightComponentRatio = 0`
- `centerBrightCoverage = 0`
- `verticalSpreadRatio = 0`
- `gapFillRatio = 0`
- `temporalAreaVariance = 0`
- `temporalShapeVariance = 0`
- `hardGatePassRatio = 0`

这说明不是后面的门控误杀，而是前面的亮团分割没有抓到任何东西。

## 3. Key Image Interpretation

结合当前夜间 ROI 截图，真实视觉现象已经发生变化：

### `has_splash`

- 更像一整团中灰偏亮、低对比、边缘模糊的雾状亮团
- 不是白天那种高亮喷溅白块
- 条状桨叶结构被整体吞没

### `no_splash`

- 仍然是几根分离的亮条
- 暗缝还能看见
- 结构更硬、更细

因此夜晚问题不是“亮不亮”，而是：

- 是否形成一整块模糊连续团
- 是否保留条状分离结构
- 是否保留条间暗缝

## 4. Fix Direction

夜晚修复方向应从“绝对高亮提取”切换为“相对亮度 + 低对比连续团提取”。

### 4.1 What must change

- 不再依赖固定高阈值 `highlightPixelThreshold = 215`
- 不再假设夜晚水花一定是强白亮块
- 不再把亮团提取建立在“像素足够白”这个前提上

### 4.2 What should stay

- `day_visible` / `night_ir` 双模式结构保留
- preset + ROI + sequence + alignment + frame gate + temporal vote 主链保留
- `gapFillRatio` 思路保留
- 时序变化特征思路保留

## 5. Required Fix Order

建议严格按下面顺序修，不要先调投票阈值。

### Step 1: Replace absolute highlight threshold for night ROI

目标：

- 让夜间主亮团能被真实提取出来

建议方向：

- 夜间分支改成 ROI 相对阈值，不再直接使用固定绝对阈值

可选实现方式：

- `mean + k * std`
- 基于 ROI 灰度分位数，如 `q80 / q85 / q90`
- 先做局部对比度增强，再做相对阈值

要求：

- 白天逻辑不要一起改
- 夜间单独分支处理

验收标准：

- clear `night_has` 不再全部出现 `largestBrightComponentRatio = 0`
- `centerBrightCoverage` / `gapFillRatio` 至少恢复为非零可分析值

### Step 2: Revalidate bright-mass extraction before any gate tuning

目标：

- 先确认夜间亮团已经被提出来

要看：

- `largestBrightComponentRatio`
- `centerBrightCoverage`
- `verticalSpreadRatio`
- `gapFillRatio`

注意：

- 这一阶段先不要急着追最终 `visualState`
- 先确认输入特征恢复正常

验收标准：

- `night_has` 与 `night_no` 的以上结构特征开始出现明显差异

### Step 3: Re-check temporal features on top of recovered extraction

目标：

- 确认 `temporalAreaVariance` / `temporalShapeVariance` 不是因为空亮团而恒为 0

要做：

- 在亮团恢复后重新观察夜间正负样本的时序特征

验收标准：

- `night_has` 的时序特征应高于 `night_no`
- 至少不能再全部为 0

### Step 4: Only then retune night hard gate

目标：

- 在有效特征恢复后再修夜间硬门控

可调项：

- `hardGateMinLargestBrightComponentRatio`
- `hardGateMinCenterBrightCoverage`
- `hardGateMinVerticalSpreadRatio`
- `hardGateMinContinuousBrightRatio`
- `hardGateMinGapFillRatio`
- `hardGateMinTemporalAreaVariance`
- `hardGateMinTemporalShapeVariance`

注意：

- 现在不建议先动 `sequenceVoteThreshold`
- 不建议先动 `framePassThreshold`

因为当前失败不在投票层。

### Step 5: Keep day path frozen

目标：

- 确保夜间修复不破坏白天基线

要求：

- 不混改白天分割入口
- 夜间修复后至少做一轮白天回归

验收标准：

- 白天既有基线结果不回退

## 6. Suggested Implementation Split

### `inspector/frame_features.py`

重点任务：

- 给夜间模式增加独立的 ROI 相对阈值分割逻辑
- 让 `gapFillRatio`、`temporalAreaVariance`、`temporalShapeVariance` 建立在真实提取出的亮团上

### `inspector/config.py`

重点任务：

- 增加夜间相对阈值相关参数
- 例如：
  - night quantile threshold
  - night std multiplier
  - night local contrast enable

### `inspector/frame_scoring.py`

重点任务：

- 在亮团恢复后再调夜间硬门控
- 当前先不要优先动投票层

### `inspector/run_once_service.py`

重点任务：

- 保留现有 `sceneMode` 输出
- 保留现有 replay metadata
- 如有必要，补更多夜间分割诊断字段，方便后续调试

## 7. What Not To Do Next

当前这轮修复里先不要做：

- 不要先调 `sequenceVoteThreshold`
- 不要先调 `framePassThreshold`
- 不要先放宽所有夜间门控
- 不要直接回退到白天算法
- 不要立刻上模型
- 不要用每个预置点单独参数救火

## 8. Immediate Acceptance Target

下一轮修复后的最小目标不是“夜晚完全调好”，而是：

1. `night_has` 不再全部亮团提取为 0
2. `night_has` 与 `night_no` 的结构特征开始分离
3. 夜间时序特征不再全部为 0
4. 在此基础上，再看最终 `visualState`

## 9. Recommended Next Step

下一轮应先做：

1. 夜间 ROI 相对亮团分割替换
2. 重跑现有 `run_once_result_night_has` / `run_once_result_night_no`
3. 先检查结构特征是否恢复
4. 再做夜间硬门控收敛

