# Night IR Implementation Checklist

Date: 2026-07-05

## 1. Goal

本清单用于把夜晚红外识别方案拆成可执行步骤，供下一轮实现直接按顺序推进。

目标不是一次做完所有夜晚问题，而是先完成第一版可验收链路：

- 复用现有运行架构
- 新增夜晚红外模式
- 让明显 `has_splash` / `no_splash` 先分开
- 保留完整留痕与回放能力

## 2. Implementation Order

建议严格按下面 6 步推进，每一步做完就停下来验收，不要一口气混改。

### Step 1: Add Night IR Mode Split

目标：

- 在不破坏白天链路的前提下，引入夜晚红外模式

要做：

- 在识别全局配置中增加“场景模式”概念
- 支持至少两个全局模式：
  - `day_visible`
  - `night_ir`
- 运行时根据指定模式读取对应参数，而不是继续只吃一套白天参数

建议实现方式：

- 保持现有算法框架不变
- 只切配置，不切主流程
- 不要做每个预置点单独参数

验收标准：

- 白天原有 `run_once` 行为不回归
- 夜晚模式可以独立加载一套参数
- 结果输出里能明确看出本次使用的是哪套模式/版本

### Step 2: Keep Current Daytime Path Frozen

目标：

- 防止夜晚改动误伤已通过验收的白天线

要做：

- 不修改白天默认参数含义
- 夜晚参数初版从独立配置块进入
- replay / metadata / result 中写明当前模式

验收标准：

- 白天基线文档中的逻辑描述仍成立
- 夜晚改动不会影响白天 `10/10 has` 与 `10/10 no` 的回归使用方式

### Step 3: Add IR-Specific Frame Features

目标：

- 补上夜晚最关键的“条状亮体 vs 连续亮团”识别能力

优先级最高的新特征：

1. `gapFillRatio`
- 观察桨叶之间暗缝是否被亮团填平
- `no_splash`：暗缝更明显
- `has_splash`：暗缝会被水花吞没

2. 主亮团时序面积变化特征
- 可命名为 `temporalAreaVariance` 或类似名称
- 观察主亮团面积在短序列里是否持续波动

3. 主亮团时序形状变化特征
- 可命名为 `temporalShapeVariance` 或类似名称
- 观察主亮团轮廓是否持续变化

已有特征中建议保留并重用：

- `largestBrightComponentRatio`
- `centerBrightCoverage`
- `verticalSpreadRatio`
- `fragmentationScore`
- `highlightDisturbance`

暂不建议第一步就做的特征：

- 复杂纹理特征
- 模型特征
- 每预置点模板匹配

验收标准：

- 结果摘要中能看到新特征值
- replay 元数据中能记录这些新特征
- 没有破坏现有结构化输出

### Step 4: Rebuild Night IR Hard Gate

目标：

- 把夜晚硬门控从“亮不亮”改成“是不是连续喷溅亮团”

夜晚硬门控建议分两段：

1. 结构门控
- 主亮团足够大
- 主亮团位于 ROI 中心
- 主亮团纵向扩展足够
- 亮团碎裂度不能太高
- `gapFillRatio` 达标

2. 动态门控
- 以下至少一项达标：
  - `localResidualMotion`
  - `highlightDisturbance`
  - `temporalAreaVariance`
  - `temporalShapeVariance`

明确不要做的事：

- 不要让“白色高亮存在”直接过门控
- 不要让“桨叶本体几根亮条”直接参与高分

验收标准：

- 明显 `no_splash` 样本在硬门控阶段就被压住
- 明显 `has_splash` 样本可以稳定通过硬门控
- `weightedScore` 不再被静态亮桨叶抬高

### Step 5: Keep Temporal Vote Structure, Only Extend Diagnostics

目标：

- 夜晚先不重构整套时序投票，只在现有投票结构上吃新帧级结果

要做：

- 保持：
  - `framePass`
  - `framePassRatio`
  - `visualState`
  - `has_splash / no_splash / undetermined`
- 在 `scoreSummary` 和 replay metadata 中增加夜晚诊断字段：
  - `gapFillRatio`
  - `temporalAreaVariance`
  - `temporalShapeVariance`
  - 需要的话再加主亮团稳定性摘要

验收标准：

- 主链返回结构不混乱
- 在线结果依然能快速返回
- 回放材料足够支撑后续调参

### Step 6: Run Small Night Acceptance Set

目标：

- 用一小批清晰夜晚样本验证第一版方向是否正确

建议样本：

- 同一个预置点先测
- `5-10` 组 clear `has_splash`
- `5-10` 组 clear `no_splash`

第一轮重点不是覆盖所有困难场景，而是回答：

- 夜晚是否已经不再把静态亮桨叶误判成水花
- 夜晚是否已经能把连续喷溅亮团识别出来

验收标准：

- clear `has_splash` 与 clear `no_splash` 有明显分离
- 结果不是靠运气踩线，而是摘要特征方向正确
- replay 中能看出“亮条结构”和“连续亮团结构”的区别

## 3. Suggested File-Level Work Split

建议另一个对话按下面分工落代码：

### Config

- `inspector/config.py`
- `backend/local_config.example.json`

任务：

- 加入夜晚模式配置结构
- 加入 IR 新特征参数
- 加入 IR 硬门控参数

### Feature Extraction

- `inspector/frame_features.py`

任务：

- 增加 `gapFillRatio`
- 增加主亮团时序面积变化特征
- 增加主亮团时序形状变化特征

### Frame Scoring

- `inspector/frame_scoring.py`

任务：

- 增加夜晚硬门控逻辑
- 调整夜晚模式下的结构/动态权重

### Result Models / Summary

- `inspector/models.py`
- `inspector/run_once_service.py`

任务：

- 把夜晚新增特征写入 `scoreSummary`
- 把关键诊断字段写入 replay metadata
- 把本次运行模式写入结果

### Traceability

- `docs/calibration-tool-dev-log.md`
- `docs/calibration-tool-issues.md`
- `docs/calibration-tool-integration-notes.md`

任务：

- 每一步留痕
- 记录新增字段和参数含义
- 记录当前未解决风险

## 4. What Not To Do Yet

以下内容本轮先不要做：

- 不要上模型
- 不要做每个预置点单独参数
- 不要同时重构白天与夜晚
- 不要先处理大风/灯光混乱/极端噪点等困难夜晚场景
- 不要大改时序投票结构
- 不要为了夜晚去破坏白天基线

## 5. First-Round Acceptance Questions

夜晚第一版完成后，优先回答这 5 个问题：

1. `no_splash` 时，静态亮桨叶是否还会误过硬门控？
2. `has_splash` 时，连续亮团是否能稳定通过硬门控？
3. `gapFillRatio` 是否真的能反映“暗缝被吞没”？
4. 主亮团时序变化特征是否能稳定区分“静态桨叶”和“动态水花”？
5. 白天基线是否完全没有回归？

## 6. Recommended Stop Point

夜晚第一轮做到这里就可以停下来验收：

- 模式分离完成
- IR 新特征接入完成
- 夜晚硬门控完成
- 小样本 clear `has/no` 完成

不要在第一轮里继续追所有复杂边界条件。

