# Night IR Second-Round Tuning Checklist

Date: 2026-07-06

## 1. Current Status

夜间 `night_ir` 链路已经从“完全失效”推进到“可分离但召回不足”的阶段。

当前最新正样本结果：

- `15` 个真实夜间正样本
- `10 / 15` -> `has_splash`
- `2 / 15` -> `undetermined`
- `3 / 15` -> `no_splash`

当前最新负样本结果：

- `5 / 5` -> `no_splash`

这说明：

- 夜间方向已经成立
- 当前主要问题不是误报，而是漏报和边界样本召回不够

## 2. Core Tuning Conclusion

第二轮不应该优先去动：

- `sequenceVoteThreshold`
- `framePassThreshold`
- 动态特征权重主结构

第二轮应该优先动：

- 夜间结构门控阈值
- 尤其是 `hardGateMinGapFillRatio`

## 3. Why This Is The Right Focus

当前夜间正负样本已经显示出稳定结构分离：

### Positive mean

- `largestBrightComponentRatio ≈ 0.458`
- `centerBrightCoverage ≈ 0.801`
- `verticalSpreadRatio ≈ 0.777`
- `gapFillRatio ≈ 0.954`

### Negative mean

- `largestBrightComponentRatio ≈ 0.070`
- `centerBrightCoverage = 0`
- `verticalSpreadRatio ≈ 0.170`
- `gapFillRatio ≈ 0.770`

这说明真正有效的是结构特征，而不是动态特征。

当前动态特征整体仍然偏弱：

- `temporalAreaVariance`
- `temporalShapeVariance`
- `highlightMotionMean`
- `localMotionMean`

它们还不适合作为第二轮主要抓手。

## 4. Borderline Sample Interpretation

### Strong evidence for `gapFillRatio` being too strict

当前两个直接落成 `no_splash` 的正样本：

- `run_once_result_14.json`
  - `gapFillRatio ≈ 0.888`
  - `hardGatePassRatio = 0`
- `run_once_result_15.json`
  - `gapFillRatio ≈ 0.890`
  - `hardGatePassRatio = 0`

而当前夜间负样本大致只有：

- `gapFillRatio ≈ 0.768 - 0.772`

当前夜间配置却设置为：

- `hardGateMinGapFillRatio = 0.92`

这说明它很可能是现在最该先调低的门槛。

### Borderline `undetermined` samples are already close

例如：

- `run_once_result_4.json`
  - `hardGatePassRatio = 0.55`
- `run_once_result_13.json`
  - `hardGatePassRatio = 0.45`

这类样本已经不是“结构完全抓不到”，而是“部分帧通过比例不够”。

所以先继续修帧级结构门控，比先改时序投票更合理。

## 5. Recommended Tuning Order

建议严格按下面顺序调，不要一次改很多项。

### Step 1: Lower `hardGateMinGapFillRatio`

优先级：最高

原因：

- 当前证据最集中
- 直接对应两个 `0` 通过率漏检正样本
- 与当前负样本仍有明显间隔

建议方式：

- 从 `0.92` 逐步下调
- 优先试：
  - `0.90`
  - `0.88`
  - 如仍不足，再看 `0.86`

验收标准：

- `run_once_result_14.json`
- `run_once_result_15.json`

至少应从 `hardGatePassRatio = 0` 恢复到非零，再看最终是否进入 `undetermined` 或 `has_splash`

### Step 2: Recheck `hardGateMinCenterBrightCoverage`

优先级：第二

原因：

- 当前两个 `0` 通过率漏检正样本的 `centerBrightCoverage` 分别约：
  - `0.586`
  - `0.530`
- 当前夜间门槛是：
  - `0.5`

这说明它暂时不是最直接的问题，但已经非常贴边。

建议：

- 暂时先不改
- 只有在 `gapFillRatio` 放宽后仍然卡住时，再考虑轻微下调

建议探索范围：

- `0.48`
- `0.45`

### Step 3: Recheck `hardGateMinLargestBrightComponentRatio`

优先级：第三

原因：

- 当前边界正样本的 `largestBrightComponentRatio` 仍然很高
- 普遍在 `0.40+`
- 当前门槛是 `0.25`

结论：

- 这项现在不是主矛盾
- 第二轮先不要优先动

### Step 4: Recheck `hardGateMinVerticalSpreadRatio`

优先级：第三

原因：

- 当前边界正样本的 `verticalSpreadRatio` 普遍在 `0.72+`
- 当前门槛是 `0.55`

结论：

- 这项目前也不是主矛盾
- 暂不优先调整

### Step 5: Only after structure retune, consider vote threshold

优先级：最后

只有在下面情况同时成立时才考虑：

- `hardGatePassRatio` 已明显提升
- 结构特征分离依旧稳定
- 仍然主要卡在 `0.55`、`0.58`、`0.59` 这种接近投票线的样本

这时才考虑评估：

- `sequenceVoteThreshold`

但当前阶段不建议先动它。

## 6. What Not To Change First

这一轮先不要优先改：

- `temporalAreaVarianceFeatureScale`
- `temporalShapeVarianceFeatureScale`
- `highlightMotionWeight`
- `temporalAreaVarianceWeight`
- `temporalShapeVarianceWeight`
- `framePassThreshold`
- `sequenceVoteThreshold`

原因：

- 当前夜间动态特征整体仍弱
- 调这些很容易把问题带偏，甚至误以为是动态主导
- 现在最有价值的是先把结构召回补上

## 7. Practical Tuning Loop

建议每轮只改一项，然后立即回归：

1. 改一个参数
2. 跑全部 `night_has`
3. 跑全部 `night_no`
4. 重点核对：
   - `has_splash` 是否增加
   - `no_splash` 是否仍保持 `5 / 5`
   - `hardGatePassRatio` 是否从 `0` 恢复
5. 记录本轮参数值和结果

不要一次改 3 个以上参数，否则后面很难知道是谁起了作用。

## 8. Immediate Target

第二轮最小目标不是“夜间完全调完”，而是：

- 把当前 `3` 个失败正样本再救回一部分
- 理想目标：
  - `12 / 15` 或更高 `has_splash`
  - `night_no` 仍保持 `5 / 5 no_splash`

只要能在不放穿负样本的前提下继续提高正样本召回，这轮就算成功。

## 9. Recommended Next Action

下一轮建议按这个顺序执行：

1. 先只调 `hardGateMinGapFillRatio`
2. 重跑当前 `night_has` 和 `night_no`
3. 如果 `0` 通过率样本恢复，再看 `undetermined` 是否需要第二步微调
4. 只有结构线稳定后，才讨论是否进一步动时序投票

