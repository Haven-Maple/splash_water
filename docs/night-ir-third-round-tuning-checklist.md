# Night IR Third-Round Tuning Checklist

Date: 2026-07-06

## 1. Current Status

夜间链路已经从“完全不可用”推进到“总体可用，但仍有弱正样本漏检”的阶段。

当前最新夜间正样本结果：

- 样本数：`20`
- `14 / 20` -> `has_splash`
- `1 / 20` -> `undetermined`
- `5 / 20` -> `no_splash`

当前最新夜间负样本结果：

- 样本数：`5`
- `5 / 5` -> `no_splash`

当前结论：

- 误报仍被控制住
- 夜间结构识别方向成立
- 当前主要问题是正样本召回仍不够

## 2. Main Tuning Conclusion

第三轮的主调节点不再是：

- `hardGateMinGapFillRatio`

第三轮应优先转向：

- `hardGateMinCenterBrightCoverage`

原因很明确：

- 夜间失败样本里 `gapFillRatio` 普遍已经不低
- 但 `centerBrightCoverage` 在多个失败样本上持续卡在当前门槛附近或以下

## 3. Why The Focus Changes

### 3.1 Structure still dominates night IR

当前夜间正样本均值大致为：

- `largestBrightComponentRatio ≈ 0.460`
- `centerBrightCoverage ≈ 0.582`
- `verticalSpreadRatio ≈ 0.962`
- `gapFillRatio ≈ 0.966`

当前夜间负样本均值大致为：

- `largestBrightComponentRatio ≈ 0.070`
- `centerBrightCoverage = 0`
- `verticalSpreadRatio ≈ 0.170`
- `gapFillRatio ≈ 0.770`

这说明：

- 夜间正负样本在结构上仍然可以明显分离
- 当前夜间主轴仍然是结构判别，而不是动态判别

### 3.2 Dynamic features are still secondary

当前夜间正样本整体动态量级依旧偏弱：

- `temporalAreaVariance`
- `temporalShapeVariance`
- `highlightMotionMean`
- `localMotionMean`

它们能作为辅助，但还不适合做第三轮主抓手。

## 4. Borderline Sample Interpretation

当前未通过或未完全通过的样本，最值得关注的是：

- `run_once_result_11.json`
- `run_once_result_13.json`
- `run_once_result_17.json`
- `run_once_result_18.json`
- `run_once_result_19.json`
- `run_once_result_20.json`

这些样本的共同特征不是：

- `gapFillRatio` 特别低
- `largestBrightComponentRatio` 特别低
- `verticalSpreadRatio` 特别低

更一致的共同点是：

- `centerBrightCoverage` 偏低
- 而且很多就在当前门槛附近

典型值大致为：

- `0.503`
- `0.478`
- `0.500` 附近
- `0.425`
- `0.402`
- `0.361`

而当前夜间配置门槛是：

- `hardGateMinCenterBrightCoverage = 0.50`

这说明部分真实夜间水花已经形成了连续亮团，但：

- 没有足够居中
或
- 中心吞没程度不够强

于是仍被结构门控挡掉。

## 5. Recommended Tuning Order

建议第三轮严格按下面顺序调，不要同时乱动多项。

### Step 1: Lower `hardGateMinCenterBrightCoverage`

优先级：最高

原因：

- 这是当前边界样本最集中的贴边指标
- 它更符合这轮 20 个正样本暴露出来的新主要矛盾

建议探索顺序：

1. `0.48`
2. `0.46`
3. 如还不够，再考虑 `0.44`

不建议一开始直接降太多。

验收目标：

- 先看 `run_once_result_11.json`
- 再看 `run_once_result_13.json`
- 再看 `run_once_result_17.json`

理想情况：

- 先把 `undetermined` 拉成 `has_splash`
- 再尽量把一部分 `no_splash` 拉到 `undetermined` 或 `has_splash`
- 同时保持负样本不放穿

### Step 2: Keep `hardGateMinGapFillRatio` unchanged for now

优先级：第二

原因：

- 第二轮已经证明下调 `gapFillRatio` 是有效的
- 但这轮失败样本里 `gapFillRatio` 已经不再是最像主矛盾的指标

建议：

- 第三轮先锁住当前 `0.86`
- 不要先继续放

### Step 3: Keep `hardGateMinLargestBrightComponentRatio` unchanged

优先级：第三

原因：

- 当前失败样本的 `largestBrightComponentRatio` 仍然整体不低
- 它还不是主卡点

建议：

- 暂不优先调整

### Step 4: Keep `hardGateMinVerticalSpreadRatio` unchanged

优先级：第三

原因：

- 当前失败样本的 `verticalSpreadRatio` 依旧偏高
- 暂时看不出它是主要卡点

建议：

- 暂不优先调整

### Step 5: Do not touch vote thresholds first

优先级：最后

当前仍不建议优先改：

- `sequenceVoteThreshold`
- `framePassThreshold`

原因：

- 现在的问题仍然主要发生在帧级结构门控层
- 不是单纯的投票层问题

## 6. What Not To Change First

第三轮先不要优先动：

- `hardGateMinGapFillRatio`
- `hardGateMinLargestBrightComponentRatio`
- `hardGateMinVerticalSpreadRatio`
- `sequenceVoteThreshold`
- `framePassThreshold`
- 动态特征权重
- 动态特征 scale

除非 `centerBrightCoverage` 调整后结果没有改善。

## 7. Practical Tuning Loop

建议每轮只改一个参数，并立即回归：

1. 修改 `hardGateMinCenterBrightCoverage`
2. 跑全部 `night_has`
3. 跑全部 `night_no`
4. 看：
   - `has_splash` 是否上升
   - `undetermined` 是否减少
   - `night_no` 是否仍保持 `5 / 5`
5. 记录本轮配置与结果

不要一次改多个参数，否则后面很难判断有效因子。

## 8. Immediate Target

第三轮的现实目标不是“一次全调完”，而是继续提高召回，同时不放穿负样本。

推荐最小目标：

- 从 `14 / 20 has_splash`
- 提升到至少 `15 / 20` 或 `16 / 20 has_splash`

同时：

- 夜间负样本继续保持 `5 / 5 no_splash`

## 9. Suggested Stop Rule

如果出现以下情况，应先停下来复盘，不要继续盲调：

- `night_no` 出现首次误报
- `centerBrightCoverage` 下调后，召回没有改善
- `no_splash` 样本的 `hardGatePassRatio` 开始明显上升

这说明已经开始触碰夜间结构分离安全边界。

## 10. Recommended Next Action

下一轮建议直接按这个顺序执行：

1. 仅调 `hardGateMinCenterBrightCoverage`
2. 先试 `0.48`
3. 回归 `night_has` 与 `night_no`
4. 若改善不够，再试 `0.46`
5. 只有在这条线收敛后，才再考虑下一层参数

