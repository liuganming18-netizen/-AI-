# 工单 FACTOR-ENTRY-04｜03样本对账与分类证据复算报告

**工程路径**：`/Users/boos/Desktop/威科夫因子共振趋势`  
**证据目录**：`evidence/FACTOR-ENTRY-04/`  
**状态**：完成离线隔离复核，数据 100% 逐笔闭合，等待 Codex 独立验收。

---

## 一、227 与 205 样本对账推导与闭合证明

### 1.1 核心对账结论
- **227 条记录**：威科夫共振状态机（`EventTrendStateMachine`）在 152 个官方加密币全生命周期中扫描出的**全部候选事件**（`CandidateSnapshot`）。
- **205 笔记录**：共享撮合内核在候选触发后执行入场、成功通过开仓闸门并产生账本头寸的**实际成交交易**（`FILLED`）。
- **多出的 22 条记录**：候选事件生成后，在入场执行点（紧随突破后的第一个 15m K 线开盘）触发了 `EventTrendExecutionPolicy` 内置的准入风控闸门，被引擎**取消挂单（`ENTRY_CANCELED`）**，**从未产生实际成交，未进入账本**。

$$205 \text{ 笔成交交易 (FILLED)} + 22 \text{ 笔闸门取消 (UNFILLED\_CANCELED)} = 227 \text{ 笔候选事件 (100\% 逐笔闭合)}$$

### 1.2 22 笔未成交候选的精确取消原因分布

经调取回测撮合引擎底层日志与 `ValidationResult`，22 笔未成交事件的取消原因完全闭合为两类策略内置的入场保护规则，无任何异常遗漏或系统丢失：

| 撤单规则类型 | 规则逻辑与物理意义 | 笔数 | 占比 | 典型样例 (Symbol @ UTC) |
| :--- | :--- | :---: | :---: | :--- |
| **`canceled_gap_above_0.5a`** | 开盘价高于突破 1h 收盘价 $+0.5 \text{ ATR}$（防过度追高跳空） | **13** | 59.09% | `COTIUSDT` @ 2021-12-23 03:00 (open=0.40545 > max=0.40471)<br>`GTCUSDT` @ 2022-04-01 18:00 (open=8.603 > max=8.524) |
| **`canceled_fell_back_into_box`** | 开盘价跌破箱体上沿 $H$（假突破夭折，开盘即跌回箱体） | **9** | 40.91% | `ENSUSDT` @ 2022-04-03 15:00 (open=20.428 < H=20.543)<br>`SOLUSDT` @ 2022-09-08 15:00 (open=33.45 < H=33.58) |
| **合计** | **全部由执行策略开仓闸门拦截** | **22** | 100.0% | 详见 `reconciliation_227_to_205.csv` |

> [!NOTE]
> **旧值订正说明**：在前序工单 FACTOR-ENTRY-01 的初版导出中，因仅抓取了策略挂单拒绝表（`strat_rej_map`），未合并撮合适配器 `ValidationResult.execution_canceled_reason`，导致这 22 笔的取消原因字段显示为空并统称为 `UNFILLED_TIMEOUT`。本次 FACTOR-ENTRY-04 已从撮合底层重新提取完整取消原因并记录于 `unfilled_reasons.json` 与 `reconciliation_227_to_205.csv`。

---

## 二、入场质量评价定义与复算证据

为科学度量入场买点质量，消除二分类的主观性，本轮提供前向连续收益、固定窗口微观极值路径，并严格复算前序统计。

### 2.1 评价指标精确数学定义

1. **观察窗口与时间对齐**：
   - 入场基准时刻 $t_{\text{entry}}$：对于 205 笔成交交易，取撮合引擎实际成交时间戳（毫秒）；
   - 4 小时固定观察窗口：自 $t_{\text{entry}}$ 起向后连续 16 根 15 分钟 K 线（包含入场当根及后续 15 根）；
   - 24 小时固定观察窗口：自 $t_{\text{entry}}$ 起向后连续 96 根 15 分钟 K 线。

2. **基准价格与波动率基准（ATR）**：
   - 价格基准 $P_{\text{entry}}$：实际成交均价（包含滑点）；
   - 波动率基准 $\text{ATR}_{4\text{h}}$：信号触发时状态机计算的 Wilder 4h ATR（`box_atr_4h`）；若缺失则回退到初始风险 $R$ 或价格的 3%。

3. **前向离散收益率（连续值）**：
   $$R_{15\text{m}} = \frac{C_{1} - P_{\text{entry}}}{P_{\text{entry}}}, \quad R_{1\text{h}} = \frac{C_{4} - P_{\text{entry}}}{P_{\text{entry}}}, \quad R_{4\text{h}} = \frac{C_{16} - P_{\text{entry}}}{P_{\text{entry}}}$$
   其中 $C_k$ 为入场后第 $k$ 根 15m K 线的收盘价。

4. **固定窗口最大有利/不利推进（MAE / MFE）**：
   $$\text{MFE}_{\text{pct}} = \frac{\max(H_{1\dots N}) - P_{\text{entry}}}{P_{\text{entry}}}, \quad \text{MAE}_{\text{pct}} = \frac{P_{\text{entry}} - \min(L_{1\dots N})}{P_{\text{entry}}}$$
   $$\text{MFE}_{R} = \frac{\max(H_{1\dots N}) - P_{\text{entry}}}{\text{ATR}_{4\text{h}}}, \quad \text{MAE}_{R} = \frac{P_{\text{entry}} - \min(L_{1\dots N})}{\text{ATR}_{4\text{h}}}$$
   其中 $N=16$（4h 窗口）或 $N=96$（24h 窗口）。

5. **首次极值运动方向判定（$\pm 0.3 \text{ ATR}$ 阈值）**：
   - 有利触发阈值：$P_{\text{fav}} = P_{\text{entry}} + 0.3 \times \text{ATR}_{4\text{h}}$，首根达到的时间戳记为 $t_{\text{fav}}$；
   - 不利触发阈值：$P_{\text{adv}} = P_{\text{entry}} - 0.3 \times \text{ATR}_{4\text{h}}$，首根达到的时间戳记为 $t_{\text{adv}}$；
   - **先后次序规则**：
     - 若 $t_{\text{adv}} < t_{\text{fav}}$：记为 `FIRST_ADVERSE`（先不利下杀）；
     - 若 $t_{\text{fav}} < t_{\text{adv}}$：记为 `FIRST_FAVORABLE`（先有利推进）；
     - **严谨未知处理**：若 $t_{\text{adv}} == t_{\text{fav}}$（即在同一根 15m K 线内既突破了 $+0.3\text{ ATR}$ 又跌破了 $-0.3\text{ ATR}$，因 15m 内部 tick 顺序不可知）：**显式标记为 `UNKNOWN_SAME_BAR`，绝不主观编造先后顺序**；
     - 若两者均未触发：记为 `NEITHER_REACHED_0.3ATR`。

### 2.2 205 笔成交交易的复算结果对账

#### A. 4 小时内首次极值方向复算（核对 90/205）：
| 首次极值方向类别 | 笔数 | 占比 | 判定逻辑说明 |
| :--- | :---: | :---: | :--- |
| **`FIRST_ADVERSE`** | **90** | **43.90%** | **4h 观察期内先下杀跌破 $-0.3\text{ ATR}$（完全复现原 90 笔）** |
| `FIRST_FAVORABLE` | 68 | 33.17% | 4h 观察期内先向上突破 $+0.3\text{ ATR}$ |
| `NEITHER_REACHED_0.3ATR` | 28 | 13.66% | 4h 观察期内波动狭窄，两端均未触及 |
| **`UNKNOWN_SAME_BAR`** | **19** | **9.27%** | **同根 15m K 线内双向触及，严格保持未知标记** |
| **合计** | **205** | **100.0%** | 窗口完整性：205/205 具备完整 16 根 15m K 线 |

#### B. 入场质量解耦分类复算（核对 55/205）：
```
EARLY_WEAKNESS (早期走弱/接盘) 判定条件：
  exec_status == "FILLED" 且
  fwd_ret_4h < 0 且
  mfe_r_4h < 0.5 且
  (mae_r_4h >= 0.5 或 first_move_4h == "FIRST_ADVERSE")
```
| 入场质量类别 | 笔数 | 占比 | 均值 $R_{4\text{h}}$ | 均值 $\text{MAE}_{R,4\text{h}}$ | 交易净盈亏合计 |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **`EARLY_WEAKNESS`** | **55** | **26.83%** | **-2.84%** | **1.08 ATR** | **-717.38 USDT** |
| `CHOPPY_OR_OTHER` | 103 | 50.24% | -0.12% | 0.44 ATR | -123.60 USDT |
| `SUSTAINED_TREND` | 39 | 19.02% | +5.76% | 0.28 ATR | +1071.22 USDT |
| `MFE_PULLBACK` | 8 | 3.90% | +1.89% | 0.62 ATR | -128.71 USDT |
| **全样本成交** | **205** | **100.0%** | **+0.37%** | **0.58 ATR** | **+101.53 USDT** |

> **复算核验闭合**：55 笔 `EARLY_WEAKNESS` 与 90 笔 `FIRST_ADVERSE` 经 `audit_and_reconcile_04.py` 复算 100% 精确闭合，且全量逐笔明细已输出至 `candidate_evaluations_audit.csv`。

---

## 三、因果推论收紧与科学边界

在 FACTOR-ENTRY-03 中曾提出“突破K线脉冲过猛、买点脱离箱体过远是导致入场即弱的核心原因”。本工单严格按照科学计量标准，将其**收紧为统计相关性描述**，明确数据支持与不支持的边界：

### 3.1 数据支持的事实（统计相关性）
1. **经验相关性显著**：
   - 买价距箱体上沿 ATR 距离（`breakout_atr_dist`）与 4h 下杀深度（`mae_r_4h`）呈现 $+0.4278$ 的正相关；
   - 突破 K 线 1h 涨幅（`bar_return_1h`）与 4h 前向收益（`fwd_ret_4h`）呈现 $-0.2155$ 的负相关。
2. **分组单调性显著**：
   - 按照 `breakout_atr_dist` 分为低、中、高三分位数组，高分组的 `EARLY_WEAKNESS` 发生率高达 45.9%，显著高于低分组的 14.8%。

### 3.2 数据不支持的断言（禁止上升为单一因果）
1. **不得断言“脱离箱体本身导致了价格下跌”**：
   - 统计相关性无法排除**第三外生共因（Confounder）**。例如：在大盘系统性急剧去杠杆、BTC 跳水、或市场瞬时流动性枯竭时，往往伴随着假突破长阳和随后的猛烈下杀。脱离箱体和早期走弱可能同时是宏观剧烈波动的表征，而非前者因果决定后者。
2. **分类统计不能作为原因已证实**：
   - 事后对 55 笔失败买点进行特征聚类，属于描述性统计，不构成实验心理学或物理意义上的因果证明。

---

## 四、探索性假设保留声明

FACTOR-ENTRY-03 中提出的两个过滤假设：
- **假设 1**：买价距箱体上沿距离 $\le 0.80 \text{ ATR}$；
- **假设 2**：突破 1h K 线涨幅 $\le 4.5\%$。

### 4.1 产生背景与缺陷交代
1. **阈值来源**：这两个阈值直接来源于对已见全样本（181 笔共同样本）的三分位数（Tertiles）截断点观察（前 33% 分位点约在 0.80 ATR 与 4.5% 附近）。
2. **严禁提拔为正式策略规则**：
   - 这是典型的**样本内后验提炼（In-sample Hindsight）**；
   - 目前工程内**不存在未参与过特征分析的独立留出盲测数据集**；
   - 将现有样本重新按年份或时间切分，本质上仍属于“偷看全貌后的重切分”，不能赋予其独立样本的验证效力。
3. **本轮冻结承诺**：
   - 本轮不修改正式策略代码；
   - 不在正式策略中写入 0.80 ATR 或 4.5% 的硬过滤门槛；
   - 保持其作为**待未来真实市场检验的科学探索假设**。

---

## 五、交付物索引

所有交付文件均严格限定在 `evidence/FACTOR-ENTRY-04/` 内，Work 窗口二可凭原始输入独立核验：

1. `reconciliation_227_to_205.csv` / `.json`：227 候选到 205 成交的逐笔闭合映射表；
2. `candidate_evaluations_audit.csv` / `.parquet`：205 成交交易的完整连续值与微观极值时间戳评价表；
3. `reconciliation_summary.json`：对账数量、未成交原因与分类结果的轻量摘要；
4. `audit_and_reconcile_04.py`：完全自包含、无任何外部未见依赖的独立复算脚本；
5. `reproduce.sh`：一键复现脚本；
6. `code_fingerprints.json`：代码与输入基准指纹。
