# 工单 FACTOR-ENTRY-05｜真实 ATR 重建、评价定义与字段契约说明

**工程路径**：`/Users/boos/Desktop/威科夫因子共振趋势`  
**证据目录**：`evidence/FACTOR-ENTRY-05/`  
**执行角色**：离线量化研究员  

---

## 一、问题背景与根因回溯

### 1.1 问题事实确认
在工单 FACTOR-ENTRY-03 与 FACTOR-ENTRY-04 中报告的「早期走弱 55/205 笔」、「4h 内先不利运动 90/205 笔」以及「0.80 ATR 距箱体距离过滤假设」，存在**严重的底层 ATR 尺度污染问题**，经独立核实，该问题**完全属实**：
- 在 205 笔成交交易中，有 **42 笔（20.49%）** 的真实箱体 ATR（`box_atr_4h`）缺失。
- 代码中的回退逻辑自动触发，将缺失值替换为了 `initial_risk_r`（初始止损距离）：
  ```python
  atr_4h = float(rec.get("box_atr_4h", 0.0))
  if pd.isna(atr_4h) or atr_4h <= 0:
      atr_4h = float(rec.get("initial_risk_r", 0.0))  # <-- 错误回退点
  if pd.isna(atr_4h) or atr_4h <= 0:
      atr_4h = p_entry * 0.03
  ```
- **尺度差异**：
  - 真实 4h Wilder ATR(14) 占入场价的中位比例仅为 **2.60%**；
  - 而 `initial_risk_r`（初始止损距离）占入场价的中位比例高达 **16.86%**；
  - 两者相差 **6.61 倍**！
- **后果**：这 42 笔交易的除数被放大了 4 ~ 8 倍，导致其 4h 下杀深度 $MAE_R$ 被严重低估，$\pm 0.3 \text{ ATR}$ 触发门槛被非正常拉高，造成 **12 ~ 13 笔真正的早期走弱交易被系统性漏判为杂波（CHOPPY）**。原 55 笔和 90 笔统计均建立在有缺陷的尺度之上。

---

## 二、真实 ATR 算法与两套语义隔离契约

为彻底根除混淆，本轮复用正式策略中的原生算法，并严格定义与隔离两套 ATR 语义：

### 2.1 算法规格契约（复用策略原生）
- **指标公式**：Wilder ATR（递推平滑），首根为前 14 根真实波幅（TR）算术平均，后续 $ATR_t = \frac{ATR_{t-1} \times 13 + TR_t}{14}$；
- **计算周期**：14；
- **数据周期**：4 小时 K 线（`4h`）；
- **预热规则**：只使用入场前已闭合的 4h K 线（预热根数全部 $\ge 14$ 根，绝大多数具备 300 ~ 1500 根历史预热）；
- **代码实现**：复用 [`WyckoffMaster.event_trend_v0_1.features.compute_wilder_atr_series`](file:///Users/boos/Desktop/威科夫因子共振趋势/策略/WyckoffMaster/event_trend_v0_1/features.py#L18-L50)。

### 2.2 严格区分两套 ATR 语义

| 语义字段 | 中文定义 | 业务时序与用途 | 取值规则与完整性 |
| :--- | :--- | :--- | :--- |
| **`box_atr_formation`** | **箱体形成时刻冻结 ATR** | 箱体 42 根 4h K 线窗口收盘时的 ATR 值。用于确定箱体几何宽度是否落在 $2A \sim 8A$ 的入选门槛内。 | - 163 笔有原快照记录：直接取自原快照 `box_atr_a`（100% 吻合）；<br>- 42 笔旧成交记录：**因原运行未存盘箱体边界，严格标记为缺失（NaN），绝不回退或捏造**。 |
| **`signal_atr_snapshot`** | **信号/入场时刻动态 4h ATR** | 突破信号发生（或入场触发）时刻，**当时已经闭合的最晚一根 4h K 线的真实 Wilder ATR(14)**。用于度量入场后微观价格运动的归一化尺度（如 4h 路径的 MAE/MFE）。 | - **205 笔成交交易 100% 成功重建**；<br>- 严格遵守 PIT，不含未来数据。 |

---

## 三、微观评价指标数学定义（保持原窗口与阈值不变）

为保证前后对齐的可比性，**本轮严格保持原评价窗口、原阈值与原分类逻辑不变，仅将除数尺子更正为真实重建的 `signal_atr_snapshot`**：

1. **观察窗口**：
   - 4 小时固定观察期：自实际成交 $t_{\text{entry}}$ 起向后连续 16 根 15 分钟 K 线；
   - 24 小时固定观察期：向后连续 96 根 15 分钟 K 线。
2. **微观极值尺度（归一化）**：
   $$\text{MFE}_{R, 4\text{h}} = \frac{\max(H_{1\dots 16}) - P_{\text{entry}}}{\text{signal\_atr\_snapshot}}, \quad \text{MAE}_{R, 4\text{h}} = \frac{P_{\text{entry}} - \min(L_{1\dots 16})}{\text{signal\_atr\_snapshot}}$$
3. **首次运动判定规则（$\pm 0.3 \text{ ATR}$）**：
   - 有利门槛：$P_{\text{fav}} = P_{\text{entry}} + 0.3 \times \text{signal\_atr\_snapshot}$；
   - 不利门槛：$P_{\text{adv}} = P_{\text{entry}} - 0.3 \times \text{signal\_atr\_snapshot}$；
   - 若 $t_{\text{adv}} < t_{\text{fav}}$：记为 `FIRST_ADVERSE`；
   - 若 $t_{\text{fav}} < t_{\text{adv}}$：记为 `FIRST_FAVORABLE`；
   - 若 $t_{\text{adv}} == t_{\text{fav}}$：**显式标记为 `UNKNOWN_SAME_BAR`（同根 15m K 线同时触及，先后未知）**；
   - 若均未触及：记为 `NEITHER_REACHED_0.3ATR`。
4. **入场质量分类规则**：
   - **`EARLY_WEAKNESS`（早期走弱）**：$R_{4\text{h}} < 0$ 且 $\text{MFE}_{R, 4\text{h}} < 0.5$ 且 ($\text{MAE}_{R, 4\text{h}} \ge 0.5$ 或先下杀 `FIRST_ADVERSE`)；
   - **`MFE_PULLBACK`（先涨后回吐）**：全周期 $\text{MFE}_R \ge 1.0$ 且最终交易净盈亏 $\le 0$；
   - **`SUSTAINED_TREND`（持续主升）**：全周期 $\text{MFE}_R \ge 1.5$ 且最终交易净盈亏 $> 0$；
   - **`CHOPPY_OR_OTHER`（杂波/其他）**：其余交易。

---

## 四、核心订正数据汇总

| 指标项目 | 03/04 旧值（错误回退尺子） | 05 订正值（真实 4h Wilder ATR） | 差异与变动原因说明 |
| :--- | :---: | :---: | :--- |
| **早期走弱笔数** | **55 笔** (26.83%) | **67 笔** (32.68%) | **净增 +12 笔**。原 42 笔回退组因分母被放大 6.6 倍导致走弱漏判，换回真实尺子后 13 笔恢复为真实走弱。与 Work 审计完全一致。 |
| **4h 内先不利运动** | **90 笔** (43.90%) | **101 笔** (49.27%) | **净增 +11 笔**。由于真实波动门槛 $\pm 0.3 \text{ ATR}$ 恢复正常宽度，多笔原先被判为“狭窄震荡未触及”的交易显现真实下杀。 |
| **同根先后不明 (未知)** | 19 笔 (9.27%) | 27 笔 (13.17%) | 正常增加 8 笔。阈值恢复真实 ATR 宽度后，同一根 15m K 线波动覆盖 $\pm 0.3 \text{ ATR}$ 的情况增加，严格维持未知标记。 |
| **先不利组实际净盈亏** | — | **+70.51 USDT** | **先不利的那批单子全周期合计是盈利的！彻底推翻“先跌导致亏损”的假想因果！** |
| **非先不利组实际净盈亏** | — | **+31.02 USDT** | 未先出现不利下杀的单子合计盈利反而较低。 |

---

## 五、交付文件与数据契约索引

1. [`atr_source_audit.csv`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/FACTOR-ENTRY-05/atr_source_audit.csv)：227 条全量候选与成交的 ATR 来源、回退标记、放大倍数与预热审计表；
2. [`reconstructed_candidate_features.csv`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/FACTOR-ENTRY-05/reconstructed_candidate_features.csv) / [`.parquet`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/FACTOR-ENTRY-05/reconstructed_candidate_features.parquet)：载有真实重建 `signal_atr_snapshot` 及重算微观路径的特征总表；
3. [`discrepancy_old_vs_new.csv`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/FACTOR-ENTRY-05/discrepancy_old_vs_new.csv)：205 笔成交交易的旧值 vs 新值逐笔比对表（含 14 笔变动详情）；
4. [`discrepancy_summary.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/FACTOR-ENTRY-05/discrepancy_summary.json)：轻量机器可读差异统计；
5. [`audit_and_reconstruct_05.py`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/FACTOR-ENTRY-05/audit_and_reconstruct_05.py)：自包含独立执行脚本。
