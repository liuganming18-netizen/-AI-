# STEP1-A｜正式回测测试对象整理（只读清点）

- 生成时间：2026-10-18 21:05（UTC+8）
- 工程根：`/Users/boos/Desktop/威科夫因子共振趋势`
- 性质：**只读清点**。未运行任何回测，未修复任何问题，未新增策略，未调参，未改其他窗口产物。
- 写入范围：仅 `evidence/STEP1-A/`。
- 结论口径：**待 Codex 汇总确认**。本文不宣称任何正式验收通过。

---

## 工程目录确认

- 本次清点对象是 **`/Users/boos/Desktop/威科夫因子共振趋势`**（真实工程，78G，含 `策略/`、`框架/`、`数据/`、`回测日志/`、`evidence/`、`docs/`、`UI界面/`）。
- 分支 `wyckoff-v3.1-main`，HEAD `2a13325`，远端 `origin` 同点。
- **未使用任何 Codex 空工作树**作为工程源；所有数字都来自本工程内的 run 目录、配置文件和已交付报告。
- 复用的既有材料：`evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md`（冻结规则原文）、
  `docs/策略优化_数据收集_分窗口回测施工单_2026-10-15.md`（旧代际施工单）、
  `evidence/AUDIT-375-V3/`（资格审计）、`evidence/FULL_SWEEP_200x100/`（压力测试）、
  `evidence/FLOW-OI-01/` 与 `evidence/FACTOR-ENTRY-01/02/`（两套四组对照）、
  `evidence/BACKTEST-LOSS-03/`（成交价越界核查）、`evidence/FLOW-DATA-01/`（PIT 抽查）。

---

## 一句话结论

工程里现在有 **6 个代码代际、2125 个带输入的 run、至少 5 套互不相同的窗口口径、438 个标的**，
它们被混在同一个 `回测日志/event_trend_v0_1/runs` 目录里；**没有任何一个 run 的 `formal_pass_allowed` 为真**，
也**没有任何一个 run 具备完整的历史合约规格证明**。因此本单交付的是**冻结候选表**，不是「已确定的对象」。

---

## 交付物

| 文件 | 内容 |
|---|---|
| `README.md` | 本文件：范围、结论、清单、复现方式 |
| `01_回测对象冻结候选表.md` | **主表**：11 个候选对象，逐行含对象ID/版本/范围/规则/证据路径/状态/冲突 |
| `02_逐项清点.md` | 工单五项逐项清点（策略与哈希 / 批次清单 / 参数 / 四组对照 / 执行状态） |
| `03_冲突与未知.md` | 冲突项，双方证据并列，不自行裁定 |
| `04_待确认问题清单.md` | 需 Codex 拍板的问题，每条附「为什么必须定」与「用什么证据能定」 |
| `runs/step1a_runs.csv` | 2133 行逐 run 明细（每行一个 run 目录的窗口、代际、参数、成交、规格状态） |
| `runs/step1a_batches.csv` | 10 行批次汇总 |
| `runs/code_generation_diff.csv` | 6 个代码代际与当前工作树的逐文件比对 |
| `runs/runs_pool_clusters.csv` | 把混装的 `runs` 目录按「创建小时 × 代码代际」拆成 12 簇 |
| `scripts/*.py` | 3 个只读盘点脚本 |
| `reproduce.sh` | 一键复现（约 1 分钟，只读） |

---

## 四条必须先知道的事实

1. **当前工作树 = 代号 `734c505d` 的代际**，18 个代码文件逐位一致，覆盖 544 个 run。
   但这一代**没有提交**：`runner.py`、`trading_core/engine.py`、`trading_core/__init__.py` 相对 HEAD 都有改动，
   `trading_core/rejects.py` 是新增未跟踪文件。
2. **其余 5 个代际（1581 个 run）无法用当前代码复现**。其中 `f0b18131` 是「已提交的 runner + 未提交的 engine」的混合态，
   这个组合从未以任何一次提交存在过。
3. **合约规格 0 条已验证**：1750 个带精度块的 run 里，`unverified_historical` 1682 个、`contradicted` 60 个、
   `not_listed` 8 个，`verified` 为 0。所以全部 `formal_pass_allowed = False`。
4. **资格审计只覆盖了 375 个 run（17.6%）**，且正好是最老的两个代际；剩下 1750 个 run 没有任何资格审计结论。

---

## 复现方式

```bash
cd /Users/boos/Desktop/威科夫因子共振趋势
sh evidence/STEP1-A/reproduce.sh
```

脚本只读 CSV 与 `inputs.json` / `summary.json`，不写任何 run 目录。
