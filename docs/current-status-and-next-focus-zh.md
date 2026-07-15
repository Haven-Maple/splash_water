# 当前成果与下一步重点

日期：2026-07-09

## 1. 文档目的

这份文档用于整理当前阶段的真实进展，避免后续排查继续被零散上下文拖散。重点回答四件事：

- 第一阶段已经完成到什么程度
- 第二阶段已经完成到什么程度
- 当前真正卡住的问题是什么
- 下一轮修复应当只动哪里、不动哪里

## 2. 当前阶段结论

截至目前，项目已经完成两条主线的核心闭环：

1. 第一阶段：标定工具已经能稳定产出可直接被识别程序消费的标定配置。
2. 第二阶段：单点识别主链已经打通，白天可见光与夜间红外都已形成可复用基线。

因此，项目当前已经不再处于“能不能识别”的探索期，而是进入“在伪多点调度场景下，如何把取样时机、视觉就绪门控和自动场景切换收口好”的工程化阶段。

## 3. 第一阶段成果

第一阶段的目标，是解决“如何稳定标定识别目标”这个前置问题，而不是直接做水花识别。

当前已完成的关键能力：

- 连接摄像头并检查在线状态
- 获取实时预览画面
- 支持 PTZ 控制与预置点调用
- 在画面稳定后冻结当前帧
- 在冻结帧上框定目标 ROI
- 保存本地标定 JSON 与对应截图

当前已完成的结构性收口：

- `stabilityRoi` 已移除，不再要求维护第二个手动画框区域
- 标定文件已收口为“点位描述文件”，主要保存 `deviceId/channelId/targetId/targetName/presetIndex/presetName/roi/notes/snapshotPath/snapshotUrl/updatedAt`
- PTZ 等待、视觉判稳、流追帧等运行参数已统一放到全局配置，不再散落在单个标定 JSON 中
- 第一阶段页面虽然仍有预览延迟与恢复权衡问题，但已经达到“可稳定标定、可支撑第二阶段输入”的交付状态

结论：第一阶段已经不是实验性页面，而是第二阶段识别链的正式上游工具。

## 4. 第二阶段成果

第二阶段的目标，是在不依赖网页持续操作的情况下，基于标定结果独立完成水花识别。

当前识别程序已经实现：

- 读取第一阶段保存的标定 JSON
- 调用摄像头预置点
- 等待云台转动与基础流稳定
- 打开远端 FLV 流
- 执行自动场景解析
- 执行视觉就绪门控
- 在 ROI 内提取结构与动态特征
- 完成帧级判定与序列级投票
- 输出结构化 JSON 结果
- 异步保存 replay、metadata、配置快照和关键帧证据

这意味着第二阶段已经形成了可复盘、可调试、可留痕的识别流水线，而不是一次性脚本。

## 5. 第二阶段识别算法主线

当前单点识别主链顺序为：

1. 读取标定配置
2. 转到目标预置点
3. 等待 `presetTurnSettleMs` 与 `streamCatchupMs`
4. 执行视觉就绪门控
5. 采样固定时长的短视频序列
6. 解析当前场景模式
7. 对序列做帧间对齐
8. 在 ROI 内提取结构特征与动态特征
9. 先过硬门控，再做加权帧级评分
10. 对整段短序列做时序投票
11. 输出最终结果并保存证据

这里最重要的设计原则有三条：

- 识别对象不是单帧，而是一段短序列
- 先处理整帧抖动，再看 ROI 内部变化
- 先判“像不像水花主体”，再判“动态是否支持它是水花”

## 6. 白天与夜间基线

### 6.1 白天可见光基线

白天主难点是：

- 强反光
- 水波纹
- 倒影
- 局部高亮扰动

当前白天链路已经收敛到“结构主导、动态辅助”的路线。核心判断围绕以下特征：

- 中心连续亮团
- 主亮团面积与覆盖率
- 垂直展开比例
- 连续性与碎片化程度
- 至少一条动态证据

白天单点基线已经通过过多轮正负样本验证，并已冻结为当前可用基线。

### 6.2 夜间红外基线

夜间红外主难点是：

- 画面更模糊
- 噪点更多
- 桨叶本体在红外下也会呈现白色高亮

当前夜间链路走的是“结构更强主导、动态更弱辅助”的路线。核心判断围绕：

- 中央主亮团是否形成
- 覆盖是否充分
- 垂直展开是否明显
- 暗缝是否被填充
- 再辅以较弱的动态证据

夜间链路还有一条已确认的重要经验：

- ROI 不能框得过紧，必须给增氧机轻微偏移和水花外扩留余量

这一点已经在后续现场样本中被反复验证。

## 7. 自动场景切换现状

当前主链已支持三种模式：

- `day_visible`
- `night_ir`
- `auto`

`auto` 并不是简单判断“黑白图还是彩色图”，而是基于颜色与通道统计做轻量场景分类，在明确场景时直接选对应链路，在必要时保留 fallback 诊断信息。

当前已经确认有效的部分：

- 自动场景分类本身能把明显白天和明显红外分开
- 失败结果里已能保留 `requestedSceneMode`、`effectiveSceneMode`、`sceneModeReason`、`sceneModeDiagnostics`
- `sceneMode=auto` 时不再因为失败路径构造问题二次崩溃

结论：自动场景切换“分类接线”基本已打通，但它与视觉就绪门控的配合仍未完全收口。

## 8. 证据链与复盘能力

当前项目已经具备较完整的证据留存能力，包括：

- 结构化识别结果 JSON
- replay sequence
- metadata
- recognition config snapshot
- representative frame
- debug image

后续又补充了：

- 视觉就绪阶段关键帧
- 伪多点 round 顶层摘要字段
- replay 异步保存状态字段

这意味着后续修复已经不是“靠猜”，而是可以基于真实样本、关键帧和配置快照做回放定位。

## 9. 伪多点测试程序的定位

伪多点测试程序的目的不是替代识别算法，而是验证“调度时序是否会破坏识别”。

它主要验证：

- 预置点切换时序是否可靠
- 转回目标点后是否真的稳定了再识别
- 识别未完成前是否不会被下一步打断
- 单点识别链放进重复轮询后是否还能稳定工作

它已经帮助暴露出多类真实问题：

- 过早从过渡预置点打断
- ROI 过紧导致轻微偏移出框
- “构图对了”不等于“对焦完成”
- 视觉就绪门控不能过松，也不能过严

结论：伪多点测试器已经不是试验品，而是多点轮询设计前的重要验证工具。

## 10. 当前真正卡住的问题

当前主问题已经高度聚焦，不再是：

- 标定工具不能用
- 白天单点完全不行
- 夜间红外完全不行
- 自动场景切换整体失效

当前真正卡住的是：

- 在伪多点调度场景下，视觉就绪门控如何既不误放模糊画面，也不过度误杀已经可识别的正常画面

这个问题又进一步拆成两个核心方向。

### 10.1 方向一：ready window 的实现仍然过短

最近一次关键失败样本目录：

- [summary.json](C:/Users/Maple_Rain/Documents/Items/splash_water/data/pseudo_multi_point_tests/AB00A7DPAJ00124_1_p1_t2_no_splash_2026-07-09T14-43-14.507485+00-00/summary.json)
- [round_01.json](C:/Users/Maple_Rain/Documents/Items/splash_water/data/pseudo_multi_point_tests/AB00A7DPAJ00124_1_p1_t2_no_splash_2026-07-09T14-43-14.507485+00-00/round_01.json)
- [round_02.json](C:/Users/Maple_Rain/Documents/Items/splash_water/data/pseudo_multi_point_tests/AB00A7DPAJ00124_1_p1_t2_no_splash_2026-07-09T14-43-14.507485+00-00/round_02.json)

这组 `no_splash` 伪多点在 `sceneMode=auto` 下出现：

- `10 / 10` 全部失败
- 全部失败都发生在 `visual_readiness`
- 其中 `3` 轮是 `visual_not_ready_blurry`
- `7` 轮是 `visual_not_ready_ready_window_short`

尤其是 `round_02` 很关键：

- `effectiveSceneMode = night_ir`
- `framesChecked = 71`
- `elapsedMs = 3500`
- `sharpnessImprovementRatio = 5.7177`
- `sharpnessTrend = 585.27`
- 但 `readyWindowMsActual = 234`
- 最终仍被判为 `visual_not_ready_ready_window_short`

这说明当前问题很可能不是“画面一直不清晰”，而是“ready window 的实现只统计到过短的一段窗口”，导致已经明显变清晰的序列仍被超时拒绝。

### 10.2 方向二：夜间 readiness 阈值仍未真正场景化

从这次失败对应的配置快照可见：

- [recognition-config.snapshot.json](C:/Users/Maple_Rain/Documents/Items/splash_water/data/recognition_replays/AB00A7DPAJ00124_1_2026-07-09T14-43-27.497433+00-00/recognition-config.snapshot.json)

该快照已经显示：

- `sceneMode = night_ir`

但 readiness 仍然是全局默认值：

- `visualReadinessMinSharpness = 300.0`
- `visualReadinessMinReadyWindowMs = 400`
- `visualReadinessMinImprovementRatio = 1.2`
- `visualReadinessStableHighSharpnessMultiplier = 2.0`
- `visualReadinessStableBlurMaxTrend = 40.0`

同时当前 [backend/local_config.json](C:/Users/Maple_Rain/Documents/Items/splash_water/backend/local_config.json) 中，`nightIr` 主要覆盖的是 splash 特征和门控参数，并没有明确覆盖上述 readiness 字段。

这意味着目前已经做到“scene-aware routing”，但还没有做到“scene-aware readiness thresholding”。

## 11. 已确认有效的部分

在进入下一轮修复前，有几件事已经可以明确认为方向正确：

- 单点白天识别主链可用
- 单点夜间红外识别主链可用
- 自动场景分类能正确分出明显的 `day_visible` 与 `night_ir`
- 失败链已经能保留更真实的上下文诊断
- 伪多点调度确实能暴露出单点静态测试看不到的问题

换句话说，当前问题不是主识别算法整体失效，而是“调度前门控层”与“自动场景解析后的 readiness 参数选择”还没有完全对齐。

## 12. 下一轮修复边界

下一轮修复建议只动两块，不要顺手混改 splash 主算法：

1. 修正 readiness sustained window 的实现，让 `readyWindowMsActual` 真正表示持续清晰时段，而不是被过短历史窗口限制。
2. 给 `dayVisible` / `nightIr` 加上独立 readiness override，并确保 `sceneMode=auto` 解析后真正使用对应阈值。

明确不要在这一轮顺手调整：

- 白天 splash 结构权重
- 夜间 splash 结构权重
- 时序投票阈值
- 静态亮物抑制逻辑

原因很简单：当前故障主要发生在正式采样前，如果此时去混调主识别算法，会把“取样前门控问题”和“识别本体问题”搅在一起。

## 13. 当前阶段判断

从交付和推进角度看，目前状态可以概括为：

- 第一阶段标定工具：可用，且已完成结构性收口
- 第二阶段单点识别：可用，白天与夜间基线都已形成
- 自动场景切换：分类接线已打通，但与 readiness 的协同还需继续修
- 伪多点测试器：可用，且已经证明具备发现真实工程问题的价值

因此，当前剩余工作已经收缩为“调度前视觉就绪门控问题”，而不是“从零重做识别算法”。

## 14. 建议的下一步

当前最合理的下一步不是继续在同样条件下反复微调 splash 主算法，而是：

1. 先修 readiness sustained window
2. 再补 day/night readiness overrides
3. 用同一组 `no_splash` 伪多点回归验证
4. 确认不再大面积死在 readiness 前后，再回归 `has_splash`

如果后续需要切换到新会话，这份文档可作为当前阶段的压缩交接基线。
