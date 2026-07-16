# 当前成果与下一步重点

日期：2026-07-15

## 1. 当前交付边界

当前项目交付的是“可标定、可独立执行、可复盘”的单目标水花识别能力：

- 标定工具维护识别 `roi` 与对焦 `focusAnchorRoi`
- 识别主链保留：
  - stream startup freshness
  - scene-mode stability
  - visual readiness
  - sample-quality guard
  - temporal voting
  - async replay
- `auto` 只会在稳定图像后选择 `day_visible` 或 `night_ir`
- `day_visible_twilight` 只是白天子档，不是第三套 splash 算法

## 2. 已验证状态

### 白天

白天 `day_visible` 是当前稳定主线。ROI 收口、白泡沫抑制、判稳与 sample-quality 恢复预算已经形成可运行基线。

### 夜间

夜间 `night_ir` 仍是“可运行、可复盘，但未稳定验收”的状态。

2026-07-15 的最新离线 replay 扫描已将 `hardGateMinGapFillRatio` 从 `0.81` 收敛到 `0.76`：

- 扫描样本中，共有 `39` 个进入检测的夜间样本
- `0.76` 是首个同时达到
  - 有水花 `19/19`
  - 无水花 `20/20`
  的最高安全值

同一批强制 IR 有水花样本里：

- `round_05 / 07 / 10` 已冻结为 `gapFill` 假阴性
- `round_08` 单独冻结为 `sample_quality_focus_regressed`

因此当前不能把夜间问题再笼统归成 `centerBrightCoverage`，也不能把 sample-quality 与 hard-gate 假阴性混调。

## 3. 已保留的工程机制

- 流新鲜度守卫：清理旧缓存帧
- 场景判稳：`auto` 模式只在完整窗口稳定后锁定 profile
- 视觉判稳：只看 `focusAnchorRoi`
- 样本质量守卫：ready 后继续保护正式采样窗口
- 断流一次重开：读流异常快速失败并自救一次
- 结构化 replay：关键证据、硬门控计数和失败语义可复盘

## 4. 当前关键配置快照

唯一权威配置来源仍是 [backend/local_config.example.json](../backend/local_config.example.json)，本机 [backend/local_config.json](../backend/local_config.json) 只做本地覆盖。

| 范围 | 当前关键值 |
| --- | --- |
| 场景路由 | `sceneMode=auto`，startup freshness 与 scene-mode stability 启用 |
| 场景判稳 | `2` 个完整窗口，每窗口 `4` 帧，超时 `1600 ms`，最多 `1` 次 relock |
| 白天 sample-quality | timeout `5200 ms`，最大 `3` 次恢复 |
| 黄昏白天子档 | timeout `6000 ms`，最大 `4` 次恢复，最短观察 `1500 ms` |
| 夜间判稳 | `visualReadinessMinSharpness=50.0`，margin `8.0`，ready 后复验 `2` 帧 / `180 ms` |
| 夜间 sample-quality | timeout `5700 ms`，最大 `3` 次恢复 |
| 夜间关键硬门控 | `hardGateMinGapFillRatio=0.76`；`hardGateMinCenterBrightCoverage=0.46` |
| 时序判定 | `20` 帧、`10 fps`、`2000 ms`，`framePassThreshold=0.6`，`sequenceVoteThreshold=0.6` |

## 5. 下一步重点

下一步只做两件事：

1. 现场验证 `hardGateMinGapFillRatio=0.76`
2. 继续单独观察 `sample_quality_focus_regressed`

推荐顺序：

1. 夜间 `has_splash 10 轮`
2. 夜间 `no_splash 10 轮`
3. 若 `sample_quality_focus_regressed` 再重复出现，再单独分析夜间焦点恢复链

在新证据出现前，不同时调整：

- `centerBrightCoverage`
- 时序投票
- 白天门控
- sample-quality 预算

## 6. 当前不做

- `night_visible`
- 运行中 IR/彩色切换处理
- 构图守卫
- HLS 自动降级
- 单预置点专属 splash 阈值

## 7. 相关文档

- [夜间 IR 基线](./night-ir-baseline.md)
- [夜间 gapFill 扫描记录](./night-ir-gap-fill-scan-2026-07-15.md)
- [交接文档](./handoff-2026-07-15.md)
