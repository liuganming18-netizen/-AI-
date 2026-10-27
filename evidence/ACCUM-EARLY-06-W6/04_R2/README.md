# ACCUM-EARLY-06-W6-R2 · 总交接收口（目录索引）

> **主交付物是 `总交接_R2_待Codex审核.md`（v2）。本 README 只是目录索引，不重复其结论。**
> 工单：`ACCUM-EARLY-06-W6-R2`｜窗口六（C）｜工程根目录 `/Users/boos/Desktop/威科夫因子共振趋势`

## 一句话状态

**财务层可用、工程层可用、定性层只有 A 单方证明、自然运行未验证。**
A 已交付（我方独立复验 11/12，1 项返工）；B 财务层 102/102 通过但 3 项未收口；D 的 21 项待决仍未裁决。

## 文件

### 报告

| 文件 | 说明 |
|---|---|
| `总交接_R2_待Codex审核.md` | **主交付物（v2）**：四列状态矩阵、三方数字交叉验证、三组样本分离、可用/返工/待决/下一步 |
| `归档_总交接_R2_2026-10-27T0129Z.md` | 上一版原样归档（含已过时的「A 未交付」「B 未复验」表述，保留以对比） |

### 脚本（按序，均只读上游）

| 脚本 | 产出 | 结果 |
|---|---|---|
| `01_audit_state_correction.py` | `out/01_state_correction_matrix.json` | 旧批次 |
| `02_sample_disconnection_audit.py` | `out/02_sample_disconnection_audit.json` | 旧批次 |
| `03_generate_r2_handoff.py` | `out/03_r2_handoff_summary.json` | 旧批次 |
| `04_verify_r2_consistency.py` | `out/04_consistency_check.json` | 旧批次 |
| `05_verify_A_delivery.py` | `out/05_A交付独立复验.json` | **12 项 / 11 通过**（A07b 失败） |
| `06_阶段状态矩阵.py` | `out/06_阶段状态矩阵.json` | **15 项 / 12 通过**（V05b、V07、V08 为 B 缺陷） |
| `07_交接数字校验.py` | `out/07_交接数字校验.json` | **78 项 / 78 通过** |

> `01–04` 是 v1 阶段的产物，结论已被 `05–07` 取代，保留不动（不覆盖旧报告）。**读结论请读 `06` 与 `07`。**

## 复跑

```bash
cd /Users/boos/Desktop/威科夫因子共振趋势
PY=/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python   # 必须用项目 venv（含 duckdb/pyarrow）
$PY evidence/ACCUM-EARLY-06-W6/04_R2/scripts/05_verify_A_delivery.py
$PY evidence/ACCUM-EARLY-06-W6/04_R2/scripts/06_阶段状态矩阵.py
$PY evidence/ACCUM-EARLY-06-W6/04_R2/scripts/07_交接数字校验.py
```

- `06` 的 `生成时刻_utc` 按设计每次变；其余判定与哈希不随运行变化。
- **`06` 读的是 B 的实时产物**。B 若再改，哈希与 V05/V06/V07/V08 判定会变，届时总交接 §4 需重读。
- 本版锚定的 B 批次：`01/02/03/summary` 均为 `2026-10-27T02:02:0xZ`，`05` 无 timestamp。

## 边界

- 未重跑 A / B 的任何脚本（重跑会改写他人 `out/`，等于替人重签）。
- 未修改 A / B / D 任何产物，未选参数，未裁决冲突。
- 未改 `统一用户决定`、`当前交接`、生产代码、冻结规则。
- **不得**用本目录任何结论声称「策略通过」「自然信号→订单闭环已打通」「实盘可交付」。
