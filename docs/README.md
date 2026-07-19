# 文档索引

## 当前权威文档

- [当前成果与下一步重点](current-status-and-next-focus-zh.md)：当前交付边界、验证状态、关键配置和唯一识别重点。
- [2026-07-20 发布基线](release-baseline-2026-07-20.md)：控制面短超时重试、FLV 受预算恢复与 220 轮现场验证结果。
- [2026-07-19 发布基线](release-baseline-2026-07-19.md)：本次标定工具 V2、夜间 ROI 容忍、流超时加固及未解决控制面问题的交付边界。
- [阶段成果与技术说明](business-briefing-2026-07-16.md)：面向业务汇报与技术答疑的系统能力、技术栈和运行流程说明。
- [新版标定工具设计](calibration-tool-v2-design.md)：已确认的工作流、双 ROI、版本管理、导出与界面信息层级。
- [新版标定工具交接](handoff-calibration-tool-v2-2026-07-17.md)：供后续会话实现新版标定工具的代码入口、边界与验收清单。
- [白天可见光识别基线](daytime-recognition-baseline.md)：当前稳定验收的白天策略与关键阈值。
- [夜间 IR 识别基线](night-ir-baseline.md)：固定 IR 条件下已阶段性验收的夜间策略、阈值与已知边界。
- [夜间全彩 / IR 切换延期记录](night-visible-ir-transition-deferred.md)：固定 IR 的运行决策和重开条件。
- [本地配置文件使用说明](local-config-usage.md)：本机配置与密钥安全约定。
- [ADR 0001](adr/0001-stable-night-imaging-and-composition-boundary.md)：固定夜间 IR 并拒绝构图守卫的决策。

`backend/local_config.example.json` 是可执行参数的唯一示例来源；文档只记录已经验证的关键阈值与决策，不能替代配置文件。

## 历史记录

其余 `*-plan.md`、`*-design.md`、`*-checklist.md`、`*-implementation-*.md` 和开发日志保留为实施与排查历史，不是当前计划或参数来源。需要决定下一步时，先阅读本索引列出的当前权威文档。
