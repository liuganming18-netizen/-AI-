# BF-01 根因与函数依赖

- 订单：`BF-01-CLOSE`
- 修复前主线：`bcd72806`
- 修复后主线：`7c625c51`
- 改动文件：`策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py`（**仅此 1 个**）
- 实际纳入：**183 行新增 + 2 行删除 = 185 行变更**

---

## 一、根因

`26ec8aef` 提交了测试文件 `策略/WyckoffMaster/event_trend_v0_1/tests/test_backtest_repair_01_metrics.py`，它第 34 行写：

```python
from WyckoffMaster.event_trend_v0_1.engine_adapter import (
    _merge_summary, compute_summary_metrics)
```

但 `engine_adapter.py` 里这两个名字**从来没有被提交过**——只在工作区存在。

pytest 在**收集阶段**就会执行模块顶层 import，所以不是「11 个用例失败」，而是**整个模块导入即崩**：

```
ImportError: cannot import name '_merge_summary' from
'WyckoffMaster.event_trend_v0_1.engine_adapter'
```

pytest 的默认行为是「收集阶段有 1 个 error 就中断整轮」，所以干净 HEAD 上**一条用例都跑不起来**：

| 状态 | 收集结果 |
|---|---|
| 修复前 `bcd72806` | 164 tests collected, **1 error**（中断） |
| 修复后 `7c625c51` | **175 tests collected, 0 error** |

差的 11 个正是这个文件里的 11 个用例（M01–M09 + M04b + M05b）。

### 为什么是「漏提交」而不是「实现写错」

查过所有引用点：

| 引用 | 状态 |
|---|---|
| `tests/test_backtest_repair_01_metrics.py:34`（消费者） | 已提交 |
| `框架/backend/backtest_runner.py:866-910`（下游消费者，读 wins/losses/win_rate_pct/max_drawdown_pct/profit_factor/… 并兼容旧名 win_count/loss_count） | **已提交** |
| `engine_adapter.py`（生产者） | **缺失** |

也就是说：读取方早就上线了，生产方没上线。`backtest_runner` 里那些 `summary_data.get("wins")` 一直读到 `None`。这不是设计变更，是提交时漏了一个文件。

---

## 二、函数依赖（纳入范围的最小闭包）

新增内容全部集中在 `engine_adapter.py` 第 418–593 行，加 `run_event_trend_backtest` 里 2 行接线。

### 1. 口径常量（6 个 + 1 个精度阈值）

| 符号 | 行号 | 作用 |
|---|---|---|
| `WIN_LOSS_BASIS` | 433 | 说明「盈亏按 net_pnl 判，平盘既不算盈也不算亏」 |
| `WR_BASIS` | 434 | 胜率分母 = 已平仓笔数（含平盘） |
| `PF_BASIS` | 435 | 盈亏比聚合口径 |
| `AWLR_BASIS` | 436 | 平均盈亏比均值口径 |
| `MDD_BASIS` | 437 | 最大回撤口径 |
| `COST_ID_BASIS` | 438 | 成本恒等式口径（滑点不二次扣减） |
| `_METRIC_EPS` | 441 | 1e-9 容差 |

这些常量**只被写进返回值**，让页面能显示「这个数字是怎么算的」，不参与任何计算分支。

### 2. 纯计算辅助（3 个）

| 函数 | 行号 | 依赖 | 说明 |
|---|---|---|---|
| `_num(x)` | 444 | `math.isfinite` | None / 非数 / NaN / inf 一律返回 None，脏值不进合计 |
| `_mean(xs)` | 457 | 无 | 空列表返回 None（不是 0） |
| `_max_drawdown_pct(values)` | 461 | 无 | 少于 2 点返回 None（不是 0：一个点看不出回撤） |

### 3. 主函数

```
compute_summary_metrics(trades, equity_curve, initial_equity, final_equity) -> dict   # 480
  ├─ _num × 5 处（net_pnl / gross_pnl / fees / funding_fee / equity）
  ├─ _mean × 2 处（avg_win / avg_loss）
  ├─ _max_drawdown_pct × 1 处
  └─ 常量 × 6 处（写进 basis 字段）

_merge_summary(base, trades, equity_curve, initial_equity, final_equity) -> dict      # 582
  └─ compute_summary_metrics(...)
     └─ out.setdefault(k, v)  ← 只加不改，旧字段优先级高于新算值
```

模块级依赖只有 `math` 与 `typing.{Optional, List, Tuple}`——**这三个 import 在改动前就已经在文件里**，本次没有新增任何 import。

### 4. 接线（2 行）

```python
# 921
"summary": _merge_summary({
    ... 原有 13 个字段原样不动 ...
# 936
}, trades, result.get("equity_curve"), eq,
   float(result.get("final_equity", eq))),
```

`_merge_summary` 用的是 `setdefault`，所以原有 13 个字段的值**优先级更高**，新算出来的同名字段（如 `trade_count`）不会覆盖它们。

---

## 三、契约核对（对照测试与调用方）

| 契约点 | 谁要求 | 实现是否满足 |
|---|---|---|
| 盈亏按 `net_pnl` 判，平盘计入分母 | M06、`WIN_LOSS_BASIS` | 满足：`wins+losses+flats == trade_count`，胜率分母含平盘 |
| 零分母返回 `None`，不填 0、不输出 inf | M01、M03、M04 | 满足：全盈利时 PF 为 None 并写说明；全亏损时 PF=0.0（分子 0 有定义）；曲线不足 2 点 MDD 为 None |
| 成本恒等式 `net == gross − fees − funding`，滑点不二次扣减 | M05、M05b | 满足：`cost_identity_ok` / `cost_identity_delta`，恒等式**只用于校验**，不用来推净收益 |
| 旧 13 个 summary 字段一字不变 | M07 | 满足：`setdefault` + 实测逐字段比对 0 处改动 |
| 下游 `backtest_runner` 能读到新字段且兼容旧名 | M08 | 满足：该文件已在主线，字段名逐个对齐 |
| 脏值（None/NaN/inf）不进合计 | M09 | 满足：`_num` 过滤，并在 `metrics_notes` 写明「N 笔成交缺 net_pnl」 |

---

## 四、是否触碰冻结策略语义

**没有。** 三重核对：

1. **改动内容里没有任何策略标识**：对新增行做关键词扫描（`stoploss` / `trail` / `risk_budget` / `max_holding` / `initial_equity =` / `taker_fee` / `slippage_bps` / `oi_` / `participation` / `config.`）——**命中 0 条**。
2. **只读账本产物，不改账本**：输入是 `trades`（成交行）与 `equity_curve`（权益曲线），输出是一个新 dict。不写库、不下单、不改持仓。
3. **旧字段实测零改动**：同一个确定性回测在改动前后各跑一次，把 `summary` 全字段导出比对 —— 13 个旧字段**数值全部一致，改动 0 处**，新增 23 个字段。

结论：这是**报表层加性扩展**，不构成策略语义变更，不需要停下来请示。
