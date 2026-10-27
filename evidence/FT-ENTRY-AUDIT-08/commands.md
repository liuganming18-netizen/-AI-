# FT-ENTRY-AUDIT-08 实际执行命令

> 工作目录：`/Users/boos/Desktop/威科夫因子共振趋势`
> Python：`evidence/FT-BASELINE-01/.venv/bin/python`（Python 3.11，pandas 3.0.6 / numpy 2.4.6 / pyarrow 26.0.0）
> 全部脚本只读上游；未调用 `freqtrade`，未重跑回测。

```bash
cd "/Users/boos/Desktop/威科夫因子共振趋势"
PY=evidence/FT-BASELINE-01/.venv/bin/python
A=evidence/FT-ENTRY-AUDIT-08
```

## 01 输入锁定（166 项 FT-ENTRY-07 指纹复核 + 18 项追加锁定）

```bash
$PY $A/queries/01_lock_inputs.py 2>&1 | tee $A/logs/01_lock_inputs.log
```

结果：`FT-ENTRY-07 基准 166 项；不一致 0 项`；追加锁定 18 项。

## 02 第①项：统计稳定性

```bash
$PY $A/queries/02_stats_stability.py 2>&1 | tee $A/logs/02_stats_stability.log
```

结果：38 个 30 天块；18 主族 + 9 次族；C1−C0 事件级 +0.8007U（CI 0.1204~1.4096，p=0.0326），
主族 maxT p=0.189；C2−C1 −0.069U（p=0.9188）。

## 03 第②项：入场下限 L−0.2A → P0

```bash
$PY $A/queries/03_entry_floor_impact.py 2>&1 | tee $A/logs/03_entry_floor_impact.log
```

结果：1357 笔成交 H 与 `frozen_H` 100% 精确相等；1397 个确认候选（778+619）
在旧/新下限下同一触发根；受影响 0。

## 04 第③项：74 币逐币有效区间

```bash
$PY $A/queries/04_sample_validity.py 2>&1 | tee $A/logs/04_sample_validity.log
```

结果：74 币 = 26 起点合格 + 44 期内后段合格 + 4 全程不合格；115 笔交易早于
200 根 4h 资格起点、0 笔早于 42 根箱体；未删除交易。

## 05 三问判定固化

```bash
$PY $A/queries/05_final_verdict.py 2>&1 | tee $A/logs/05_final_verdict.log
```

## 06 本轮产物指纹（最后执行）

```bash
$PY $A/queries/06_lock_outputs.py 2>&1 | tee $A/logs/06_lock_outputs.log
```

## 说明

- `queries/_common.py` 中的块级统计常数（30 天块 / 5000 次 / seed=20261020 / Holm / maxT）
  与 `FT-ENTRY-AUDIT-04/05/06/07` 完全一致，未按结果更换检验。
- 第②项的 4h 箱体 H/L/A 为**几何重算**：按冻结规则从锁定 4h 数据取 42 根，用
  `frozen_H` 1357/1357 对拍；不是重跑回测/信号/撮合。
- 所有输出写入 `evidence/FT-ENTRY-AUDIT-08/out`，日志写入 `logs/`；未覆盖 Work 旧文件。
- `06_lock_outputs.py` 的指纹清单不含清单自身、`06_产物锁定摘要_v1.json` 与 `logs/06_lock_outputs.log`
  （三者与锁定同时写入）；其余 38 个交付物逐文件可复核。
