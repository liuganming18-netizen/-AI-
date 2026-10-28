# NATURAL-CANDIDATE-01｜自然零候选全景穿透排查专项报告

- **日期**：2026-10-27（北京时间）
- **项目**：`/Users/boos/Desktop/威科夫因子共振趋势`
- **执行方**：反重力
- **证据目录**：`evidence/NATURAL-CANDIDATE-01/`
- **代码与规则基线**：
  - 核心状态机：`策略/WyckoffMaster/event_trend_v0_1/state_machine.py`
  - 信号运行时：`框架/backend/unified_runtime.py`
  - 冻结策略规则：`evidence/WX_EVENT_TREND_V0_1_策略与回测冻结规则.md`
- **只读审计原则**：全程使用独立探针脚本重演全池 526 标的 48 天回放，零改动生产代码、零写入生产库。

---

## 一、核心结论概要 (Executive Summary)

针对系统在 48 天历史回放中产生 **BOX 10,085 次、SWEEP 2,542 次、RETEST 1,560 次，但 CANDIDATE 彻底为 0** 的“自然零候选”现象，经过对全池 526 标的 1,560 次 `RETEST` 事件的逐笔穿透与状态轨迹分析，**已确凿定位其三大根本原因**：

1. **第一重致命杀手：超窄止损（扫死 91.9% 的候选苗头）**
   - 规则与代码在回踩确认后，将止损失效位硬编码设定为 `Lr - 0.1 * A`（`initial_stop_buffer_atr = 0.1`）。
   - 在 1h K 线级别上，`0.1 * ATR` 对应现货/合约价格的容差仅为 **0.05% ~ 0.25%**（以 BTC 60000 计仅约 50~90 美元）。
   - **实测数据**：在全部 1,560 次 `RETEST` 中，有 **1,433 次（91.86%）** 在确认回踩后仅中位数 **6 小时**（平均 22.45 小时）内，直接被正常的市场呼吸震荡击穿该超窄止损线，触发 `INVALIDATED` 夭折。
2. **第二重结构阻碍：跨越大箱体的高难度（超时 3.3%）**
   - 经典 V0.1 属于大箱体突破模式（Box Breakout）。回踩点 `Lr` 位于箱体最下沿 `L` 附近，而确认突破要求从最底部一路涨穿大箱体上沿 `H + 0.1 * A`（大箱体宽度要求为 `2A ~ 8A`，通常对应 5% ~ 20% 以上的跨度）。
   - 在必须保持“绝对不跌破 0.1A 超窄止损”的前提下，要求在 7 天内走完这一整段大波段单边上涨，统计概率极低。
   - **实测数据**：有 **51 次（3.27%）** 因 7 天内无法跨越整个大箱体而触发 `7_DAYS_TIMEOUT`（`EXPIRED`）。
3. **第三重门禁绝杀：3a 臂的 OI 门槛将仅存的突破“团灭”（拦截 3.4%）**
   - 在全池 1,560 次回踩中，历经千难万险成功跨越大箱体并首次收盘涨破 `H + 0.1 * A` 的突破尝试，全池 48 天内**仅有 62 次（3.97%）**。
   - 在这 62 次中：
     - **9 次** 成交量不足 1.5 倍 V20，被 `VOLUME_REJECTED` 拒绝；
     - **53 次** 成交量已达标（>= 1.5 倍 V20）！但在 3a 开启 `enable_oi_filter=True` 时，由于历史回放期缺乏原生实时逐行 PIT OI 数据或 6h 增长率未达标，被 `OI_UNAVAILABLE` **100% 团灭**！
   - **关键事实（3b 方案的潜在候选）**：根据冻结规则，**3b 方案仅关闭 OI 门槛**。这 **53 笔** 真实突破在 3b 方案下将**全部直接转化为合格的 CANDIDATE**！

4. **为什么早期累积方案（4a/4b/4c）有 289 条候选，而经典 V0.1 为 0？**
   - **4a/4b/4c（早期入场）**：入场门槛是**局部压制位（Local Resistance）**，局部压制位位于 `L` 上方仅 0.5A~1.5A 处，只需局部站稳即可生成候选（生成 105 / 87 / 97 条）；
   - **经典 V0.1（大箱体突破）**：要求必须跨越 2A~8A 的全局大箱体，并穿越全局上沿 `H + 0.1A`，路径要求极为严苛。

---

## 二、量化穿透全景台账

### 1. 全生命周期漏斗分布（48 天全池 526 标的）

| 状态机环节 | 状态迁移次数 | 环比转化率 | 累计转化率 | 核心流失原因 |
| :--- | :---: | :---: | :---: | :--- |
| **BOX**（箱体建档） | 10,085 | 100.0% | 100.0% | 4h 连续 42 根，宽度 2~8A，ER <= 0.3 |
| **SWEEP**（扫低回收） | 2,542 | 25.21% | 25.21% | 破 L 不超 0.5A，同根收回 L+0.1A |
| **RETEST**（缩量回踩） | 1,560 | 61.37% | 15.47% | 在 L~L+0.5A 高于 S 且缩量收阳 |
| **突破尝试**（上穿 H+0.1A） | 62 | **3.97%** | 0.61% | 绝大多数被 `Lr - 0.1A` 止损震荡出局 |
| **量能达标**（Vol >= 1.5*V20） | 53 | 85.48% | 0.53% | 仅 9 笔量能不足 |
| **CANDIDATE（3a 臂：含 OI）** | **0** | **0.00%** | **0.00%** | 53 笔全部被 `OI_UNAVAILABLE` 拦截 |
| **CANDIDATE（3b 臂：无 OI）** | **53** | **100.00%** | **0.53%** | 关闭 OI 门槛，53 笔全部成立 |

### 2. 1,560 次 RETEST 事件最终终态明细

| 终结状态代码 | 子原因分类 | 事件数量 (次) | 占比 (%) | 状态说明 |
| :--- | :--- | :---: | :---: | :--- |
| **INVALIDATED** | `BREAK_LR_STOP` | **1,433** | **91.86%** | 跌破 `Lr - 0.1*A` 超窄止损线 |
| **OI_UNAVAILABLE** | `OI_DATA_UNAVAILABLE` | **53** | **3.40%** | 突破成功且量能达标，但 OI 不可用 |
| **EXPIRED** | `7_DAYS_TIMEOUT` | **51** | **3.27%** | 回踩后 7 天内未发生突破 |
| **RETEST** | `STILL_IN_RETEST` | **14** | **0.90%** | 截至当前观测时点仍在观察中 |
| **VOLUME_REJECTED** | `VOLUME_LT_1_5_V20` | **9** | **0.58%** | 突破根成交量不足 1.5 倍 V20 中位数 |
| **总计** | — | **1,560** | **100.0%** | 全池 526 标的 48 天回放完整覆盖 |

### 3. RETEST 事件存活时长统计

- **样本总量**：1,560 次
- **平均存活时间**：`22.45` 小时
- **中位数存活时间**：`6.00` 小时
- **90分位数 (P90)**：`74.00` 小时
- **最短存活**：`0.24` 小时（确认后下一根 1h 立即被刺穿止损）
- **最长存活**：`169.00` 小时（顶格触碰 7 天超时被强制归档）

---

## 三、代码逻辑缺陷与物理机制剖析

### 1. 超窄止损设计缺陷 (`state_machine.py:503`)

```python
# 策略/WyckoffMaster/event_trend_v0_1/state_machine.py
# 失效检查: low < Lr - 0.1A 或 连续两根收盘 < L
if low_p < (event.retest_low_lr - self.config.initial_stop_buffer_atr * A) or self.consecutive_closes_below_l >= 2:
    self._terminate_event(
        event, EventState.INVALIDATED, close_time_ms,
        "跌破Lr-0.1A或连续2根收盘破L",
        {"low": low_p, "lr_limit": event.retest_low_lr - self.config.initial_stop_buffer_atr * A}
    )
    return None
```
- **参数硬编码**：`config.initial_stop_buffer_atr = 0.1`。
- **物理现实矛盾**：
  - 威科夫理论中，回踩（Secondary Test / Retest）是吸筹区间下沿的二次确认，在此之后价格往往会在箱体内反复拉锯筑底；
  - 但代码赋予回踩的容错区间仅有 `0.1 * A`（0.1 个 ATR）。在 1 小时周期上，加密货币现货与永续合约的单根 K 线正常随机波动（甚至由买卖盘点差引起的小刺针）幅度通常就在 0.1A~0.3A；
  - 这种“超窄止损”导致 91.9% 的正常筑底过程在 6 小时内直接被判定为“结构失效”，根本没有机会发起向上的大波段攻击。

### 2. 跨越大箱体的路径阻碍与 7 天超时 (`state_machine.py:495`)

```python
# 超时检查: retest 之后 7 天
if close_time_ms - event.retest_time_ms > self.config.breakout_max_hours_after_retest * 3600 * 1000:
    self._terminate_event(
        event, EventState.EXPIRED, close_time_ms,
        "回踩后7天内未发生突破", {"elapsed_hours": (close_time_ms - event.retest_time_ms) / 3600000}
    )
    return None
```
- **矛盾机制**：从箱体下沿 `L` 启动，想要突破箱体上沿 `H + 0.1A`，必须完整走完 `2.0A ~ 8.0A` 的全程。
- 如果市场处于宽幅震荡（例如箱体宽度 5A~6A），价格在 7 天内往往只会在箱体中轨徘徊（实测平均反弹幅度仅达到箱体宽度的 35%~50%），很难在 168 小时内完成单边贯穿，从而被超时清退（3.3%）。

### 3. OI 门槛在离线/历史环境中的单点阻塞 (`state_machine.py:585-593`)

```python
if self.enable_oi_filter:
    if oi_status not in ("ok", OI_STATUS_ASSUMED) or oi_growth is None:
        self._terminate_event(
            event, EventState.OI_UNAVAILABLE, close_time_ms,
            f"OI数据缺失或不可用({oi_status})",
            {"oi_status": oi_status, "oi_curr": oi_curr, "oi_past": oi_past,
             "pit_detail": oi_res.get("detail", "")}
        )
        return None
```
- 在全池 1,560 次事件中，历经严苛筛选成功突破 `H + 0.1A` 且量能达标的 **53 笔优质突破**，在 3a 臂上遭遇了 100% 的 `OI_UNAVAILABLE` 阻断。
- **53 笔突破标的抽样举例**：
  - `1000CATUSDT` (2026-07-03 突破，存活 60h，箱体宽 5.84A)
  - `AXSUSDT` (2026-09-18 突破，存活 30h，箱体宽 4.88A)
  - `BABYUSDT` (2026-09-18 突破，存活 29h，箱体宽 4.11A)
  - `BBUSDT` (2026-10-11 突破，存活 61h，箱体宽 5.54A)
  - `BLURUSDT` (2026-08-20 突破，存活 49h，箱体宽 6.61A)
  - `CARVUSDT` (2026-09-19 突破，存活 65h，箱体宽 5.97A)
- **结论**：若策略以 **3b 臂（关闭 OI 门槛）** 规则运行，这 **53 笔将直接转化为实打实的 CANDIDATE**。

---

## 四、对比启示：为什么 4a/4b/4c 能够顺利产出 289 条候选？

| 维度 | 经典 V0.1 方案（大箱体突破） | 早期累积方案 4a/4b/4c（局部压制位确认） |
| :--- | :--- | :--- |
| **确认目标位** | 全局大箱体上沿 `H + 0.1 * A` | 局部压制位 `local_resistance`（仅高出下沿 0.5A~1.5A） |
| **行进距离要求** | 必须跨越完整大箱体（`2A ~ 8A`） | 只需越过局部短期阻力（`0.5A ~ 1.5A`） |
| **止损失效机制** | 回踩低点 `Lr - 0.1 * A`（极易被扫） | 跌回箱体下沿 L 或连续破位（容差大） |
| **时效要求** | 7 天内必须暴涨至箱体上沿 | 当根或下一根收盘确认即可 |
| **48 天候选产出** | **0 笔**（3a 臂） / **53 笔**（3b 臂） | **289 笔**（4a=105, 4b=87, 4c=97） |

---

## 五、产物与清单

1. **执行探针**：`evidence/NATURAL-CANDIDATE-01/scripts/probe_retest_full_funnel.py`
2. **汇总台账**：`evidence/NATURAL-CANDIDATE-01/out/retest_funnel_summary.json`
3. **逐笔明细**：`evidence/NATURAL-CANDIDATE-01/out/retest_events_detail.csv`（全量 1,560 行，含逐笔标的、时间、箱体参数、终态、子原因、存活时长等）
4. **主分析报告**：`evidence/NATURAL-CANDIDATE-01/README.md`
