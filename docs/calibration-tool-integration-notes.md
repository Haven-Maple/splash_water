# 标定工具联调记录

## 2026-07-17 标定工具 V2

- 标定页已重构为电脑端三栏工作台：左侧设备/预置点/云台，中间视频与双 ROI，右侧检查单、保存和导出；小于 `1024px` 明确提示使用电脑端。
- `roi` 只用于水花识别，显示为红色；`focusAnchorRoi` 只用于对焦判稳，显示为蓝色。两者始终同时可见，但一次只编辑一个。
- 云台移动或预置点切换会使冻结帧和两个 ROI 失效，必须重新判稳并抓图；已有草稿被切换、加载或新建前会要求确认。
- 当前生效文件仍为 `data/calibrations/<device>_<preset>.json`，识别程序无需更改读取方式。
- 新版保存会额外写入不可变历史 `data/calibration_history/<device>_<preset>/v000N/`，每个版本保存 `calibration.json`、原始快照和带 ROI/标识信息的标注快照。
- 首次更新旧标定时，旧文件会先归档为 `v0001`（`legacy=true`），更新后的记录从 `v0002` 开始；恢复历史版本会创建新版本，不会改写旧目录。
- 导出接口：当前配置、全部当前配置 ZIP、完整标定归档 ZIP。归档 ZIP 使用相对路径清单。
- 部署配置导出仅保留识别运行字段：设备、通道、目标、预置点、识别 ROI 与对焦锚点 ROI；不会携带快照、版本、审计或本机归档路径。
- 加载不同设备或通道的已有标定会立即关闭旧视频会话，冻结按钮保持禁用，直到为目标设备重新建立预览。
- 版本恢复要求原始和标注快照完整存在；缺任一快照的旧版本会明确拒绝恢复，避免创建缺证据的新版本。

## 2026-07-01

### 联调范围
- 本次完成本地接口骨架、前端接线和第一轮字段修复。
- 已根据真实报错结果修正 PTZ 与预置点请求体结构。

### 已接入接口
- `POST /open-api/api-base/auth/getAppAccessToken`
- `POST /open-api/api-iot/device/deviceOnline`
- `POST /open-api/api-iot/device/queryDeviceFlvLive`
- `POST /open-api/api-iot/device/getHlsLiveList`
- `POST /api-aiot/device/controlMovePTZ`
- `POST /open-api/api-aiot/device/queryPreset`
- `POST /open-api/api-aiot/device/configPreset`
- `POST /open-api/api-aiot/device/turnPreset`

### 已确认修正
- PTZ：
  - 原错误请求体：`xSpeed` / `ySpeed` / `zoomSpeed` / `durationMs`
  - 修正后请求体：`operation` / `duration` / `horizontalSpeed` / `verticalSpeed`
  - 原错误 endpoint：`/open-api/api-aiot/device/controlMovePTZ`
  - 修正后 endpoint：`/api-aiot/device/controlMovePTZ`
- 预置点：
  - 原错误请求体：`presetId` / `presetName`
  - 修正后请求体：`index` / `name`
  - 前后端统一内部命名：`presetIndex` / `presetName`
- 页面：
  - 增加操作日志区
  - 增加 `GET /api/debug/recent-logs`
  - 标定保存前补齐前端校验
- 标定存储：
  - `snapshotPath` 改为相对 `data_root` 的 artifact 路径
  - `snapshotUrl` 与 `/artifacts` 静态挂载根保持一致
  - `data_root` 指向项目外时不再因路径相对化失败触发 500
- token 缓存：
  - 过期时间统一按 UTC aware datetime 处理
  - 避免二次请求时出现 naive/aware datetime 比较异常
- PTZ 验证状态：
  - `ptz_operation_map` 支持本地配置覆盖
  - `ptz_verified_map` 支持记录已真机确认的“动作 -> operation”映射
  - `/api/ptz/move` 返回 `operationVerified` / `verifiedOperation` / `verifiedMap`
- 预览低延迟：
  - FLV 预览已启用低延迟 `flv.js` 配置
  - 页面会记录 `loadedmetadata` / `canplay` / `error`
  - PTZ / 预置点跳转后会进入 `ptzSettleMs` 稳定等待，再允许抓图
  - 抓图日志增加前端请求时间和实际截图时间
  - 播放器日志回调已做稳定引用处理，避免日志刷新触发播放器销毁重建
  - 播放就绪判定已从 `canplay` 收紧到 `playing`
  - 页面补充 `waiting` / `stalled` / `play failed` 日志
  - FLV 参数已从极限低延迟档回调到更稳的折中档
  - `waiting` / `stalled` 时会自动 seek 到最新 `live edge` 并重试 `play()`
  - 若 1.2 秒内仍未恢复，会执行 player soft reload
  - 页面新增 `Reconnect Preview` 按钮，移除 live 原生进度条
- 稳定门控：
  - 抓图门控已从固定倒计时改为 5 阶段：`commandAccepted` -> `mechanicalSettling` -> `streamCatchingUp` -> `visualStabilizing` -> `readyForCapture`
  - PTZ move 使用 `command.duration + ptzExtraSettleMs` 作为机械稳定阶段
  - preset turn 使用 `presetTurnSettleMs` 作为机械稳定阶段
  - 两者后续统一进入 `streamCatchupMs` 和视觉判稳
  - 视觉判稳使用低分辨率帧差，连续多次低于阈值才放行
  - 播放器一旦进入 `waiting/stalled/loading`，门控会退回到 `streamCatchingUp`
  - 新稳定参数已随 calibration JSON 一并保存，供第二阶段程序复用
  - 当前版本不再单独保存 `stabilityRoi`
  - 当前视觉判稳统一按整幅预览画面采样，旧 JSON 中残留的 `stabilityRoi` 会被忽略
  - 新增 `rawMotionScore` / `smoothedMotionScore`、grace band 和窗口式迟滞
  - 播放器离开 `ready` 后先进入 debounce pending，短暂恢复不重置整段流程
  - 只有 debounce 超时或 player reload 才真正回退到 `streamCatchingUp`

### 已观察到的真实错误
- PTZ 旧请求体曾返回：
  - `code: IDV10001`
  - `msg: Invalid parameter, please modify parameter`
- 预置点旧请求体曾返回：
  - `code: IDV10068`
  - `msg: Invalid input parameters`

### 当前适配策略
- 鉴权：
  - 复用现有 `request_sign_utils.py`
  - 增加 token 缓存和 401 后单次刷新重试
- 流地址：
  - 对返回体做递归 URL 提取
  - 优先返回匹配 `flv` / `hls` 关键字的地址
- 预置点：
  - 优先解析 `index` / `name`
  - 兼容旧字段 `presetId` / `presetName`
- 标定配置：
  - 新结构使用 `presetIndex`
  - 读取旧文件时兼容 `presetId`
- 标定截图：
  - 新结构使用 `snapshots/...` 作为 `snapshotPath`
  - 读取旧 `data/snapshots/...` 记录时自动归一化
- PTZ：
  - 当前只确认到 endpoint、字段结构和至少一条 `left -> operation=3` 成功日志
  - 其余方向/缩放需继续真机确认后写入 `ptz_verified_map`
- 预览：
  - 当前优化目标是“控制反馈更顺、抓图时机更可靠”
  - 不把 PTZ 成功与否绑定到网页画面是否立刻变化
  - 当前优先级高于第二阶段识别开发，先确保抓图放行时机准确

### 待联调事项
- 确认 PTZ 每个方向与缩放对应的真实 `operation` 编码。
- 确认 `queryPreset` 返回字段是否稳定包含 `index` / `name`。
- 确认浏览器端 FLV/HLS 播放的跨域与证书兼容性。
- 确认新的 `snapshotUrl` 在前端保存后能直接访问到静态截图。
- 对比本轮低延迟参数前后，确认 FLV 网页回显拖尾是否有肉眼可见下降。
- 验收 5 阶段门控是否完整出现，且 `Freeze Current Frame` 只在 `readyForCapture` 时放行。
## 2026-07-02 supplemental integration notes

- During live preview recovery, `Preview auto recover: player reload` must be treated as a real gate fallback event. The UI now restarts the full `streamCatchingUp` timer instead of only changing the visible phase.
- `streamCatchingUp` completion now reads the latest playback state from a ref. This avoids a field bug where playback had already recovered but the timer callback still saw an older `loading` state and never advanced to `visualStabilizing`.
- Frontend status copy in `CalibrationPage.tsx` was rebuilt to remove corrupted text, so onsite operators can distinguish `命令已发送` / `机械稳定中` / `直播追帧中` / `视觉判稳中` / `可抓图`.
- Backend direct API callers are now guarded by schema validation: `visualStableGraceThreshold` must be greater than or equal to `visualStableThreshold`.
## 2026-07-04 phase-2 step 1 integration notes

- Phase-2 currently enters through CLI, not frontend:
  - `python -m inspector.run_once --config data/calibrations/<file>.json --preset <index>`
- The new CLI reuses phase-1 calibration JSON as the only point config source in step 1.
- `CalibrationStorageService.load_path(...)` is now the shared normalization entry, so older calibration JSON can still be consumed with the same compatibility rules as the phase-1 backend.
- Step-1 result is already normalized into a stable shape:
  - `executionResult`
  - `visualState`
  - `scoreSummary`
  - `evidencePaths`
  - `timing`
  - `algorithmVersion`
- Current behavior:
  - if `--preset` mismatches calibration `presetIndex`, the command exits with structured `detect_error`
  - if Dahua config is missing, the command exits with structured `detect_error`
  - if preset turn fails at vendor side, the command exits with structured `preset_failed`
  - only after later milestones will `visualState` become non-null
- Runtime note:
  - for real preset execution, use a Python environment that has `backend/requirements.txt` installed
  - Codex bundled Python was sufficient for compile validation, but not for vendor-runtime dependency execution
## 2026-07-04 phase-2 step 2 integration notes

- `run_once` now reaches the second milestone:
  - `turnPreset`
  - wait global settle
  - fetch FLV
  - sample fixed-spec short sequence
  - return online result
  - persist replay materials in background
- Current sampling source is FLV only. HLS fallback is still intentionally out of scope for v1.
- Replay artifact format for this milestone:
  - `sequence.npz`
  - `metadata.json`
- The command returns replay paths immediately through:
  - `evidencePaths.replaySequencePath`
  - `evidencePaths.replayMetadataPath`
  This means the caller gets the final intended path before the async save fully finishes.
- Sampling-related acceptance signals are now exposed directly in the online result:
  - `timing.sampleMs`
  - `scoreSummary.sampledFrameCount`
  - `scoreSummary.targetFrameCount`
  - `scoreSummary.actualSampleFps`
  - `scoreSummary.actualSampleDurationMs`
- Failure split:
  - FLV open / stream runtime failures -> `stream_failed`
  - frame count not enough within deadline -> `insufficient_frames`
## 2026-07-04 phase-2 step 2 tail-fix integration notes

- Replay persistence is now lifecycle-safe for CLI usage:
  - `run_once` dispatches a detached child process
  - the parent process returns without waiting for final replay material writes
- Current detached handoff is file-based, not shared-memory-based:
  - parent writes lightweight `replay-handoff.json`
  - parent writes temporary raw frame/timestamp files
  - child converts those raw artifacts into final replay materials
  - parent no longer writes a second compressed replay handoff archive
- New replay contract for callers:
  - `evidencePaths.replaySequencePath` and `evidencePaths.replayMetadataPath` are destination paths
  - `replaySave.status` tells whether those files are `disabled`, `pending`, `ready`, or `failed`
  - `replaySave.statusPath` points to `replay-status.json`, which can be polled by the caller
- Current ready-state rule:
  - only when `replaySave.status == "ready"` or `replay-status.json` says `ready` should callers assume replay files are readable
- Worker failure rule:
  - if detached save fails, `replay-status.json` is flipped to `failed`
  - online result is not retroactively changed, but the failure becomes explicit instead of hidden in logs only
## 2026-07-04 phase-2 step 3 integration notes

- `run_once` now has a third-stage internal pipeline after sampling:
  - full-frame alignment
  - ROI frame features
  - weighted frame scoring
- Current result contract additions:
  - `scoreSummary.framePassCount`
  - `scoreSummary.framePassRatio`
  - `scoreSummary.alignmentApplied`
  - `scoreSummary.globalMotionExceeded`
  - `scoreSummary.overflowFrameCount`
  - `scoreSummary.meanGlobalShiftX`
  - `scoreSummary.meanGlobalShiftY`
  - `scoreSummary.maxGlobalShiftMagnitude`
  - `scoreSummary.maxAppliedShiftMagnitude`
  - `scoreSummary.preAlignmentRoiMotionMean`
  - `scoreSummary.postAlignmentRoiMotionMean`
  - `scoreSummary.localMotionMean` / `localMotionMax`
  - `scoreSummary.dynamicAreaScore`
  - `scoreSummary.dynamicAreaMean` / `dynamicAreaMax`
  - `scoreSummary.highlightMotionMean` / `highlightMotionMax`
  - `scoreSummary.weightedFrameScoreMean` / `weightedFrameScoreMax`
- Debug artifact paths are now exposed in:
  - `evidencePaths.representativeFramePath`
  - `evidencePaths.debugImagePath`
- Current interpretation:
  - frame-level pass summaries are now available for step-3 acceptance
  - `scoreSummary.dynamicAreaRatio` now means raw ROI dynamic-area ratio again
  - `scoreSummary.dynamicAreaScore` means the normalized scoring component
  - `meanGlobalShiftX/Y` and `maxGlobalShiftMagnitude` now reflect raw estimated whole-frame motion, not silently zeroed overflow frames
  - `visualState` is still intentionally `null` until step 4 temporal voting is added
- Replay-evidence timing note:
  - representative/debug images are no longer written synchronously on the online return path
  - callers should treat them like other replay artifacts and rely on `replaySave.status` / `replaySave.statusPath` before assuming they are readable
## 2026-07-04 phase-2 step 4 integration notes

- `run_once` now resolves a final `visualState` on successful runs:
  - `has_splash`
  - `no_splash`
  - `undetermined`
- The sequence-vote layer consumes the existing step-3 outputs instead of inventing a parallel scoring path:
  - `framePassCount`
  - `framePassRatio`
  - `overflowFrameCount`
  - `globalMotionExceeded`
  - `preAlignmentRoiMotionMean`
  - `postAlignmentRoiMotionMean`
- New score summary diagnostics now expose why a final decision landed where it did:
  - `overflowFrameRatio`
  - `motionReductionRatio`
  - `reliabilityGateTriggered`
  - `temporalVoteReason`
- Current vote semantics:
  - above `sequenceVoteThreshold` -> `has_splash`
  - below `1 - sequenceVoteThreshold` -> `no_splash`
  - middle band -> `undetermined`
  - reliability gates can also force `undetermined`
- New async evidence contract:
  - `evidencePaths.recognitionConfigSnapshotPath` points to the frozen effective-config snapshot path
  - replay metadata includes the effective-config summary used by this run
  - replay worker no longer depends on the current mutable local config when generating async artifacts
## 2026-07-05 phase-1 calibration integration notes

- The phase-1 stability gate remains active, but it no longer depends on a separate stored `stabilityRoi`.
- Frontend behavior:
  - only one ROI is now calibrated: the detection ROI
  - visual stability sampling always runs against the full preview frame
  - page logs now describe this as full-frame visual stability
- Backend behavior:
  - `CalibrationSaveRequest` no longer requires `stabilityRoi`
  - `CalibrationRecord` no longer exposes `stabilityRoi`
  - old stored JSON files may still contain `stabilityRoi`, but the reader ignores it
## 2026-07-05 phase-2 frame-gate integration notes

- Step-4 temporal voting remains unchanged in structure:
  - `framePassRatio` still feeds the final `has_splash` / `no_splash` / `undetermined` decision
  - this round only changes how a frame earns `framePass=true`
- The new frame-pass contract is now:
  - no sufficiently large central continuous bright component -> `framePass=false`
  - only after that hard gate passes does weighted scoring run
- New online summary fields now expose whether the result came from a real splash-like white body or from fragmented highlights:
  - `scoreSummary.largestBrightComponentRatio`
  - `scoreSummary.brightComponentCount`
  - `scoreSummary.fragmentationScore`
  - `scoreSummary.centerBrightCoverage`
  - `scoreSummary.upperHalfBrightRatio`
  - `scoreSummary.lowerHalfBrightRatio`
  - `scoreSummary.verticalSpreadRatio`
  - `scoreSummary.hardGatePassed`
  - `scoreSummary.hardGatePassRatio`
  - `scoreSummary.hardGatePassCount`
- Replay metadata `extra` now also records the same bright-mass diagnostics, so offline replay review can tell:
  - whether the sequence ever formed a central bright body
  - whether the ROI was mostly fragmented reflective highlights instead
- Default recognition config shifted toward geometric splash evidence:
  - lower weights for `localResidualMotion` and `dynamicAreaRatio`
  - higher weights for `largestBrightComponentRatio`, `centerBrightCoverage`, `verticalSpreadRatio`, and bright continuity
## 2026-07-05 phase-2 frame-gate follow-up notes

- The hard gate is now two-part, not structure-only:
  - structure: central, continuous, sufficiently large bright splash-like mass
  - dynamics: at least one dynamic evidence branch above minimum
- New hard-gate config knobs:
  - `hardGateMinLocalMotion`
  - `hardGateMinDynamicAreaRatio`
  - `hardGateMinHighlightMotion`
- Sequence summary semantics are now explicit:
  - `scoreSummary.anyHardGatePassed` means any frame passed the hard gate
  - `scoreSummary.hardGatePassed` means `hardGatePassRatio >= sequenceVoteThreshold`
  - `scoreSummary.hardGatePassRatio` remains the least ambiguous sequence-level replay signal
- Config behavior changed:
  - hard-gate thresholds are now independent from feature normalization scales
  - this allows stricter gating while still letting weighted-score components saturate earlier

## 2026-07-05 phase-2 night ir integration notes

- `run_once` now has an explicit scene-mode contract:
  - `sceneMode = day_visible`
  - `sceneMode = night_ir`
- Config consumption rule:
  - `recognition_v1` top-level remains the shared baseline
  - `recognition_v1.dayVisible` may override daytime-only values
  - `recognition_v1.nightIr` may override night-only values
  - the resolved active mode is written back into the effective config snapshot and the online result
- Current runtime separation:
  - daytime scoring still uses the accepted center-bright-mass + dynamic-evidence path
  - night scoring reuses the same pipeline skeleton:
    - sampling
    - alignment
    - ROI features
    - frame gate
    - temporal vote
  - but switches the frame gate emphasis to:
    - dominant bright-mass geometry
    - `gapFillRatio`
    - temporal dominant-body variance
- New night diagnostics visible online and in replay metadata:
  - `scoreSummary.sceneMode`
  - `scoreSummary.gapFillRatio`
  - `scoreSummary.temporalAreaVariance`
  - `scoreSummary.temporalShapeVariance`
  - replay `metadata.json -> extra.sceneMode`
  - replay `metadata.json -> extra.gapFillRatio`
  - replay `metadata.json -> extra.temporalAreaVariance`
  - replay `metadata.json -> extra.temporalShapeVariance`
- Current night gate interpretation:
  - static bright blade-like IR bars should fail because they keep dark gaps and lack temporal dominant-body change
  - a continuous bright splash body can pass after structure gate + dynamic branch both succeed
- Local replay verification used this round:
  - daytime offline regression remained:
    - `21` old `no_splash` replays still resolve to `no_splash`
    - `20` old `has_splash` replays still resolve to `has_splash`
  - real dark replays at local about `19:46-19:47` stayed `night_ir -> no_splash`
  - synthetic night probes confirmed:
    - `static_bars -> no_splash`
    - `dynamic_blob -> has_splash`
- Current acceptance boundary:
  - night negative path has initial replay evidence
  - night positive path still needs a small real clear `has_splash` set before the `nightIr` override block should be treated as tuned

## 2026-07-06 night ir relative-threshold integration notes

- Night extraction no longer assumes IR splash must hit a fixed white threshold.
- Current `night_ir` runtime behavior:
  - day path still uses the existing absolute bright threshold
  - night path now computes a ROI-relative bright threshold from the replay ROI itself
  - a light ROI blur is applied before the night threshold so diffuse IR splash bodies are not dropped for being low-contrast
- New night config knobs now available in both config files:
  - `nightBrightQuantile`
  - `nightBrightStdMultiplier`
  - `nightBrightMinThreshold`
  - `nightBrightBlurRadius`
- New diagnostics now exposed online and in replay metadata:
  - `scoreSummary.brightThresholdMean`
  - `scoreSummary.roiBrightnessQ99Mean`
  - `scoreSummary.roiBrightnessMaxMean`
- Current replay interpretation after the fix:
  - low-structure IR clips:
    - still look like separated bright bars
    - keep `centerBrightCoverage = 0`
    - keep lower `largestBrightComponentRatio`
    - remain `no_splash`
  - high-structure IR clips:
    - now recover into a large central diffuse bright body
    - show much higher `largestBrightComponentRatio`
    - show much higher `centerBrightCoverage`
    - show much higher `verticalSpreadRatio`
    - show much higher `gapFillRatio`
- Current night hard-gate tuning strategy:
  - stronger structure thresholds
  - much weaker dynamic minimums matched to real IR replay magnitudes
  - no change to sequence vote threshold in this round
- Current acceptance snapshot on the latest real night replay batch:
  - low-structure cluster: `5 / 5 no_splash`
  - high-structure cluster: `3 / 5 has_splash`
  - remaining boundary outputs: `1 undetermined`, `1 no_splash`

## 2026-07-06 night ir second-round gap-fill notes

- This round followed the one-knob tuning rule from [docs/night-ir-second-round-tuning-checklist.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/night-ir-second-round-tuning-checklist.md).
- Only one runtime parameter was changed:
  - `recognition_v1.nightIr.hardGateMinGapFillRatio`
- Tested replay-only progression:
  - `0.90`:
    - positives `10 has / 2 undetermined / 3 no`
    - negatives `5 no`
  - `0.88`:
    - positives `11 has / 3 undetermined / 1 no`
    - negatives `5 no`
  - `0.86`:
    - positives `12 has / 2 undetermined / 1 no`
    - negatives `5 no`
- The selected value is now:
  - `hardGateMinGapFillRatio = 0.86`
- Operational interpretation:
  - this confirms the current night recall bottleneck was indeed still sitting in the frame-level structure gate
  - the negative cluster still remains well below the new threshold at about `0.768 - 0.772`
  - the remaining positive miss is no longer just a pure gap-fill issue and should not automatically trigger vote-threshold tuning next

## 2026-07-06 night ir third-round center-coverage notes

- This round again followed the single-knob rule from [docs/night-ir-third-round-tuning-checklist.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/night-ir-third-round-tuning-checklist.md).
- Only one runtime parameter was changed:
  - `recognition_v1.nightIr.hardGateMinCenterBrightCoverage`
- Tested progression:
  - `0.48`
  - `0.46`
  - `0.44`
- Chosen value:
  - `hardGateMinCenterBrightCoverage = 0.46`
- Current replay interpretation:
  - `gapFillRatio` remains locked at `0.86`
  - the next real bottleneck was samples whose center coverage sits just under the old `0.50` line
  - dropping to `0.46` recovers a meaningful borderline sample without changing the negative reference outcome
  - dropping further to `0.44` did not add observable benefit in this round
- Safety check:
  - the fixed negative reference set still remains `5 / 5 no_splash`

## 2026-07-06 night ir baseline freeze notes

- Current night IR manual-mode baseline is now frozen in [docs/night-ir-baseline.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/night-ir-baseline.md).
- The baseline to preserve before auto scene switching is:
  - manual `recognition_v1.sceneMode = night_ir`
  - algorithm version `phase-2-v1-night-relative-threshold`
  - accepted ROI with night drift margin:
    - `306,296,159,99`
  - accepted sample result:
    - positive `40 / 40 has_splash`
    - negative `10 / 10 no_splash`
- Integration takeaway:
  - night runtime quality depends not only on thresholds
  - calibration guidance must also remind operators not to draw the night ROI too tightly
- Next integration target:
  - add automatic day/night scene recognition and switching
  - keep manual override available for field fallback and debugging

## 2026-07-07 auto scene switch design notes

- Auto-switch planning is now documented in:
  - [docs/auto-scene-mode-switch-design.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/auto-scene-mode-switch-design.md)
  - [docs/auto-scene-mode-switch-checklist.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/auto-scene-mode-switch-checklist.md)
- Integration direction:
  - add `sceneMode = auto`
  - classify scene before recognition using global frame statistics
  - do not rely only on ROI or only on a black-white check
  - preserve `day_visible` and `night_ir` as permanent manual overrides
- Safety rule:
  - unclear scene should not be forced into one mode without trace
  - ambiguous classification should use dual-path fallback and explicit diagnostics
- Required future integration outputs:
  - `requestedSceneMode`
  - `effectiveSceneMode`
  - `sceneModeConfidence`
  - `sceneModeReason`
  - fallback usage marker

## 2026-07-07 auto scene switch implementation notes

- `run_once` now supports three requested scene modes:
  - `auto`
  - `day_visible`
  - `night_ir`
- Manual override behavior is unchanged:
  - if requested mode is `day_visible` or `night_ir`, the runtime skips scene classification and executes that recognition branch directly
- Auto runtime chain is now:
  - turn preset
  - wait settle
  - sample short FLV sequence
  - classify scene mode from early frames
  - choose effective mode or trigger dual-path fallback
  - run the existing alignment + feature + scoring + temporal-vote body
- Scene classifier implementation details:
  - module: `inspector/scene_mode_resolver.py`
  - input:
    - first `sceneAutoFrameCount` frames
    - center crop controlled by `sceneAutoCenterCropRatio`
  - diagnostics:
    - `colorfulnessMean`
    - `saturationP90`
    - `channelDeltaMean`
    - `channelCorrelation`
    - `brightnessMean`
    - `brightnessStd`
- New result contract fields:
  - `requestedSceneMode`
  - `effectiveSceneMode`
  - `sceneModeConfidence`
  - `sceneModeReason`
  - `sceneModeFallbackUsed`
  - `sceneModeDiagnostics`
  - `dayVisibleVisualState`
  - `nightIrVisualState`
  - `fallbackResolution`
- Current fallback rule:
  - if classifier output is `ambiguous` and `sceneAutoUseDualPathFallback = true`
  - the same sampled sequence is evaluated once with `day_visible` config and once with `night_ir` config
  - if both final `visualState` values agree, that result is accepted
  - if they conflict, final `visualState = undetermined`
- Replay metadata now also records the scene-switch decision summary, so offline review can tell:
  - what mode was requested
  - what mode was actually used
  - whether fallback ran
  - whether day/night paths agreed or conflicted
- Current local rollout default in `backend/local_config.json` is now:
  - `recognition_v1.sceneMode = auto`
  - frozen `dayVisible` / `nightIr` override blocks remain intact
- Current validation completed in Codex:
  - bundled Python `compileall inspector backend/app`
  - synthetic colored vs grayscale resolver smoke check
- Current validation still pending outside Codex:
  - real frozen baseline replay under `auto`
  - field confirmation that clear scenes rarely fall into fallback

## 2026-07-07 auto scene switch follow-up notes

- Auto mode remains implemented, but it is no longer the default publish/runtime mode before replay acceptance:
  - `backend/local_config.example.json` is back to `sceneMode = day_visible`
  - current machine `backend/local_config.json` is back to manual `sceneMode = night_ir`
- Recognition version traceability is now mode-correct:
  - shared daytime path uses `phase-2-v1-step4-center-gate`
  - `nightIr` override explicitly uses `phase-2-v1-night-relative-threshold`
  - this applies to:
    - `RecognitionRunResult.algorithmVersion`
    - replay metadata
    - effective config snapshots
- `RunOnceService` constructor now supports preserving raw scene profiles during injected-config usage:
  - new optional parameter: `raw_config`
  - if omitted, the service now prefers the original local raw config over flattening the effective snapshot
  - this keeps `dayVisible` / `nightIr` overrides available for auto fallback in script and test entry points

## 2026-07-07 single-point stage completion notes

- Single-point recognition stage is now frozen in [docs/single-point-recognition-completion.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/single-point-recognition-completion.md).
- The preserved acceptance sets currently used as integration anchors are:
  - daytime:
    - [run_once_result_has](C:/Users/Maple_Rain/Documents/Items/splash_water/run_once_result_has/)
    - [run_once_result_no](C:/Users/Maple_Rain/Documents/Items/splash_water/run_once_result_no/)
    - total `20 / 20` correct
  - night IR:
    - [run_once_result_night_has](C:/Users/Maple_Rain/Documents/Items/splash_water/run_once_result_night_has/)
    - [run_once_result_night_no](C:/Users/Maple_Rain/Documents/Items/splash_water/run_once_result_night_no/)
    - total `50 / 50` correct
- Integration conclusion:
  - current recognition body is no longer the next-stage bottleneck
  - next integration target should move from single-point correctness to pseudo multi-point orchestration and stability

## 2026-07-08 pseudo multi-point design notes

- Pseudo multi-point testing is now documented in:
  - [docs/pseudo-multi-point-test-design.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/pseudo-multi-point-test-design.md)
  - [docs/pseudo-multi-point-test-checklist.md](C:/Users/Maple_Rain/Documents/Items/splash_water/docs/pseudo-multi-point-test-checklist.md)
- Integration direction:
  - keep the current single-point recognition body unchanged
  - wrap it with a scheduler-style repeat loop
  - use preset 2 only as a transition preset
  - call `RunOnceService` directly in-process
- Output and replay policy:
  - each run writes a dedicated directory
  - summary plus per-round JSON files are both required
  - replay async save remains non-blocking
  - interrupted runs still need partial summary output
- Acceptance direction:
  - strict first-pass target is `10 / 10` expected recognition results after repeated preset departure and return

## 2026-07-08 pseudo multi-point implementation notes

- New CLI entry is now available:
  - `python -m inspector.pseudo_multi_point_test --config <preset1 calibration> --transition-preset <preset2> --expected-visual-state <state>`
- Current integration contract:
  - preset 1 comes only from the existing calibration JSON
  - preset 2 is transition-only and needs only a preset index
  - the tool reuses `RunOnceService` directly and does not spawn repeated `run_once` subprocesses
- Current round success rule is explicit:
  - transition preset accepted
  - recognition `executionResult = success`
  - recognition `visualState == expectedVisualState`
- Current trace outputs per run:
  - one dedicated run directory
  - one `summary.json`
  - one round JSON per executed round
- Current interruption behavior:
  - completed round JSON files are written immediately after each round
  - `summary.json` is still written on interrupt or abort
- Current timeout behavior:
  - timeout fields are implemented at scheduler level
  - they currently act as soft overrun markers, not hard cancellation
  - this keeps the inner recognition chain unchanged for the first pseudo multi-point validation pass
- Current scene-mode behavior:
  - default inherits the current `recognition_v1.sceneMode`
  - optional `--scene-mode` override can force `auto`, `day_visible`, or `night_ir`
- Suggested first field command:
  - `python -m inspector.pseudo_multi_point_test --config data/calibrations/AB00A7DPAJ00124_1.json --transition-preset 2 --rounds 10 --expected-visual-state has_splash`

## 2026-07-08 phase-1 preview background-power notes

- Calibration preview now distinguishes browser background power-saving pause from real stream/player failure.
- Current behavior:
  - if the page is hidden, preview `play()` retries are deferred
  - `waiting/stalled` no longer triggers aggressive auto-recover loops while hidden
  - once the page becomes visible again, the preview performs a live-edge recovery attempt
- Practical integration effect:
  - background tab/window suspension should no longer flood logs with repeated `AbortError` recovery failures
  - live preview is less likely to remain silently far behind real time after a hidden-tab interval

## 2026-07-08 pseudo multi-point transition-settle notes

- The pseudo multi-point scheduler now has one extra transition-only wait knob:
  - `--transition-settle-ms`
- Integration intent:
  - this wait belongs only to the pseudo multi-point departure / return schedule
  - it is not a recognition-main-chain settle change
  - it is not a day/night parameter change
- Current round chain is now:
  - turn preset 2
  - if accepted, wait `transitionSettleMs`
  - then call `RunOnceService.run(...)` for preset 1
- New trace fields per round:
  - `transitionSettleMsConfigured`
  - `transitionSettleWaitMsActual`
- New scheduling snapshot field in `summary.json`:
  - `transitionSettleMs`
- Operational reading:
  - if `has_splash` pseudo multi-point results improve after increasing this wait, the main issue is likely that preset 2 had not finished its transition before the return to preset 1 started
  - if improvement is still weak, the next priority should stay on the return-to-preset-1 settle side, not on recognition thresholds

## 2026-07-08 phase-1 preview stream-refresh escalation notes

- Preview recovery now has two levels:
  - light recovery:
    - for ordinary `waiting` / `stalled`
    - still uses live-edge seek, replay, and soft reload on the current player
  - heavy recovery:
    - for FLV/network-layer failures where the current stream URL is no longer trustworthy
    - now re-calls `getPreferredStream(...)`, updates the stream state, and rebuilds the player
- Current integration split:
  - player hook only decides when the current URL is likely bad
  - page layer owns the actual stream-address refresh request
- Current heavy-refresh trigger examples:
  - `FLV player error: NetworkError / Exception`
  - `Failed to fetch`
  - `ERR_EMPTY_RESPONSE`
  - `IOException`
- Current cooldown rule:
  - automatic heavy refresh is rate-limited in the page layer
  - short upstream jitter should not spam the backend with repeated stream-address fetches
- Current hidden-page rule:
  - if the document is hidden, automatic heavy refresh is deferred
  - once the page becomes visible again, the deferred refresh can be requested
- Current manual fallback:
  - `Reconnect Preview` now also re-calls `getPreferredStream(...)`
  - it no longer only restarts the old player against the old URL

## 2026-07-08 phase-1 preview stability fallback notes

- Preview tuning has now been pulled back from the earlier aggressive ultra-low-latency profile to a stable low-latency profile.
- Current integration intent:
  - reduce `waiting`
  - reduce visible multi-second freeze intervals
  - keep latency low enough for calibration work, but not at the cost of frequent playback instability
- Current FLV runtime profile:
  - stash buffer restored
  - larger initial stash
  - wider backward cleanup window
  - audio timestamp-gap fix restored
- Current recovery behavior:
  - ordinary `waiting` / `stalled` still stay on light recovery
  - repeated short-interval `waiting` / `stalled` no longer keep re-triggering the full recover sequence
  - this is meant to avoid recovery storms caused by the recovery loop itself
- Current heavy-refresh boundary is unchanged:
  - only explicit broken-link/network signals should escalate to stream-address refresh
  - ordinary playback jitter should not call `getPreferredStream(...)`

## 2026-07-09 calibration payload slimming notes

- Phase-1 calibration persistence and phase-1 runtime knobs are now intentionally separated.
- Calibration JSON responsibility:
  - describe which device / channel / preset / ROI to use
  - keep optional target metadata and saved snapshot references
- Global runtime-config responsibility:
  - phase-1 page gate timing and visual-stability tuning come from backend `calibration_tool`
  - phase-2 preset settle and stream catch-up continue to come from backend `recognition_v1`
- New frontend/backend handshake:
  - page loads `GET /api/calibration/runtime-config`
  - preview gate uses the returned global values
  - save payload no longer sends runtime knobs for persistence
  - PTZ / preset / capture controls stay disabled until that runtime config has actually loaded
- Backward compatibility rule:
  - old calibration JSON files may still contain legacy runtime fields
  - storage normalization ignores those fields on read
  - new calibration saves do not write them back
- Operational effect:
  - one tuning change in local config now updates all phase-1 capture-gate sessions
  - phase-2 `run_once` remains driven by recognition global config instead of per-calibration JSON copies

## 2026-07-09 pseudo multi-point composition-timing notes

- Current intermittent pseudo multi-point failures should be diagnosed as scheduling/timing first, not algorithm-threshold drift first.
- Current preferred repair order:
  - first raise transition-preset settle wait
  - then, only if needed, raise `recognition_v1.presetTurnSettleMs`
  - finally, only if preview is already centered but replay material still looks older than expected, raise `recognition_v1.streamCatchupMs`
- Current default scheduler-level transition wait is now:
  - `transitionSettleMs = 1800`
- Purpose:
  - give preset-2 departure and return-to-preset-1 recognition more physical convergence margin before sampling starts
- Current round JSON / stderr progress now surface these timing checkpoints directly:
  - `transitionPreset.elapsedMs`
  - `transitionSettleMsConfigured`
  - `transitionSettleWaitMsActual`
  - `recognitionPresetTurnMs`
  - `recognitionSettleWaitMs`
  - `recognitionSampleMs`
  - `recognitionDetectMs`
- Evidence-path reading rule:
  - top-level `representativeFramePath` / `debugImagePath` in `round_*.json` are still target paths, not proof of immediate readability
  - always read them together with:
    - `replaySaveStatus`
    - `replaySaveStatusPath`
    - or the nested `recognitionResult.replaySave`
  - only `replaySaveStatus = ready` should be treated as evidence that replay materials are actually available on disk
- Current failure-first review rule:
  - if `effectiveSceneMode` is normal
  - and `overflowFrameRatio = 0`
  - and `globalMotionExceeded = false`
  - and `visualState = no_splash`
  - then first inspect:
    - representative frame
    - motion/debug image
    - corresponding `round_*.json`
  - if the water-splash subject is visibly shifted, clipped, or compositionally incomplete, treat it as early sampling / composition timing first, not threshold tuning first

## 2026-07-09 visual readiness and static-bright suppression notes

- Fixed settle waits remain only coarse buffering.
- Recognition is now intended to start only after:
  - preset turn
  - fixed settle wait
  - visual-readiness gate pass
- Current visual-readiness gate checks:
  - full-image center-crop sharpness
  - consecutive clear frames
  - non-abnormal frame-to-frame instability
- Current failure meaning:
  - if readiness does not pass in time, recognition should not continue as normal `has_splash` / `no_splash`
  - blurry / blurry-and-unstable readiness failures should surface as `visual_blurry_before_detection`
  - timeout-only readiness failures should stay in a readiness-failure path such as `visual_not_ready_timeout` with `visualState = undetermined`
- Current operator/debug reading:
  - `timing.visualReadinessMs` explains time spent before formal sampling
  - `visualReadinessPassed` / `visualReadinessReason` explain whether failure happened before detection or inside detection
  - visual-readiness sharpness/stability now prefer the target ROI neighborhood rather than the generic full-frame center crop,
    which is important when nearby floats, foam devices, or rail edges remain sharp while the actual splash target area is still blurry
  - visual readiness now also enforces a minimum elapsed wait plus a minimum sustained ready window,
    so phase-2 cannot promote a stream to "ready" after only a few immediately buffered startup frames
  - when reviewing pseudo-multi-point failures, the round/replay outputs now expose:
    - readiness trend/improvement diagnostics
    - gate-stage pass/fail booleans
    - ROI evidence frames for readiness start, readiness pass boundary, and formal sample start
  - pseudo multi-point `round_*.json` now surfaces those readiness fields directly
  - pseudo multi-point `summary.json` now also surfaces:
    - `recognitionExecutionBreakdown`
    - `visualReadinessFailureReasons`
    - `visualReadinessFailedRounds`
    - `visualNotReadyTimeoutRounds`
    - `staticBrightInterferenceSuppressedRounds`
- Static bright interference is now treated as a formal risk class:
  - nearby static white foam devices
  - non-target white floating bodies
  - adjacent white bright structures
- Current suppression strategy is intentionally conservative:
  - do not add manual ignore masks first
  - do not broadly retune day/night thresholds first
  - instead suppress `has_splash` only when the main bright body is structurally strong but temporally too static

## 2026-07-13 focus-anchor ROI split

- Calibration now stores two independent rectangles per target:
  - `roi`: splash detection ROI
  - `focusAnchorRoi`: readiness / focus-stability ROI
- Frontend behavior:
  - both ROI boxes are rendered together on the frozen frame
  - operators can switch edit mode between `识别 ROI` and `对焦 ROI`
  - redraw / clear actions are separated for the two boxes
  - save validation now blocks independently on missing detection ROI or missing focus ROI
- Runtime behavior:
  - visual readiness and sample-quality focus checks use `focusAnchorRoi`
  - splash feature extraction, scoring, and temporal voting still use `roi`
- Compatibility:
  - old calibration JSON files may omit `focusAnchorRoi`
  - runtime temporarily falls back to `roi`, but result metadata records the fallback explicitly through
    - `focusAnchorRoiSource`
    - `focusAnchorRoiFallbackUsed`
