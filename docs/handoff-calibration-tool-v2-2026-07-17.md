# Handoff: 新版标定工具实现

## 任务目标

在新的会话中实现新版标定网页及其配套标定管理 API。目标是将当前开发型英文页面重构为全中文、桌面优先的工业工作台，同时补齐版本化标定、双快照与导出能力。

完整已确认设计见：[新版标定工具设计](./calibration-tool-v2-design.md)。先阅读该文档；它是已确认需求的权威来源，不要重新讨论或擅自缩减其中的规则。

## 产品边界

- 本轮只改标定工具：`frontend/`、`backend/` 与必要的 `API-SDK-SignPythonDemo/` 接口适配。
- 不改水花识别算法、`inspector/` 的行为、阈值或 replay 逻辑。
- 识别程序当前仍会读取 `data/calibrations/<device>_<preset>.json`。这个当前生效文件必须继续存在且保持旧字段兼容。
- 新字段必须可选，旧标定必须可读取；首次更新旧标定才进入版本历史。
- 标定工具是电脑端完整使用，最小设计尺寸 `1280 x 800`；小于 `1024px` 不承诺完整 ROI 编辑。

## 当前实现入口

### 前端

- 页面总控：[CalibrationPage.tsx](../frontend/src/pages/CalibrationPage.tsx)
- 双 ROI 画布：[RoiCanvas.tsx](../frontend/src/components/RoiCanvas.tsx)
- 视频和冻结帧：[StreamPreview.tsx](../frontend/src/components/StreamPreview.tsx)
- 设备、云台、预置点、保存面板：`frontend/src/components/`
- 草稿状态：[useCalibrationDraft.ts](../frontend/src/hooks/useCalibrationDraft.ts)
- API 类型与调用：`frontend/src/types/calibration.ts`、`frontend/src/api/calibrationApi.ts`
- 当前样式：[styles.css](../frontend/src/styles.css)

`CalibrationPage.tsx` 当前集中了页面编排、请求、判稳状态机、定时器和 ROI 操作。重构时应拆分为清晰的 feature 组件和 hooks；建议至少拆出会话/草稿、取景判稳、已有标定与版本历史三个责任边界。

### 后端

- 标定 schema：[calibration.py](../backend/app/schemas/calibration.py)
- 标定 API：[calibration_router.py](../backend/app/routers/calibration_router.py)
- 文件存储：[calibration_storage_service.py](../backend/app/services/calibration_storage_service.py)
- 全局路径与数据目录：[config.py](../backend/app/config.py)

当前 `save()` 会覆盖 `data/calibrations/<device>_<preset>.json` 并只保存原始 PNG。新版需要保留该文件作为当前生效配置，同时新增历史目录和版本 API。

## 必须实现的关键能力

按设计文档实现，重点如下：

1. 单页工作台：顶部连接栏、左侧预置点/云台、中间视频与双 ROI、右侧检查单和保存区；日志移入诊断抽屉。
2. 双 ROI：识别 ROI 红色、对焦锚点 ROI 蓝色；始终同时显示，一次只编辑一个；切换预置点或云台移动后冻结帧与 ROI 失效。
3. 标定保护：必须绑定已有预置点；存在未保存草稿时，切换/加载/新建前确认丢弃。
4. 预置点覆盖：已标定、未标定、当前标定中、不纳入巡检，并显示覆盖概览。
5. 既有标定：加载、查看、更新确认；保存成功后保留当前结果，用户显式新建下一条。
6. 双快照：保存原始冻结帧与带红蓝 ROI、标签、设备/预置点/时间/坐标的标注快照。
7. 历史版本：历史不可变；恢复旧版本必须创建新版本并记录 `restoredFromVersion`；本地不自动清理。
8. 导出：当前有效配置、全部当前有效配置、含快照与清单的标定归档。不要定义管理系统上传协议。
9. 兼容迁移：旧记录显示为旧版；首次更新时归档旧记录为 `v0001`（`legacy=true`），新保存为 `v0002`。

## 建议数据策略

保持当前生效文件：

```text
data/calibrations/<device>_<preset>.json
```

新增不可变历史：

```text
data/calibration_history/<device>_<preset>/v0001/
  calibration.json
  snapshot-original.png
  snapshot-annotated.png
```

恢复历史版本时复制内容创建新版本，不把旧目录直接设为当前。部署导出只含当前有效配置；归档导出使用相对路径清单，不把本机绝对路径带入包内。

## 视觉与交互约束

- 全中文，不能保留截图中英文和中文混杂的问题。
- 浅色、克制、工业工作台；深青为普通主操作色。
- 红色仅用于识别 ROI、危险/覆盖确认；蓝色仅用于对焦锚点 ROI；状态同时使用文字和图标，不只依赖颜色。
- 检查单状态：未完成、进行中、已完成、需重新完成、异常、已保存。
- 原始技术指标、播放器事件和后端日志不占首屏；在诊断抽屉中查看。

## 验收

至少覆盖：

- 新建标定、绑定预置点、冻结帧、红蓝双 ROI、保存和新建下一条。
- 预置点切换或云台移动后 ROI 失效。
- 未保存草稿切换时确认保护。
- 已有标定加载、更新确认、历史查看、恢复生成新版本。
- 原始/标注快照均存在且标注位置准确。
- 当前/全部配置导出与归档导出可用。
- 旧 `data/calibrations` 记录继续读取，且 `inspector` 仍可消费当前生效 JSON。
- 前端在 `1280 x 800`、`1440px` 以上和 `1024-1439px` 下布局正确；小屏提示正确。
- 使用浏览器实际操作验证 ROI 画布、视频重连和弹窗，不只运行 TypeScript 编译。

## 当前工作区与提交提醒

当前存在未提交文档变更：

- 修改：`docs/README.md`、`docs/current-status-and-next-focus-zh.md`、`docs/night-ir-baseline.md`
- 新增：`docs/business-briefing-2026-07-16.md`、`docs/calibration-tool-v2-design.md`、本交接文档

不要还原这些变更。Git 元数据写入受会话限制时，由用户执行 `git add`、`git commit`、`git push`。

## 建议下一步

先使用 `$prototype` 做可运行桌面工作台原型，确认布局后再实现。若直接进入工程化实施，先定义后端 schema/API 与存储版本策略，再重构前端状态和组件，最后用 `$playwright` 做真实页面验收。
