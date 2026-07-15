# 标定工具具体实现方案

## 1. 文档目的

本文档给出标定工具的具体实现蓝图，面向后续实际编码工作。目标是让另一个对话或另一位开发者能够直接按本文档分阶段实现，而不需要重新进行高层方案设计。

本文档默认第一阶段只实现单设备、单点标定闭环。

## 2. 推荐技术路线

在当前已有 Python 签名示例的前提下，推荐采用以下技术组合：

- 后端：Python + FastAPI
- 前端：Vite + React + TypeScript
- FLV 预览：`flv.js`
- HLS 预览：浏览器原生或 `hls.js`
- 配置存储：本地 JSON 文件
- 截图存储：本地文件目录

推荐原因：

- Python 便于直接复用现有大华签名工具
- FastAPI 适合快速搭建本地工具后端
- React 便于实现预览、PTZ 控件和 ROI 框选
- FLV/HLS 前端生态成熟，便于远程预览

## 3. 建议目录结构

建议按以下结构实现：

```text
/
├─ API-SDK-SignPythonDemo/
│  └─ request_sign_utils.py
├─ backend/
│  ├─ app/
│  │  ├─ main.py
│  │  ├─ config.py
│  │  ├─ schemas/
│  │  │  ├─ device.py
│  │  │  ├─ preset.py
│  │  │  ├─ ptz.py
│  │  │  └─ calibration.py
│  │  ├─ services/
│  │  │  ├─ dahua_auth_service.py
│  │  │  ├─ dahua_device_service.py
│  │  │  ├─ dahua_stream_service.py
│  │  │  ├─ dahua_ptz_service.py
│  │  │  ├─ dahua_preset_service.py
│  │  │  └─ calibration_storage_service.py
│  │  ├─ routers/
│  │  │  ├─ device_router.py
│  │  │  ├─ stream_router.py
│  │  │  ├─ ptz_router.py
│  │  │  ├─ preset_router.py
│  │  │  └─ calibration_router.py
│  │  ├─ utils/
│  │  │  ├─ request_sign_adapter.py
│  │  │  ├─ logging_utils.py
│  │  │  └─ time_utils.py
│  │  └─ logs/
│  └─ requirements.txt
├─ frontend/
│  ├─ src/
│  │  ├─ api/
│  │  │  ├─ deviceApi.ts
│  │  │  ├─ streamApi.ts
│  │  │  ├─ ptzApi.ts
│  │  │  ├─ presetApi.ts
│  │  │  └─ calibrationApi.ts
│  │  ├─ components/
│  │  │  ├─ DevicePanel.tsx
│  │  │  ├─ StreamPreview.tsx
│  │  │  ├─ PtzControlPanel.tsx
│  │  │  ├─ PresetPanel.tsx
│  │  │  ├─ RoiCanvas.tsx
│  │  │  └─ SaveCalibrationPanel.tsx
│  │  ├─ pages/
│  │  │  └─ CalibrationPage.tsx
│  │  ├─ types/
│  │  │  ├─ device.ts
│  │  │  ├─ preset.ts
│  │  │  ├─ ptz.ts
│  │  │  └─ calibration.ts
│  │  ├─ hooks/
│  │  │  ├─ useStreamPlayer.ts
│  │  │  └─ useCalibrationDraft.ts
│  │  ├─ utils/
│  │  │  └─ roi.ts
│  │  └─ main.tsx
│  └─ package.json
├─ data/
│  ├─ calibrations/
│  └─ snapshots/
└─ docs/
```

## 4. 后端实现方案

## 4.1 后端职责

后端负责 5 类事情：

- 大华开放接口鉴权与签名
- 设备与流地址查询
- PTZ 与预置点控制
- 标定配置保存与读取
- 日志与留痕记录

后端不负责：

- 直接解码视频流
- 在服务端完成 ROI 框选
- 做复杂任务调度

## 4.2 后端分模块建议

### 4.2.1 `request_sign_adapter.py`

职责：

- 包装现有 `request_sign_utils.py`
- 提供统一的 `post_signed_request()` 方法
- 自动处理 token 获取、header 构造、异常包装

建议方法：

- `get_app_access_token()`
- `post_open_api(path: str, body: dict, extra_headers: dict | None = None)`

### 4.2.2 `dahua_device_service.py`

职责：

- 调用 `deviceOnline`
- 返回标准化设备状态

建议输出：

```json
{
  "deviceId": "xxx",
  "online": true,
  "raw": {}
}
```

### 4.2.3 `dahua_stream_service.py`

职责：

- 调用 `queryDeviceFlvLive`
- 调用 `getHlsLiveList`
- 封装成统一结果

建议方法：

- `get_flv_stream(device_id, channel_id)`
- `get_hls_stream(device_id, channel_id)`
- `get_preferred_stream(device_id, channel_id, prefer="flv")`

建议输出：

```json
{
  "streamType": "flv",
  "streamUrl": "https://...",
  "fallbackAvailable": true,
  "raw": {}
}
```

### 4.2.4 `dahua_ptz_service.py`

职责：

- 封装 PTZ 控制接口

建议方法：

- `move_up(step_profile)`
- `move_down(step_profile)`
- `move_left(step_profile)`
- `move_right(step_profile)`
- `move_diagonal(direction, step_profile)`
- `zoom_in(step_profile)`
- `zoom_out(step_profile)`
- `move_custom(x_speed, y_speed, duration_ms, zoom_speed=None)`

建议预设步长：

- `small`
- `medium`
- `large`

建议统一映射到速度和持续时间。

### 4.2.5 `dahua_preset_service.py`

职责：

- 查询预置点
- 保存预置点
- 跳转预置点

建议方法：

- `query_presets(device_id, channel_id)`
- `save_preset(device_id, channel_id, preset_id, preset_name)`
- `turn_preset(device_id, channel_id, preset_id)`

### 4.2.6 `calibration_storage_service.py`

职责：

- 保存标定配置 JSON
- 保存冻结帧截图
- 根据 `deviceId + presetId` 查询配置

建议目录：

- `data/calibrations/`
- `data/snapshots/`

建议命名规则：

- 配置：`{deviceId}_{presetId}.json`
- 截图：`{deviceId}_{presetId}_{timestamp}.png`

## 4.3 后端本地接口定义

建议后端先提供以下接口。

### 4.3.1 设备接口

- `POST /api/device/online`

请求：

```json
{
  "deviceId": "xxx"
}
```

### 4.3.2 流接口

- `POST /api/stream/flv`
- `POST /api/stream/hls`
- `POST /api/stream/preferred`

请求：

```json
{
  "deviceId": "xxx",
  "channelId": "0"
}
```

### 4.3.3 PTZ 接口

- `POST /api/ptz/move`

请求：

```json
{
  "deviceId": "xxx",
  "channelId": "0",
  "action": "up",
  "stepProfile": "small"
}
```

或：

```json
{
  "deviceId": "xxx",
  "channelId": "0",
  "xSpeed": 0.2,
  "ySpeed": -0.2,
  "durationMs": 200
}
```

### 4.3.4 预置点接口

- `POST /api/preset/query`
- `POST /api/preset/save`
- `POST /api/preset/turn`

### 4.3.5 标定配置接口

- `POST /api/calibration/save`
- `GET /api/calibration/get`
- `GET /api/calibration/list`

`POST /api/calibration/save` 请求建议：

```json
{
  "version": "1.0",
  "deviceId": "DEVICE_ID",
  "channelId": "CHANNEL_ID",
  "targetId": "AERATOR_001",
  "targetName": "1号增氧机",
  "presetId": "PRESET_001",
  "presetName": "1号增氧机预置点",
  "roi": {
    "x": 320,
    "y": 180,
    "width": 260,
    "height": 220
  },
  "streamPreference": "flv",
  "captureMode": "snapshot",
  "captureDurationMs": 1500,
  "ptzSettleMs": 1200,
  "snapshotPath": "data/snapshots/xxx.png",
  "notes": ""
}
```

## 5. 前端实现方案

## 5.1 页面结构

前端建议只有一个主页面：`CalibrationPage`

页面分为 5 个区域：

- 设备连接区
- 视频预览区
- PTZ 控制区
- 预置点操作区
- 标定保存区

## 5.2 组件职责

### 5.2.1 `DevicePanel.tsx`

职责：

- 输入设备 ID、通道 ID
- 检查在线状态
- 获取预览流

### 5.2.2 `StreamPreview.tsx`

职责：

- 播放 FLV 或 HLS
- 提供刷新预览
- 支持冻结当前帧
- 将冻结帧传给 ROI 组件

建议：

- 把“实时播放”和“冻结帧”拆开
- 冻结后停止用户继续直接画在视频层上

### 5.2.3 `PtzControlPanel.tsx`

职责：

- 提供方向按钮
- 提供变倍按钮
- 提供步长档位选择

### 5.2.4 `PresetPanel.tsx`

职责：

- 查询预置点列表
- 输入新预置点 ID/名称
- 保存预置点
- 跳转预置点

### 5.2.5 `RoiCanvas.tsx`

职责：

- 在冻结帧上框选 ROI
- 支持拖拽调整
- 输出矩形坐标

### 5.2.6 `SaveCalibrationPanel.tsx`

职责：

- 输入目标名称、目标 ID、备注
- 查看当前配置摘要
- 提交保存标定配置

## 5.3 前端状态建议

建议前端维护一个 `calibrationDraft` 状态对象，结构与保存配置结构基本一致。

它至少要包含：

- `deviceId`
- `channelId`
- `presetId`
- `presetName`
- `targetId`
- `targetName`
- `roi`
- `streamPreference`
- `snapshotPath`
- `notes`

## 6. 实现顺序

建议编码顺序如下。

### 6.1 第一轮：后端骨架

实现目标：

- 跑起 FastAPI
- 接入签名工具
- 跑通 `deviceOnline`

验收结果：

- 可以通过本地接口看到设备在线结果

### 6.2 第二轮：流地址获取

实现目标：

- 跑通 FLV 地址获取
- 跑通 HLS 降级获取

验收结果：

- 本地接口能返回标准化流地址结果

### 6.3 第三轮：PTZ 与预置点

实现目标：

- 跑通 PTZ 移动
- 跑通预置点查询
- 跑通预置点保存
- 跑通预置点跳转

验收结果：

- 能完成一次手动转动和一次预置点保存

### 6.4 第四轮：前端基础页面

实现目标：

- 页面能连接设备
- 页面能预览 FLV/HLS
- 页面能触发 PTZ 操作

验收结果：

- 能从浏览器里手动把摄像头调到目标位置

### 6.5 第五轮：冻结帧与 ROI

实现目标：

- 能冻结当前帧
- 能在冻结帧上框选 ROI
- 能展示 ROI 坐标

验收结果：

- 能产生可复用的 ROI 数据

### 6.6 第六轮：配置保存

实现目标：

- 保存 JSON 配置
- 保存冻结帧截图
- 支持读取已有配置

验收结果：

- 一次完整标定可以被复现

## 7. 工作留痕方案

实现时必须保留三类痕迹。

### 7.1 接口调用留痕

记录内容：

- 调用时间
- 本地接口名
- 大华原始接口名
- 请求核心参数摘要
- 返回码
- 错误信息

不要记录：

- 明文密钥
- 完整 token

建议日志文件：

- `backend/app/logs/api.log`

### 7.2 标定结果留痕

记录内容：

- 最终配置 JSON
- 冻结帧截图
- ROI 坐标
- 保存时设备信息

建议目录：

- `data/calibrations/`
- `data/snapshots/`

### 7.3 问题排查留痕

建议在 `docs/` 下维护一份问题记录文档，至少记录：

- 问题现象
- 复现步骤
- 初步判断
- 最终原因
- 规避方式

建议文件：

- `docs/calibration-tool-dev-log.md`

## 8. 重点防踩坑项

### 8.1 FLV 延迟并不一定很低

建议：

- 不要假设 FLV 就是零延迟
- 必须做冻结帧能力

### 8.2 PTZ 微调不适合长时间移动

建议：

- 默认使用小步和中步
- 用多次点击代替一次长移动

### 8.3 预置点保存后不一定完全一致

建议：

- 保存后立刻调用 `turnPreset` 回查
- 必要时允许覆盖保存

### 8.4 ROI 过紧会拖累后续识别

建议：

- 第一版不要极限贴边
- 预留安全边界

### 8.5 不要把私有流问题当当前阻塞项

建议：

- 先用 FLV/HLS 把工具做出来
- 私有 `getStreamUrl` 后续单独研究

## 9. 开发完成定义

标定工具第一阶段完成的定义是：

- 设备可在线检查
- FLV 预览可用
- PTZ 控制可用
- 预置点查询、保存、跳转可用
- 冻结帧与 ROI 框选可用
- 配置 JSON 与截图保存可用
- 全链路日志可追踪

只要以上成立，就可以交给下一个阶段去接识别程序。

## 10. 推荐交接语句

如果这个文档要交给另一个对话或开发者，可以直接按以下口径交接：

“请按 `docs/calibration-tool-design.md` 和 `docs/calibration-tool-implementation-plan.md` 实现第一阶段标定工具。优先完成后端鉴权、流地址、PTZ、预置点和本地配置保存，再完成前端预览、冻结帧和 ROI 框选。实现过程中请同步维护日志与问题记录，留痕要求见文档说明。”
