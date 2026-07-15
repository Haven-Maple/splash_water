# 当前成果与下一步重点

日期：2026-07-15

## 1. 当前交付边界

当前项目交付的是“可标定、可独立执行、可复盘”的单目标水花识别能力：

- 标定工具为每个预置点维护识别 ROI 与对焦锚点 ROI。
- 识别主链完成预置点切换、流新鲜度处理、场景判稳、视觉判稳、样本质量守卫、时序判定和异步 replay 留存。
- `auto` 根据稳定图像选择 `day_visible` 或 `night_ir`；`day_visible_twilight` 仅是白天可见光的等待预算子档，不是独立的 splash 算法。
- 伪多点测试用于验证转点不会破坏识别，不是生产调度器。

当前不包含生产级多目标编排、设备运行状态接入、异常告警派发或模型训练。异常告警属于后续集成：只有 `executionResult=success` 且 `visualState=no_splash` 才可与设备状态交叉比对；`undetermined` 只能重试或复盘。

## 2. 已验证状态

### 白天可见光

白天 `day_visible` 是当前稳定验收的主线。现场多轮正负样本已表明，ROI 收口、白色泡沫静态亮斑抑制、判稳和小幅 sample-quality 恢复预算能让正常画面稳定进入识别并保持区分。

白天仍应以新增现场 replay 回归，而不是凭连续成功轮次继续放宽阈值。

### 夜间红外

夜间 `night_ir` 已有可运行、可复盘的基线，但尚未达到稳定验收。`hardGateMinGapFillRatio=0.81` 的离线扫描和一组现场回归曾得到有水花 `10/10`、无水花 `10/10`；有水花的 `gapFillPassCount` 为 `18-20/20`，无水花为 `0`。

后续强制 IR 的有水花样本又出现 `6/10`，失败集中在 `centerBrightCoverage` 硬门控通过帧仅 `1-3/20`，成功轮为 `13-20/20`。因此当前结论是“基线可运行，但存在已定位的假阴性边界”，不能写成夜间稳定验收。

## 3. 已解决并保留的工程机制

- 流启动新鲜度守卫：排空预置点切换前遗留的旧流帧，并保存启动阶段证据。
- 场景判稳：`auto` 在完整 probe 窗口稳定后才选择图像配置；关闭守卫时回退为单次 probe，不误报 transition timeout。
- 视觉判稳与样本质量守卫：分别保证对焦稳定和采样期间质量；夜间包含 ready 后短复验与受限恢复预算。
- 断流语义与重开：读流超时或中断不再伪装成模糊，单轮仅重开一次 session，并记录原因与重开结果。
- 双 ROI 标定：识别 ROI 只服务 splash 特征，对焦锚点 ROI 只服务判稳；旧标定仅临时 fallback。
- 日间静态亮斑抑制：白色泡沫等静态亮物门控只作用于 `day_visible`，不再误杀夜间真实水花。
- 结构化复盘：replay metadata 与伪多点 round 记录流新鲜度、场景判稳、双 ROI 来源、判稳、样本质量、读流异常和关键硬门控计数。

## 4. 当前关键配置快照

可执行配置的唯一来源是 [backend/local_config.example.json](../backend/local_config.example.json)；本机 `backend/local_config.json` 可覆盖它，且不进入版本控制。以下仅冻结已验证、会影响判断边界的关键值：

| 范围 | 当前关键值 |
| --- | --- |
| 场景路由 | `sceneMode=auto`；stream startup freshness 与 scene-mode stability 均启用 |
| 场景判稳 | `2` 个完整窗口，每窗口 `4` 帧，超时 `1600 ms`，最多 `1` 次 relock |
| 白天样本质量 | timeout `5200 ms`，最多 `3` 次恢复 |
| 黄昏白天子档 | timeout `6000 ms`，最多 `4` 次恢复，最短观察 `1500 ms` |
| 夜间判稳 | 最低清晰度 `50.0`，清晰度 margin `8.0`，ready 后复验 `2` 帧 / `180 ms` |
| 夜间样本质量 | timeout `5200 ms`，最多 `3` 次恢复 |
| 夜间关键硬门控 | `hardGateMinGapFillRatio=0.81`；`hardGateMinCenterBrightCoverage=0.46` |
| 时序判定 | `20` 帧、`10 fps`、`2000 ms`；`framePassThreshold=0.6`、`sequenceVoteThreshold=0.6` |

这些值不是逐点配置。新阈值只能在同一批正负 replay 上离线扫描，并通过现场回归后更新。

## 5. 当前唯一识别重点

下一步只调查 `nightIr.hardGateMinCenterBrightCoverage` 的假阴性边界：

1. 使用已保存的夜间有水花与无水花 replay 做离线扫描。
2. 记录每个候选阈值的 `centerBrightCoveragePassCount`、hard-gate 通过率和最终视觉状态。
3. 选择同时保持无水花拒绝、且恢复真实有水花的最高安全阈值。
4. 再进行夜间现场正负样本回归。

在证据出现前，不同时调整 gap fill、时序投票、白天门控或样本质量规则。

## 6. 明确暂不做

- `night_visible` 第三套识别 profile，以及识别运行中彩色/红外切换的处理。
- 通过额外日夜参考图进行构图一致性守卫；相关代码已删除，不保留 dormant 开关。
- HLS 自动降级、训练模型、生产级多增氧机调度、设备状态/告警集成。
- 为单个预置点维护独立 splash 阈值；当前使用共享的场景 profile。

夜间部署前提是摄像头固定红外黑白模式。重新考虑 `night_visible` 或构图守卫前，必须先取得可重复、带 replay 证据的真实问题样本。
