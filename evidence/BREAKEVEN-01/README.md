# BREAKEVEN-01 · 交付索引

工单：1R／2R 保本止损同样本对照。结论状态：**待 Codex 审核**，不上线。

## 先看这两个

1. **`报告.md`** —— 结论、三组总表、差异分解、复现校验、限制与未完成项
2. **`out/paired_trades.csv`** —— 206 笔逐笔 A/B/C 配对表（60 列）

## 其余

| 文件 | 说明 |
|---|---|
| `00_样本与规则契约.md` | 复用核实、样本口径、三组规则、保本价公式、执行时序 |
| `02_代码与数据版本.md` | 代码/数据 SHA-256、修正记录、零改动自检 |
| `03_实际命令.md` | 实际执行命令、日志位置、版本作废说明 |
| `out/summary.json` | 汇总指标（机器可读） |
| `out/sample_plan.json` | 253 个运行（覆盖原样本 100%） |
| `out/signal_instances.csv` | 57 个信号的多窗口不同结局明细 |
| `out/ambiguity.csv` | 同根先后不明清单（0 笔） |
| `out/verify_results.json` | 9 项保本规则测试（全 PASS） |
| `out/dedup_sensitivity.json` | 候选级 206 笔 vs 事件级 201 笔（5 组重复入场）对照 |
| `out/versions.json` | 代码/数据指纹 |
| `logs/02_run_abc_v3.log` | 最终原始运行日志（253 run，0 error） |
| `logs/06_dedup.log` | 去重敏感性运行日志 |
| `code/` | 全部脚本（`be_core.py` 是唯一新增的保本逻辑，约 200 行） |

## 复现

```
python evidence/BREAKEVEN-01/code/01_build_sample.py
WORKERS=6 python evidence/BREAKEVEN-01/code/02_run_abc.py
python evidence/BREAKEVEN-01/code/03_report.py
python evidence/BREAKEVEN-01/code/04_verify.py
python evidence/BREAKEVEN-01/code/05_hashes.py
python evidence/BREAKEVEN-01/code/06_dedup_sensitivity.py
```

解释器：`/Users/boos/.workbuddy-ai/binaries/python/envs/default/bin/python`
