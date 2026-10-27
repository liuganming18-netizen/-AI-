# BREAKEVEN-02 · 交付索引

工单一｜Work 窗口1｜【回测执行】BREAKEVEN-02：保本止损对照收口。
结论状态：**补齐三项验收缺口，收口完成，待联合验证，不上线**。

---

## 核心交付文件导航

1. **`报告.md`** —— 核心收口报告、三项缺口闭环结论、三组固定仓位总表、三项归因拆解、去重敏感性分析。
2. **`out/paired_trades.csv`** —— 206 笔逐笔 A/B/C 固定仓位配对表（65 列，含四时刻、保本价、三项归因分类、OPUSDT 单列标记）。
3. **`01_TRX与历史基线对账.md`** —— TRXUSDT 旧版（FULL_SWEEP）vs 当前基线 A 的候选、仓位、成交和费用全项对账说明。
4. **`out/opusdt_cascade.json`** —— OPUSDT 动态仓位（8.2 股）vs 固定仓位（8.3 股）与 0.112375 U 串行权益差异单列对账。
5. **`out/subsequent_costs.csv`** —— 17 笔触发后新增资金费结算导致微亏的明细清单（平均 -0.08 U/笔）。

---

## 完整文件结构与说明

| 文件/目录 | 说明 |
|---|---|
| `00_样本与规则契约.md` | 复用核实、样本口径、固定仓位原理、三组规则、四时刻定义、跳空规则 |
| `01_TRX与历史基线对账.md` | TRX 一笔差异对账、旧版与当前版基线全项对账表 |
| `02_代码与数据版本.md` | 代码/数据 SHA-256 指纹、零改动自检、历史版本标记 |
| `03_实际命令.md` | 实际执行命令序列、复现指南、供联合验证复用的入口 |
| `out/summary.json` | 机器可读指标汇总（三组总指标、归因拆解、一致性检验） |
| `out/sample_plan.json` | 253 个最小运行覆盖方案 |
| `out/trx_reconciliation.json` | TRXUSDT 逐项对账机器数据 |
| `out/opusdt_cascade.json` | OPUSDT 仓位差异单列对账机器数据 |
| `out/subsequent_costs.csv` | 17 笔后续费用小亏明细清单 |
| `out/dedup_sensitivity.json` | 候选级 206 笔 vs 事件级 201 笔去重敏感性对照数据 |
| `out/verify_results.json` | V1~V12 针对性测试结果（12/12 全部 PASS） |
| `out/ambiguity.csv` | 同根先后不明清单（0 笔） |
| `out/versions.json` | 文件与数据完整指纹字典 |
| `logs/02_run_abc.log` | 253 run 回测执行原始日志（0 错误） |
| `code/` | 全套源码（`be_core.py` 唯一新增保本与固定仓位 Mixin，其余为回测、对账与验证脚本） |

---

## 复现运行命令

```bash
# 步骤 1：构建样本计划
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python evidence/BREAKEVEN-02/code/01_build_sample.py

# 步骤 2：全量 253 个运行三组回测（多进程）
WORKERS=6 /Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python evidence/BREAKEVEN-02/code/02_run_abc.py

# 步骤 3：数据报告、三项归因与全项对账生成
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python evidence/BREAKEVEN-02/code/03_report.py

# 步骤 4：自动化针对性验证测试（V1 ~ V12）
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python evidence/BREAKEVEN-02/code/04_verify.py

# 步骤 5：代码与数据版本指纹
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python evidence/BREAKEVEN-02/code/05_hashes.py

# 步骤 6：去重敏感性分析
/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python evidence/BREAKEVEN-02/code/06_dedup_sensitivity.py
```
