# 文档索引

## 当前权威文档

- [当前成果与下一步重点](current-status-and-next-focus-zh.md)：当前交付边界、验证状态、关键配置和唯一识别重点。
- [白天可见光识别基线](daytime-recognition-baseline.md)：当前稳定验收的白天策略与关键阈值。
- [夜间 IR 识别基线](night-ir-baseline.md)：可运行但尚未稳定验收的夜间策略与已知边界。
- [夜间全彩 / IR 切换延期记录](night-visible-ir-transition-deferred.md)：固定 IR 的运行决策和重开条件。
- [本地配置文件使用说明](local-config-usage.md)：本机配置与密钥安全约定。
- [ADR 0001](adr/0001-stable-night-imaging-and-composition-boundary.md)：固定夜间 IR 并拒绝构图守卫的决策。

`backend/local_config.example.json` 是可执行参数的唯一示例来源；文档只记录已经验证的关键阈值与决策，不能替代配置文件。

## 历史记录

其余 `*-plan.md`、`*-design.md`、`*-checklist.md`、`*-implementation-*.md` 和开发日志保留为实施与排查历史，不是当前计划或参数来源。需要决定下一步时，先阅读本索引列出的当前权威文档。
