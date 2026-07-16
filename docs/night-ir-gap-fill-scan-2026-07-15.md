# Night IR gapFill 扫描记录（2026-07-15）

## 范围

- 问题样本：`data/pseudo_multi_point_tests/AB00A7DPAJ00124_1_p1_t3_has_splash_2026-07-15T14-20-49.632721+00-00`
- 通过的夜间有水花基线：`data/pseudo_multi_point_tests/AB00A7DPAJ00124_1_p1_t3_has_splash_2026-07-15T14-16-50.353115+00-00`
- 通过的夜间无水花基线：
  - `data/pseudo_multi_point_tests/AB00A7DPAJ00124_1_p1_t3_no_splash_2026-07-15T14-13-08.289969+00-00`
  - `data/pseudo_multi_point_tests/AB00A7DPAJ00124_1_p1_t3_no_splash_2026-07-15T14-10-10.579672+00-00`

## 冻结证据

- 当前问题批次共 10 轮，其中 1 轮不进入 gapFill 扫描：
  - `round_08`：`sample_quality_focus_regressed`
- 其余 9 轮进入离线重放。
- 旧阈值 `0.81` 下的 3 个 gapFill 假阴性：
  - `round_05`：`gapFillPassCount=8`，`framePassRatio=0.35`
  - `round_07`：`gapFillPassCount=0`，`framePassRatio=0.00`
  - `round_10`：`gapFillPassCount=3`，`framePassRatio=0.10`

## 扫描结果

离线脚本：`python -m inspector.night_ir_gap_fill_scan`

粗扫 `0.81 -> 0.76`，步长 `0.01`：

| 阈值 | 有水花匹配 | 无水花匹配 | 结论 |
| --- | --- | --- | --- |
| `0.81` | `16/19` | `20/20` | 不安全 |
| `0.80` | `16/19` | `20/20` | 不安全 |
| `0.79` | `18/19` | `20/20` | 不安全 |
| `0.78` | `18/19` | `20/20` | 不安全 |
| `0.77` | `18/19` | `20/20` | 不安全 |
| `0.76` | `19/19` | `20/20` | 安全 |

细扫 `0.780 -> 0.760`，步长 `0.005`：

| 阈值 | 有水花匹配 | 无水花匹配 | 结论 |
| --- | --- | --- | --- |
| `0.780` | `18/19` | `20/20` | 不安全 |
| `0.775` | `18/19` | `20/20` | 不安全 |
| `0.770` | `18/19` | `20/20` | 不安全 |
| `0.765` | `18/19` | `20/20` | 不安全 |
| `0.760` | `19/19` | `20/20` | 安全 |

## 结论

- `nightIr.hardGateMinGapFillRatio` 应从 `0.81` 下调到 `0.76`。
- `0.765` 仍不够，问题批次 `round_07` 只恢复到 `11/20`，仍是 `pass_ratio_middle_band`。
- `0.76` 下：
  - 当前问题批次的 9 个已进入检测的有水花轮次全部恢复为 `has_splash`
  - 两组夜间无水花基线共 `20/20` 仍全部保持 `no_splash`
- 当前不调整 `sample-quality`，`round_08` 继续单独观察。

## 产物

- 粗扫 JSON：`docs/night-ir-gap-fill-scan-2026-07-15.json`
- 细扫 JSON：`docs/night-ir-gap-fill-scan-2026-07-15-fine.json`
