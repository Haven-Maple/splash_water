# 标定工具问题分析与修复方案

## 1. 文档目的

本文档用于记录当前标定工具联调时暴露出的关键问题，并给出明确的修复方向、字段修正方案、页面交互调整建议和验收重点，供后续实现对话直接按清单修改。

## 2. 当前暴露的问题概览

根据当前代码、浏览器控制台日志和大华请求体示例，当前问题不是单一页面美观问题，而是以下三层问题叠加：

- 大华接口请求体字段与实际示例不一致
- 页面交互模型与真实标定流程不够匹配
- 错误回显、日志展示和联调反馈不足

这三类问题会直接导致：

- PTZ 按钮点击后设备无动作
- 预置点保存与跳转无效
- 标定配置保存失败但页面提示不清晰
- 用户误以为是 CORS 问题，实际是请求失败后的表象

## 3. 关键问题定位

## 3.1 PTZ 请求体字段错误

当前代码位置：

- [backend/app/services/dahua_ptz_service.py](C:/Users/Maple_Rain/Documents/Items/splash_water/backend/app/services/dahua_ptz_service.py:1)

当前实现使用的字段：

- `xSpeed`
- `ySpeed`
- `zoomSpeed`
- `durationMs`

但你提供的大华请求体示例为：

```json
{
  "deviceId":"8E0A090PAG6A67D",
  "channelId":"0",
  "operation":"10",
  "duration":1000,
  "horizontalSpeed":0.25,
  "verticalSpeed":0.25
}
```

说明当前 PTZ 请求体字段模型是错的，至少存在以下错位：

- `durationMs` 应改为 `duration`
- `xSpeed` 应改为 `horizontalSpeed`
- `ySpeed` 应改为 `verticalSpeed`
- 当前代码使用 `action + speed vector` 模型，但大华接口示例要求显式 `operation`

结论：

- 目前 PTZ 功能无法工作，最主要原因是请求体结构与厂商接口不匹配

## 3.2 PTZ 接口路径疑似错误

当前代码使用：

- `/open-api/api-aiot/device/controlMovePTZ`

但你前面提供的是：

- `https://open.cloud-dahua.com/api-aiot/device/controlMovePTZ`

也就是缺少 `/open-api`

这意味着当前 PTZ 功能还有一层潜在问题：

- 即使请求体字段修正，如果路径本身不对，仍然不会成功

结论：

- 需要优先确认 PTZ 接口真实路径
- 如果按你提供的 URL 为准，则必须单独修正 PTZ service 的 endpoint

## 3.3 预置点字段错误

当前代码位置：

- [backend/app/services/dahua_preset_service.py](C:/Users/Maple_Rain/Documents/Items/splash_water/backend/app/services/dahua_preset_service.py:1)
- [backend/app/schemas/preset.py](C:/Users/Maple_Rain/Documents/Items/splash_water/backend/app/schemas/preset.py:1)

当前使用字段：

- 保存：`presetId`、`presetName`
- 跳转：`presetId`

但你给的大华示例是：

保存预置点：

```json
{
  "deviceId": "AA03012YXXXXX",
  "channelId": "0",
  "name": "vnGji",
  "index": 10
}
```

转动到预置点：

```json
{
  "deviceId": "AA03012YXXXXXXX",
  "channelId": "0",
  "index": 9
}
```

说明当前预置点逻辑错误如下：

- `presetId` 应改为 `index`
- `presetName` 应改为 `name`
- 查询结果归一化逻辑也应优先识别 `index` 和 `name`

结论：

- 当前预置点保存和跳转不工作，主要原因就是字段名模型错了

## 3.4 页面命名误导

当前前端位置：

- [frontend/src/components/PresetPanel.tsx](C:/Users/Maple_Rain/Documents/Items/splash_water/frontend/src/components/PresetPanel.tsx:1)

当前页面字段名仍是：

- `Preset ID`
- `Preset Name`

但按你当前接口定义，更准确的业务语言应该是：

- `Preset Index`
- `Preset Name`

问题在于：

- `ID` 容易让人误以为是字符串型标识
- 实际这里更像是厂商规定的预置点序号

结论：

- 前后端都应把 `presetId` 统一重命名为 `presetIndex`

## 3.5 保存标定配置的页面校验不完整

当前前端位置：

- [frontend/src/components/SaveCalibrationPanel.tsx](C:/Users/Maple_Rain/Documents/Items/splash_water/frontend/src/components/SaveCalibrationPanel.tsx:1)

当前按钮禁用条件只检查：

- `deviceId`
- `presetId`
- `targetId`
- `targetName`
- `roi`

但后端保存 schema 还要求：

- `presetName`

这会导致：

- 前端允许点击保存
- 后端因字段不完整失败

即使这不一定是你当前 500 的唯一原因，它仍然是明确的逻辑缺陷。

## 3.6 当前“CORS 报错”更像是后端 500 的表象

浏览器控制台显示：

- `No 'Access-Control-Allow-Origin' header`
- `500 Internal Server Error`

当前判断：

- 这更像是 `saveCalibration` 在后端执行中抛了异常
- 浏览器把最终失败显示成了 CORS + 500 组合问题

当前最可疑点包括：

- 标定保存字段不完整
- 保存时后端数据处理异常
- 后端没有把异常信息清晰返回给前端

结论：

- 不建议先把精力花在“改 CORS”上
- 应优先查后端 `/api/calibration/save` 的实际报错栈和参数内容

## 3.7 页面逻辑顺序不够贴合真实操作

当前页面是功能都摆出来了，但用户路径不够明确，导致使用感“奇怪”。

当前更合理的标定顺序应是：

1. 连接设备
2. 检查在线状态
3. 加载预览流
4. PTZ 微调画面
5. 保存或覆盖预置点
6. 查询并验证预置点
7. 冻结当前帧
8. 框选 ROI
9. 保存标定配置

当前问题是：

- 页面没有明显分步骤
- 用户可以在上游信息不完整时操作下游功能
- 保存配置和预置点逻辑没有形成自然闭环

## 4. 修复方案

## 4.1 PTZ 修复方案

### 4.1.1 请求体改造

将 PTZ 请求体改为更贴近厂商协议：

```json
{
  "deviceId": "xxx",
  "channelId": "0",
  "operation": "10",
  "duration": 300,
  "horizontalSpeed": 0.25,
  "verticalSpeed": 0.25
}
```

需要改动：

- `backend/app/schemas/ptz.py`
- `backend/app/services/dahua_ptz_service.py`
- `frontend/src/api/ptzApi.ts`

### 4.1.2 引入操作码映射表

不要再让前端直接传 `action` 给后端做向量换算，而是建立一张明确映射表：

- `up`
- `down`
- `left`
- `right`
- `upLeft`
- `upRight`
- `downLeft`
- `downRight`
- `zoomIn`
- `zoomOut`

都映射到厂商 `operation`

注意：

- 你目前提供的示例只出现了一个 `operation: "10"`
- 因此还必须补齐完整的 `operation` 编码表

如果编码表没确认，缩放和斜向移动都不应贸然写死。

### 4.1.3 PTZ 按键修改建议

页面建议从英文文字按钮改成更直观的中文/方向按钮：

- `左上` `上` `右上`
- `左` `停止/空位` `右`
- `左下` `下` `右下`
- `放大`
- `缩小`

步长建议改成：

- `微调`
- `标准`
- `大步`

每次点击只发送一次短动作。

## 4.2 预置点修复方案

### 4.2.1 字段统一改名

统一把：

- `presetId` 改为 `presetIndex`
- `presetName` 保留或改为 `presetName`

后端真正向厂商发送时使用：

- `index`
- `name`

### 4.2.2 查询结果归一化

查询预置点时，优先识别：

- `index`
- `name`

兼容保留：

- `presetId`
- `presetName`

但厂商主路径应以 `index/name` 为准。

### 4.2.3 页面文案修改

建议将 `Preset` 区块的按钮改成：

- `查询预置点`
- `保存当前为预置点`
- `转到预置点`

输入项改成：

- `预置点序号`
- `预置点名称`

## 4.3 标定保存修复方案

### 4.3.1 前端保存条件补齐

保存标定配置前必须确保：

- 设备 ID 存在
- 通道 ID 存在
- 预置点序号存在
- 预置点名称存在
- 目标 ID 存在
- 目标名称存在
- ROI 存在
- 冻结帧截图已生成

### 4.3.2 后端错误回显增强

`/api/calibration/save` 失败时，前端需要明确展示：

- 请求参数摘要
- 后端返回 message
- 是否是字段校验失败

而不是只在浏览器控制台看到 `ERR_FAILED`

## 4.4 日志与联调反馈修复方案

### 4.4.1 页面增加操作反馈区

建议在页面顶部或右侧增加一个“联调日志/操作结果”区域，显示最近几条：

- `PTZ 命令已发送`
- `预置点保存成功`
- `预置点跳转失败`
- `标定配置保存失败`

### 4.4.2 后端日志可视化

建议新增一个只读调试接口，例如：

- `GET /api/debug/recent-logs`

返回最近若干条 vendor 调用日志，便于页面内直接查看，不必每次手动翻文件。

### 4.4.3 失败日志必须带请求摘要

尤其以下接口：

- `controlMovePTZ`
- `configPreset`
- `turnPreset`
- `saveCalibration`

失败时必须能看到实际请求体摘要。

## 4.5 页面流程修复方案

建议把页面结构改成明确的 5 步：

### 第一步：设备连接

- 设备 ID
- 通道 ID
- 在线检查
- 加载 FLV/HLS

### 第二步：云台控制

- 方向键
- 缩放键
- 步长选择

### 第三步：预置点管理

- 查询预置点
- 输入预置点序号
- 输入预置点名称
- 保存当前为预置点
- 转到预置点

### 第四步：画面标定

- 冻结当前帧
- 框选 ROI

### 第五步：保存配置

- 目标 ID
- 目标名称
- 备注
- 保存标定配置

这样的流程更符合实际心智，而不是把所有按钮并列堆在一起。

## 5. 优先修改顺序

建议按以下顺序修改，而不是一起乱改：

1. 修正 PTZ endpoint 和 PTZ 请求体字段
2. 修正预置点 save/turn/query 字段
3. 统一前后端 `presetIndex` 命名
4. 修复保存标定配置的前端校验
5. 增加操作结果日志区
6. 优化页面分步结构和按钮文案

## 6. 验收重点

修完后优先验证以下 6 项：

- PTZ 上下左右能否真实移动
- 缩放能否真实生效
- 预置点保存后能否查询到
- 预置点跳转后能否回到目标位置
- 冻结帧和 ROI 是否可保存
- 标定配置保存是否成功且无 500

## 7. 当前推荐结论

当前版本的主要问题不是“实现不完整”，而是“与厂商协议对接层有明显错位”。只要先把 PTZ 和预置点字段模型纠正，再把页面交互顺序收紧，工具的可用性会明显提升。
