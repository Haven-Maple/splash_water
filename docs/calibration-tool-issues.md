# 标定工具问题清单

## PTZ operation 编码表未完全确认

- 现象：修复文档只给出了 `operation: "10"` 示例，没有完整方向与缩放编码表。
- 复现步骤：使用真实设备分别测试上下左右、斜向和缩放。
- 原因判断：厂商文档样例不足，当前只能先按约定映射实现。
- 解决方案：
  - 已将映射集中在 `backend/app/services/dahua_ptz_service.py`
  - 支持通过 `backend/local_config.json -> ptz_operation_map` 覆盖
  - 支持通过 `ptz_verified_map` 标记已真机确认的“动作 -> operation”映射
  - `/api/ptz/move` 与页面日志会显式返回 verified/unverified，且校验基于当前实际 operation，不再只看动作名
- 是否已关闭：否

## snapshotPath / snapshotUrl 与 data_root 解耦不完整

- 现象：旧实现把截图路径按 `workspace_root` 相对路径保存，静态挂载根却是 `data_root`，导致 URL 生成成 `/artifacts/data/snapshots/...`；当 `data_root` 在项目外时，`relative_to(workspace_root)` 直接抛错。
- 复现步骤：
  - 保存带截图的标定配置
  - 或把 `data_root` 配到项目目录外后调用 `/api/calibration/save`、`/api/calibration/list`
- 原因判断：存储层把“展示路径”和“静态资源根”混成了同一层相对路径语义。
- 解决方案：
  - `snapshotPath` 改为相对 `data_root` 保存
  - `snapshotUrl` 统一按 `/artifacts/{snapshotPath}` 生成
  - 兼容旧 `data/snapshots/...` 记录的归一化读取
  - 列表接口改为优先显示 `data_root` 相对路径，避免跨目录抛错
- 是否已关闭：是

## Token 过期时间 naive/aware 混用

- 现象：当 token 过期时间来自无时区 ISO 字符串时，第一次请求可能成功，后续命中缓存校验时会因 naive/aware 比较抛 `TypeError`。
- 复现步骤：
  - 厂商返回无时区 `expireTime` 或 `expiresAt`
  - 第二次调用任一依赖 token 缓存的接口
- 原因判断：`datetime.fromisoformat()` 对无时区字符串返回 naive datetime，而 `utc_now()` 是 aware datetime。
- 解决方案：
  - 统一将字符串时间解析为 UTC aware datetime
  - 时间戳时间也统一归一到 UTC
- 是否已关闭：是

## Dahua 返回结构不稳定

- 现象：不同接口可能把核心字段放在 `data`、`list`、`items`、`records` 等不同层级。
- 复现步骤：对接真实设备并分别调用在线状态、流地址、预置点查询接口。
- 原因判断：厂商 OpenAPI 不同产品线/版本返回结构可能不完全一致。
- 解决方案：后端服务层已采用宽松字段提取和递归 URL 搜索；联调时继续收敛结构。
- 是否已关闭：否

## FLV 预览可能无法直接播放

- 现象：浏览器端即使拿到 FLV 地址，也可能因跨域、证书、协议限制无法正常播放。
- 复现步骤：前端获取 FLV 预览流后在浏览器播放。
- 原因判断：浏览器端流媒体兼容性与厂商流地址有效期存在不确定性。
- 解决方案：已保留 HLS 降级路径；联调后根据真实情况决定是否增加代理转发。
- 是否已关闭：否

## 云端 FLV 预览仍有网页回显延迟

- 现象：PTZ 或预置点命令生效后，网页画面会有可感知拖尾，用户容易误以为命令没执行。
- 复现步骤：
  - 打开 FLV 预览
  - 连续点击 PTZ 或执行预置点跳转
  - 观察网页视频与设备实际动作回显
- 原因判断：瓶颈在云端 FLV 网页回显，不在控制链路。
- 解决方案：
  - 已开启 `flv.js` 低延迟配置，减少默认缓冲
  - 已把“命令发出”和“允许抓图”拆成两阶段，稳定等待复用 `ptzSettleMs`
  - 已补播放器加载事件和抓图时间戳日志，方便继续分析时序
- 补充修复：
  - 已避免播放器因为页面日志回调引用变化而反复销毁重建
  - 当前播放器重建只应发生在流地址或流类型真正变化时
  - 已将 FLV 参数从极限低延迟档回调到更稳的折中档，减少“首帧出来后卡住”的概率
  - 已增加 `playing` / `waiting` / `stalled` / `play failed` 日志，方便判断是未播放还是播放中断流
  - 已新增自动恢复逻辑：跳 live edge -> 补 play -> 必要时软重载 player
  - 已移除 live 预览原生进度条，避免用户依赖拖动进度条手工救活直播
- 是否已关闭：否

## 固定时间门控会过早放行抓图

- 现象：PTZ move 或预置点跳转后，页面只按固定时间倒计时进入“可抓图”，镜头仍在转动、直播还没追到最新位置时就可能提前放行。
- 复现步骤：
  - 点击 PTZ move 或 turn preset
  - 观察镜头仍明显运动时页面是否已显示“可抓图”
- 原因判断：旧逻辑只依赖 `ptzSettleMs`，没有拆分机械稳定、直播追帧和视觉判稳阶段。
- 解决方案：
  - 已拆分 5 阶段门控：`commandAccepted` / `mechanicalSettling` / `streamCatchingUp` / `visualStabilizing` / `readyForCapture`
  - 已新增机械稳定、直播追帧和视觉判稳参数，并持久化到 calibration JSON
  - 已新增前端视觉判稳 hook，持续采样 `video` 帧做低分辨率帧差判定
  - 只要播放器离开 `playing/ready`，门控会退回到 `streamCatchingUp`
- 是否已关闭：否

## 视觉判稳对室外自然动态过敏

- 现象：机械已经稳了，但仍长期卡在 `visualStabilizing`；或者播放器短暂 `waiting` 后整段回退，等待时间远超真实稳定时间。
- 复现步骤：
  - 在有风、水面反光、树影或远处自然动态的场景下做 PTZ / 预置点操作
  - 观察门控是否在镜头已停稳后仍迟迟不放行
- 原因判断：
  - 旧判稳按整帧灰度差，过于容易被环境动态触发
  - 播放器离开 `ready` 后立刻回退，对直播短抖动过敏
- 解决方案：
  - 当时曾引入 `stabilityRoi`，尝试只在稳定 ROI 内做判稳
  - 已加入轻微模糊、像素死区、EMA 平滑和 grace band
  - 已改为窗口式迟滞判定，不再“一次失败直接清零”
  - 已新增 `streamUnreadyDebounceMs`，短暂 `waiting/stalled` 先 pending，恢复后不重跑整段流程
- 是否已关闭：是
- 当前状态：
  - `stabilityRoi` 已在后续版本移除
  - 视觉判稳保留，但已统一回到全画面采样

## ROI 当前未支持编辑已有框

- 现象：用户重新框选会覆盖旧 ROI，不能拖动已有 ROI 的边或角。
- 复现步骤：完成一次框选后尝试微调。
- 原因判断：第一阶段只实现最小可用框选。
- 解决方案：后续迭代增加 ROI 拖拽、缩放柄和键盘微调。
- 是否已关闭：否
## Capture gate stale state after player reload / catch-up

- Symptom: after `Preview auto recover: player reload`, the page could show `streamCatchingUp` forever without reopening the gate, or remain stuck when playback became ready before the catch-up timer ended.
- Cause:
  - the page only changed `captureGatePhase` on player reload and did not restart the actual catch-up timer
  - the catch-up timer callback compared against a stale `playbackState` closure
- Fix:
  - player reload now calls the shared `beginStreamCatchup(...)` flow
  - catch-up completion now reads the latest playback state from a ref
- Closed: yes

## FLV low-latency config drift

- Symptom: `useStreamPlayer.ts` had drifted back to a buffered FLV config, which weakened the earlier low-latency optimization.
- Cause: `flv.js` options no longer matched the accepted tuning baseline.
- Fix:
  - `enableStashBuffer: false`
  - `stashInitialSize: 32`
  - `fixAudioTimestampGap: false`
- Closed: yes
## Phase-2 step 1 runtime dependency boundary

- Symptom: `python -m inspector.run_once` can fail before preset execution if the active Python runtime does not have backend dependencies such as `requests`.
- Cause:
  - phase-2 CLI reuses the existing Dahua preset service
  - the bundled validation runtime in Codex did not have `backend/requirements.txt` installed
- Mitigation already applied:
  - Dahua preset imports were moved to lazy-load inside `run_once_service`
  - config loading and preset mismatch validation can now run without importing network dependencies
- Remaining requirement:
  - real preset-turn execution still needs a Python environment with `backend/requirements.txt` installed
- Closed: no

## Focus-stability still coupled to splash ROI before focus-anchor split

- Symptom:
  - dynamic splash motion inside the main detection ROI could prevent visual readiness from ever appearing stable enough
  - the same ROI had to serve two incompatible jobs:
    - detect splash
    - evaluate focus stability
- Fix:
  - introduced a separate `focusAnchorRoi`
  - calibration now requires operators to mark a nearby stable machine/bracket edge instead of the splash body for focus checks
  - runtime now uses `focusAnchorRoi` for readiness and sample-quality focus guard only
  - detection and voting remain on the original `roi`
- Remaining caution:
  - legacy calibration JSON without `focusAnchorRoi` still falls back to `roi`
  - those targets should be re-calibrated onsite to get the full benefit
- Closed: yes

## Phase-2 pseudo multi-point intermittent misses currently look more like early sampling/composition drift than algorithm failure

- Symptom:
  - pseudo multi-point `has_splash` rounds can still fail intermittently even when day/night routing is correct and the main algorithm body is otherwise stable
  - representative failure material can show the splash body shifted left or compositionally incomplete
- Current best explanation:
  - sampling can start slightly too early after returning from preset 2 toward preset 1
  - likely contributors are:
    - physical return not fully converged yet
    - FLV catch-up not yet at the true latest frame
    - or both together
- Current fix direction:
  - keep algorithm thresholds unchanged
  - first raise pseudo-multi-point scheduler `transitionSettleMs`
  - current default is now `1800 ms`
- Current triage rule:
  - if a failed round keeps:
    - `effectiveSceneMode` normal
    - `overflowFrameRatio = 0`
    - `globalMotionExceeded = false`
    - `visualState = no_splash`
  - then inspect representative-frame / debug-image / `round_*.json` first for composition shift before tuning recognition thresholds
- Next action:
  - rerun `has_splash` 10 rounds with the higher transition settle wait
  - only if misses remain, decide between:
    - more return-to-preset settle
    - more stream catch-up
- Closed: no

## Phase-2 no_splash false positives can be amplified by blurry recognition start and nearby static white devices

- Symptom:
  - some pseudo multi-point `no_splash` failures are not best explained by scene-mode routing or core splash logic collapse
  - representative material can show:
    - image still blurry / not visually settled
    - nearby white foam-like static devices being scored too much like splash structure
- Current repair priority:
  - first block pre-detection blurry / visually unready frames
  - then suppress static bright interference conservatively
  - do not start by broad day/night threshold retuning
- Current mitigation now added:
  - visual readiness gate before formal recognition sampling
  - readiness failure returns a separate execution-result path instead of continuing normal recognition
  - conservative static-bright interference gate based on weak main-bright-body dynamics
- Next action:
  - replay no_splash blurry samples and nearby-white-device samples first
  - confirm readiness failures are clearly separated from true algorithm decisions
  - confirm false `has_splash` rate drops before considering further threshold work
- Closed: no

## Calibration JSON previously duplicated runtime tuning knobs per preset

- Symptom:
  - phase-1 calibration JSON had been storing capture-gate timing and visual-stability tuning fields together with the actual preset/ROI description
  - this increased file weight and made it easier for per-preset JSON to drift away from the intended global runtime settings
- Fix:
  - moved phase-1 page runtime knobs to backend `calibration_tool`
  - kept phase-2 settle knobs under backend `recognition_v1`
  - added `GET /api/calibration/runtime-config` for frontend runtime consumption
  - narrowed new calibration saves to actual preset/ROI metadata plus snapshot references
- Compatibility:
  - old calibration JSON files still load
  - legacy runtime fields are ignored on read
  - new saves do not write those fields back
- Closed: yes

## Phase-2 step 1 current scope boundary

- Symptom: `run_once` step 1 does not yet sample FLV, does not yet emit `visualState`, and does not yet save replay materials.
- Cause: this round intentionally stops at the first ordered milestone from `docs/phase-2-recognition-v1-plan.md`.
- Status:
  - configuration consumption: done
  - single-point command entry: done
  - remote sequence sampling: pending
  - frame-level detection: pending
- Closed: yes
## Phase-2 step 2 real FLV sampling not yet end-to-end validated in Codex runtime

- Symptom: the current Codex bundled Python can compile the new sampler, but cannot complete a real preset-turn + FLV sampling chain because it lacks `requests` and `cv2`.
- Cause:
  - vendor API runtime still depends on `requests`
  - FLV sampling runtime now depends on `opencv-python-headless`
- Mitigation already applied:
  - code paths remain lazily imported where practical
  - `backend/requirements.txt` now declares the needed runtime packages
  - local synthetic replay save validation covers the async persistence branch without needing camera/network access
- Remaining requirement:
  - run step-2 acceptance in the actual project Python environment with dependencies installed and camera access available
- Closed: no

## Phase-2 replay save must not block online result

- Requirement: replay persistence failure must only log, never slow down or fail the main `run_once` response.
- Current implementation:
  - background save uses a thread pool
  - async failure only writes `logger.exception(...)`
  - sync fallback branch is also wrapped so save failure only logs
- Closed: yes
## Phase-2 step 2 CLI fake-async replay save

- Symptom: the old implementation used a global `ThreadPoolExecutor`, which could still be waited on implicitly by the one-shot CLI process before exit, so it did not satisfy the "must not block online main flow" acceptance requirement.
- Cause:
  - async execution was still tied to the parent process lifecycle
  - CLI completion and replay-save completion were not truly decoupled
- Fix:
  - replay save now runs in a detached child process via `python -m inspector.replay_worker --handoff ...`
  - main process no longer compresses a full replay handoff archive
  - main process only writes lightweight handoff metadata plus temporary raw frame files, then returns
- Validation:
  - local synthetic replay test returned in about `18.82 ms`
  - replay status changed from `pending` to `ready` after return
- Closed: yes

## Shared-memory handoff was not safe enough for Windows CLI exit

- Symptom: the first attempt to replace the payload archive with named shared memory failed in local validation with worker-side attach errors on Windows.
- Cause:
  - after CLI parent exit, the last shared-memory handle could disappear before the detached child had attached
- Fix:
  - switched to temporary raw frame handoff files plus lightweight JSON descriptor
  - kept the expensive compression step inside the detached child process
- Closed: yes

## Phase-2 step 2 evidence path readiness ambiguity

- Symptom: `replaySequencePath` and `replayMetadataPath` were previously returned without any status field, so callers could misread them as immediately available files.
- Cause: result model exposed preallocated paths but not replay material readiness state.
- Fix:
  - added `replaySave.status`
  - added `replaySave.statusPath`
  - added `replaySave.message`
  - worker writes `replay-status.json` with `pending` / `ready` / `failed`
- Closed: yes
## Phase-2 step 3 local splash falsely driving global alignment

- Symptom: the first alignment implementation could estimate absurd whole-frame shifts on locally dynamic splash-like frames, which erased genuine ROI motion and broke frame scoring.
- Cause:
  - phase-correlation output was accepted without a realism guard
  - local motion on synthetic splash frames could dominate the estimated shift
- Fix:
  - added `maxAlignmentShiftRatio`
  - shifts above the allowed whole-frame jitter range are rejected and forced back to zero
- Validation:
  - jitter sequence still aligned successfully
  - splash-like sequence no longer collapsed into zero residual motion
- Closed: yes

## Phase-2 step 3 temporal voting intentionally still pending

- Symptom: result now includes frame-level summaries, but `visualState` remains `null`.
- Cause: this round stops at the third ordered milestone and intentionally does not add temporal voting yet.
- Status:
  - frame alignment: done
  - frame features: done
  - weighted frame scoring: done
  - temporal voting: pending
- Closed: yes

## Phase-2 step 3 raw global motion evidence was being silently erased

- Symptom:
  - frames whose estimated whole-frame shift exceeded the allowed alignment range were forced to `(0, 0)`
  - this both hid the overflow evidence and made later ROI motion look "locally dynamic" without telling the caller why
- Fix:
  - preserved raw estimated shifts in the alignment result
  - tracked separately clamped applied shifts used for actual frame translation
  - added overflow flags so the caller can see whether whole-frame motion exceeded the trusted alignment range
- Closed: yes

## Phase-2 step 3 detect timing and debug evidence were mixed into the online path

- Symptom:
  - representative-frame and motion-debug artifacts were still written synchronously in `run_once`
  - `timing.detectMs` stayed at `0`, so detection cost could not be separated from write cost
- Fix:
  - `timing.detectMs` is now filled from the real detect stage
  - representative/debug artifact generation moved into the replay persistence path
  - async worker now writes those artifacts as best-effort outputs under the replay run directory
- Closed: yes

## Phase-2 step 3 dynamic-area summary field had mismatched semantics

- Symptom: `scoreSummary.dynamicAreaRatio` previously returned the normalized score component instead of the raw dynamic-area ratio.
- Fix:
  - `scoreSummary.dynamicAreaRatio` now returns the raw feature mean
  - `scoreSummary.dynamicAreaScore` now carries the normalized scoring component
- Closed: yes

## Phase-2 step 4 async evidence was still exposed to config drift

- Symptom:
  - detached replay worker could still reload the current `recognition_v1` config at worker start
  - if local config changed after online return, async evidence could diverge from the main-chain decision
- Fix:
  - main chain now freezes `effectiveRecognitionConfig` before temporal voting
  - handoff payload includes that effective config snapshot
  - replay directory writes `recognition-config.snapshot.json`
  - replay worker now uses the snapshot from handoff instead of reloading mutable local config
- Closed: yes

## Phase-2 step 4 visualState was still missing from success results

- Symptom: after step 3 the recognition pipeline still returned `visualState = null` even on successful end-to-end runs.
- Fix:
  - added temporal vote resolution on top of frame-level outputs
  - added lightweight reliability gates that downgrade only to `undetermined`, not execution failure
  - `executionResult` remains the flow result while `visualState` now carries the visual conclusion
- Closed: yes

## Phase-1 stability ROI no longer matches current workflow target

- Symptom:
  - the calibration tool still required a separate `stabilityRoi`
  - this added an extra operator step that the current workflow no longer wants to maintain
- Fix:
  - removed the dedicated stability ROI UI, draft field, schema field, and save validation
  - kept the stability gate but switched its visual sampling basis back to full frame
  - preserved backward compatibility by ignoring `stabilityRoi` if old calibration JSON files still contain it
- Closed: yes
## Phase-2 frame-level scoring over-trusted bright reflections

- Symptom:
  - daytime no-splash samples could still stabilize into `has_splash`
  - the old frame scorer over-weighted generic motion and dynamic-bright-area evidence
  - fragmented water reflections near the aerator could satisfy the old `bright + moving` path too easily
- Cause:
  - step-3 frame features did not explicitly require a large continuous bright splash body near the ROI center
  - `localResidualMotion` and `dynamicAreaRatio` still acted like dominant positive evidence
- Fix:
  - added bright connected-component analysis in `inspector/frame_features.py`
  - added `largestBrightComponentRatio`, `brightComponentCount`, `fragmentationScore`, `centerBrightCoverage`, `upperHalfBrightRatio`, `lowerHalfBrightRatio`, and `verticalSpreadRatio`
  - tightened `highlightDisturbance` to only score disturbance inside the main bright component
  - added a hard gate in `inspector/frame_scoring.py` so frames without a sufficiently large central continuous bright blob cannot pass
  - rebalanced default weights so bright-mass geometry dominates and generic motion only acts as weak supporting evidence
- Expected acceptance impact:
  - obvious reflection-only clips should no longer all collapse to `has_splash`
  - true splash clips should still pass mainly because they form a thick central white body
- Closed: yes
## Phase-2 hard gate still allowed static white masses

- Symptom:
  - a large central continuous white region could still pass the frame gate even if it barely moved
  - this left a false-positive risk for daylight reflection plateaus or IR overexposure
- Cause:
  - the first hard gate only checked structure
  - structural feature weights alone were already high enough to clear `framePassThreshold`
- Fix:
  - added dynamic minimums to the hard gate
  - the frame gate now requires at least one of:
    - `localResidualMotion >= hardGateMinLocalMotion`
    - `dynamicAreaRatio >= hardGateMinDynamicAreaRatio`
    - `highlightDisturbance >= hardGateMinHighlightMotion`
- Closed: yes

## Phase-2 hardGatePassed summary field was ambiguous

- Symptom:
  - `hardGatePassed=true` previously meant only that at least one frame passed
  - replay reviewers could misread that as "the whole sequence cleared the hard gate"
- Fix:
  - added `anyHardGatePassed` for the old "at least one frame" meaning
  - redefined `hardGatePassed` as the sequence-level boolean derived from `hardGatePassRatio >= sequenceVoteThreshold`
  - preserved `hardGatePassRatio` and `hardGatePassCount` as the primary quantitative diagnostics
- Closed: yes

## Phase-2 hard-gate thresholds were incorrectly tied to score scales

- Symptom:
  - config validation rejected perfectly valid setups where the hard-gate threshold should be stricter than the score normalization saturation point
- Cause:
  - the validator treated gate thresholds and scoring scales as if they were the same concept
- Fix:
  - removed the `hardGate* <= *FeatureScale` coupling checks
- Closed: yes

## Phase-2 night IR thresholds still need real positive acceptance tuning

- Symptom:
  - the new `night_ir` branch is structurally in place, but current acceptance evidence is still asymmetric
  - we have initial real dark `no_splash` replay checks plus synthetic positive probes, but not yet a small real clear night `has_splash` set
- Current evidence:
  - three darkest real replay clips stayed `no_splash` with `passRatio = 0.0`
  - synthetic `static_bars` stayed blocked
  - synthetic `dynamic_blob` passed with `passRatio = 0.95`
- Risk:
  - `hardGateMinGapFillRatio`
  - `hardGateMinTemporalAreaVariance`
  - `hardGateMinTemporalShapeVariance`
  - and the night-only weights are still provisional until real positive IR splash clips are replayed
- Next action:
  - collect `5-10` clear real `night_ir has_splash` clips from the same preset
  - replay them with `sceneMode = night_ir`
  - tune only the night override block in `recognition_v1.nightIr`
- Closed: no

## Phase-2 night IR temporal features recovered but remain weak separators

- Symptom:
  - after the relative-threshold fix, `temporalAreaVariance` and `temporalShapeVariance` are no longer all zero
  - but on the current real night replay set they are still low and partially overlapping between low-structure and high-structure clips
- Current observation:
  - high-structure night clips are now primarily separated by:
    - `largestBrightComponentRatio`
    - `centerBrightCoverage`
    - `verticalSpreadRatio`
    - `gapFillRatio`
  - temporal disturbance is now only a weak supporting branch, not a clean positive/negative separator yet
- Impact:
  - the main night split is already much better than the broken all-zero state
  - but one high-structure real replay still lands `no_splash`
  - another still lands `undetermined`
- Next action:
  - keep the current day/night split and relative extraction
  - collect a slightly larger real night set
  - decide whether the next round should:
    - further refine temporal dominant-body features
    - or keep temporal gating weak and rely mainly on stronger structure separation for IR
- Closed: no

## Phase-2 night IR gap-fill threshold was over-tightened

- Symptom:
  - two real positive night samples with recovered large central bright masses still landed `no_splash`
  - both showed `hardGatePassRatio = 0`
  - their mean `gapFillRatio` was only about `0.888 - 0.890`, below the configured `0.92`
- Root cause:
  - after the relative-threshold extraction fix, night structural separation was already working
  - `hardGateMinGapFillRatio = 0.92` remained stricter than necessary for some true positive diffuse splash bodies
- Fix:
  - replay-tuned `hardGateMinGapFillRatio` down to `0.86`
  - kept all other night knobs unchanged in this round
- Result:
  - current `15` positive night samples improved to `12 has_splash / 2 undetermined / 1 no_splash`
  - current `5` negative night samples remained `5 / 5 no_splash`
- Remaining boundary:
  - there is still `1` positive `no_splash`
  - and `2` positive `undetermined`
  - these are no longer best explained by the same pure gap-fill over-tightening
- Closed: yes

## Phase-2 night IR center-coverage threshold was the next structure bottleneck

- Symptom:
  - after fixing `gapFillRatio`, several night positive boundary samples still stayed `no_splash`
  - their shared pattern was no longer low `gapFillRatio`, low `largestBrightComponentRatio`, or low `verticalSpreadRatio`
  - the more consistent blocker was `centerBrightCoverage` sitting around the active gate line
- Evidence:
  - representative boundary values clustered around:
    - `0.478`
    - `0.500`
    - `0.425`
    - `0.402`
    - `0.361`
  - the current night gate had still been `hardGateMinCenterBrightCoverage = 0.50`
- Fix:
  - replay-tuned `hardGateMinCenterBrightCoverage` down to `0.46`
  - kept `hardGateMinGapFillRatio = 0.86` unchanged this round
  - did not touch vote thresholds or dynamic-feature weights
- Result:
  - the representative `center ≈ 0.478` boundary sample was recovered from `no_splash` to `has_splash`
  - lowering further to `0.44` did not add more replay benefit in this round
  - the negative reference set still stayed `5 / 5 no_splash`
- Remaining boundary:
  - lower-center weak samples around `0.36 - 0.42` still remain blocked
  - these are not yet safely recoverable just by shaving the same threshold further
- Closed: yes

## Phase-2 auto scene-mode switching still needs real baseline replay acceptance

- Symptom:
  - the auto scene-mode routing layer is now implemented, but the frozen real daytime and night-IR baselines have not yet been replayed end-to-end under `sceneMode = auto` in this environment.
- Current local evidence:
  - bundled Python `compileall inspector backend/app` passed
  - synthetic smoke check routes:
    - clearly colored frames -> `day_visible`
    - clearly grayscale IR-like frames -> `night_ir`
- Remaining risk:
  - clear real field scenes may still land in `ambiguous` too often
  - borderline low-saturation daytime clips could still route differently than intended
- Next action:
  - run the frozen baseline sample folders with `recognition_v1.sceneMode = auto`
  - confirm:
    - clear daytime stays `day_visible`
    - clear night IR stays `night_ir`
    - ambiguous fallback does not over-trigger on clear scenes
- Closed: no

## Phase-2 auto scene-switch first integration had release-risk defaults and version-label drift

- Symptom:
  - the first auto-switch integration temporarily shipped `sceneMode = auto` as the default before frozen-baseline replay acceptance
  - shared `algorithmVersion` also still pointed at the night label, which could mislabel daytime runs
- Fix:
  - rolled defaults back to manual mode until replay acceptance:
    - example config -> `day_visible`
    - local runtime config -> `night_ir`
  - made both `dayVisible.algorithmVersion` and `nightIr.algorithmVersion` explicit
- Current remaining risk:
  - auto mode itself still needs real baseline replay acceptance before becoming the default again
- Closed: yes

## Phase-2 pseudo multi-point timeout semantics are currently soft observation only

- Symptom:
  - the pseudo multi-point validator now exposes scheduler-level timeout thresholds, but the first implementation does not forcibly terminate an already-running inner recognition round.
- Root cause:
  - the accepted design requires reusing `RunOnceService` unchanged
  - on the current Windows CLI path, hard-killing an in-flight inner recognition call would either require invasive changes to the recognition body or unsafe process management
- Current behavior:
  - transition-step and round-level overruns are marked in:
    - `transitionPreset.timedOut`
    - `roundTimedOut`
    - summary timeout fields
  - a timeout overrun causes the round to be recorded as failed
  - already-finished round evidence and the final summary are still preserved
  - the tool does not preemptively abort the in-flight recognition body
- Practical impact:
  - this is enough for the current pseudo multi-point validation goal:
    - trace repeatability
    - measure real round cost
    - detect slow or hanging rounds in evidence
  - it is not yet a true production scheduler timeout mechanism
- Next action:
  - if real pseudo multi-point testing exposes stuck rounds often enough to matter, add a later dedicated cancellation strategy without mutating recognition semantics
- Closed: no

## Local project `.venv` is currently broken on this machine

- Symptom:
  - `.venv\\Scripts\\python.exe` cannot start and still points to a missing host interpreter under `C:\\Users\\Maple_Rain\\AppData\\Local\\Programs\\Python\\Python312\\python.exe`
- Current impact:
  - workspace-local venv cannot currently be trusted for command validation in Codex
  - validation in this turn used the bundled Codex runtime Python instead
- Next action:
  - repair or recreate `.venv` before relying on it again for local scripted verification
- Closed: no

## Frontend package-manager validation is currently noisy under restricted network

- Symptom:
  - package-manager-based frontend validation in this environment can try to reshuffle `frontend/node_modules` and then reach the public npm registry, which fails under the current restricted network policy
- Current impact:
  - full frontend `tsc` verification was not completed cleanly in this turn through `pnpm`
  - code changes were instead checked by:
    - direct source inspection
    - CLI help / runtime-path verification where applicable
- Next action:
  - use the project’s intended local frontend toolchain in a network-available or already-stable dependency environment before treating frontend typecheck as fully revalidated
- Closed: no
