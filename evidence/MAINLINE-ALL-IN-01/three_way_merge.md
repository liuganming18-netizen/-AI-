# 同一文件多版本的三方比较记录

**为什么要三方比较**：`git status` 只能告诉你「这个文件被改过」，不能告诉你
「哪一边的改动是有效功能、哪一边是旧版本残留」。
上一轮已经踩过一次：`pr2-delivery-verify-02` 里 `unified_runtime.py` 的「独有改动」
实际是 **C02-R1 / 10R 修复之前的旧版本残留**，如果按「谁有改动就合谁」会直接把修复退回去。

**方法**：`git merge-file -p <ours> <base> <theirs>`，
`base` = 共同祖先（`9c8929d5`），`ours` = 主线基线（`f05b6d63`），`theirs` = 隔离目录版本。

---

## 一、逐文件结果

| 文件 | 结果 | 说明 |
|---|---|---|
| `策略/WyckoffMaster/event_trend_v0_1/runner.py` | **1 处冲突，已人工解** | 唯一需要判断的文件 |
| `框架/backend/backtest_runner.py` | 零冲突 | 合并后 +37/−… |
| `框架/backend/oi_feed.py` | 零冲突 | 合并后 +66/−… |
| `策略/WyckoffMaster/event_trend_v0_1/instrument_spec.py` | 零冲突 | 合并后 +23/−… |
| `策略/WyckoffMaster/event_trend_v0_1/engine_adapter.py` | 零冲突，**结果与主线逐字一致** | 等于无改动（`git diff f05b6d63..HEAD` 该文件 0 行） |

---

## 二、`runner.py` 的那一处冲突

### 两侧各自改了什么

- **主线侧（ours，`f05b6d63`）**：C02-R1 的 OI 输入链路修复 ——
  `_read_oi_frame` 同读 `ts / open_interest / fetched_at / source`，
  把逐行可得时刻带进状态机（否则 `_oi_slice_as_of` 永远拿不到证据）。
- **隔离目录侧（theirs，backtest-repair-01）**：任务 A 的路径可移植 ——
  `_default_base_dir()`、`_default_parquet_base()`、`_default_runs_base()`、
  `data_paths_report()`，以及任务 B 的 `run_state_machine_simulation` 路径。

### 判定：**互补，不是替代**

两边改的是**不同函数**，没有语义重叠：

- 一个是「数据能不能按当时可得时刻取到」（PIT 证据链）；
- 一个是「数据在哪个目录、路径怎么解析」（可移植性）。

所以冲突块的解法是**两边都保留**，而不是二选一。

### 合并后的可复核证据（`grep -c` on HEAD 的 `runner.py`）

```
_default_base_dir               2 处   ← theirs（路径解析）
data_paths_report               1 处   ← theirs（三路径诊断）
run_state_machine_simulation   10 处   ← theirs（任务 B 路径）
_read_oi_frame                  2 处   ← ours（C02-R1 OI 读取）
fetched_at                      9 处   ← ours（逐行可得时刻）
```

### 一处必须说清的**否定**证据

`oi_preseed_rows` 在 `runner.py` 里是 **0 处** —— 这是**正确的**：
该槽位定义在 `框架/backend/unified_runtime.py`（3 处），
`runner.py` 只负责把 OI 行交给引擎，不持有这个槽位。
**不能因为「runner.py 里搜不到 `oi_preseed_rows`」就判定 C02-R1 没合入** ——
那会把「符号在另一个文件里」误判成「功能缺失」。

---

## 三、`engine_adapter.py` 为什么「零改动」

隔离目录里该文件确实有修改，但三方合并的结果与主线**逐字一致**。
说明那边改的是**主线早已包含的内容**（同一处代码在主线已按同样方式改过），
属于重复实现，不是新功能。

判定依据：`git diff --stat f05b6d63..HEAD -- .../engine_adapter.py` **输出为空**。

---

## 四、被判定为「旧版本残留」而非「成果」的（上一轮已定性，本轮沿用）

| 文件 | 隔离目录里的样子 | 实际身份 |
|---|---|---|
| `unified_runtime.py`（`pr2-delivery-verify-02`） | 少了 `oi_preseed_rows`、少了 `fetched_at` 回填 | C02-R1 修复**之前**的旧版 |
| `accum_early_entry.py`（同上） | 缺 `gap_model` / `same_bar_execution` | 10R 撮合修复**之前**的旧版 |

这两处如果按「谁有改动合谁」处理，会把 C02-R1 与 10R 的修复**直接退回去**。
本轮的处置：**不采纳**，以主线为准。
