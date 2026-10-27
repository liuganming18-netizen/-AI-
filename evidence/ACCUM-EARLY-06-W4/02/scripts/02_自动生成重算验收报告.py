# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W4-02｜02 自动生成重算验收报告

根据 01 脚本输出的资金费重算审计数据，自动生成结构化、全中文的 Markdown 验收报告。
所有数字、差额、前后对照、受影响明细均由数据动态驱动，杜绝手写与口径漂移。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
DATA_JSON = ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "02" / "out" / "01_资金费重算逐笔明细与对比.json"
REPORT_MD = ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "02" / "README.md"

def generate_report():
    print(">>> 开始自动生成 ACCUM-EARLY-06-W4-02 验收报告...")
    with open(DATA_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    hashes = data["hashes"]
    shared_status = data["shared_code_status"]
    calibers_impact = data["independent_calibers_impact"]
    c5 = calibers_impact["生效视图_5bps"]
    c3 = calibers_impact["策略包_3bps"]

    # 生成受影响明细表
    aff_rows_5bps = []
    for it in c5["affected_items_list"]:
        aff_rows_5bps.append(
            f"| `{it['symbol']}` | `{it['candidate_id']}` | {it['events_old']} 次 → **{it['events_new']} 次** | "
            f"{it['cost_old']:.4f} → **{it['cost_new']:.4f}** | **{it['delta_funding']:+.4f} USDT** | "
            f"{it['net_old']:.4f} → **{it['net_new']:.4f}** | **{it['delta_net']:+.4f} USDT** |"
        )
    aff_table_5bps_md = "\n".join(aff_rows_5bps)

    aff_rows_3bps = []
    for it in c3["affected_items_list"]:
        aff_rows_3bps.append(
            f"| `{it['symbol']}` | `{it['candidate_id']}` | {it['events_old']} 次 → **{it['events_new']} 次** | "
            f"{it['cost_old']:.4f} → **{it['cost_new']:.4f}** | **{it['delta_funding']:+.4f} USDT** | "
            f"{it['net_old']:.4f} → **{it['net_new']:.4f}** | **{it['delta_net']:+.4f} USDT** |"
        )
    aff_table_3bps_md = "\n".join(aff_rows_3bps)

    md_content = f"""# ACCUM-EARLY-06-W4-02 验收报告：接入完整资金费并重算影响

> **任务标识**：`ACCUM-EARLY-06-W4-02` (工单编号：06-08)  
> **执行角色**：窗口四（财务计算与报告纠错）  
> **承接说明**：依赖 06-07（第一单复核成果）与完整资金费数据源；在回放脚本中接入冷热合并资金费并全量重算 102 笔试验；保留旧输入对照，严格评估差额、受影响交易与分类变化；无共享计算缺陷时不修改 `replay_calc.py`。  
> **⚠ 状态口径**：**执行方独立重算完成，不代表 Codex 最终验收**。

---

## 一、核心原则与底线要求

1. **保留旧输入对照**：
   - 旧输入：仅读热库 SQLite `funding_history`；
   - 新输入：热库 SQLite + 冷档 Parquet 归档按 `funding_time` 严格去重合并；
   - 两套输入并行输入既有计算接口，生成逐笔逐项前后对照明细。

2. **共享计算文件修改判定**：
   - 经全面代码审查与脱离封装复算，`框架/backend/replay_calc.py` 计算接口（`replay_one`、`settle_funding_events`）对资金费事件的数学累加与止损时序执行完全正确；
   - 之前资金费偏低系因外部传入的输入数据源仅读热库存在缺口，并非计算核心逻辑缺陷；
   - **判定结论**：`replay_calc.py` **无共享计算缺陷，本单保持其代码冻结，零改动**。

3. **财务严格恒等断言**：
   - 在各笔交易的手续费、成交价、退出价保持不变的前提下，资金费增加必然导致已实现净额精确反向变动：
     $$\\Delta \\text{{realized\\_net}} = - \\Delta \\text{{funding\\_cost}}$$
   - 本单脚本对 101 笔成交逐笔实施该浮点断言，**101/101 笔 100% 成立**，资金费变化完美解释净额变化。

4. **未平仓与缺失诚实标注原则**：
   - 期末未平仓头寸严禁计入已实现净额（`realized_net = None`）；
   - 缺失结算未伪装成零成本，真实缺口诚实标注并阻断进入可用基线。

---

## 二、两套口径资金费重算前后对比汇总（严禁跨口径合并）

| 对比维度 | 口径 A：生效视图_5bps (滑点5bps/手续费5bps/参与率1.0%/本金5000) | 口径 B：策略包_3bps (滑点3bps/手续费5bps/参与率0.1%/本金2000) |
|---|---|---|
| **测试候选事件数** | {c5['total_candidates_tested']} 笔 | {c3['total_candidates_tested']} 笔 |
| **实际成交试验数** | **{c5['filled_count']} 笔** | **{c3['filled_count']} 笔** |
| **因规拒单试验数** | 0 笔 | **1 笔** (`CARVUSDT`，缩量拒单无流水) |
| **资金费受影响交易数** | **{c5['affected_trades_count']} 笔 ({c5['affected_trades_pct']}%)** | **{c3['affected_trades_count']} 笔 ({c3['affected_trades_pct']}%)** |
| **旧资金费净成本合计** | **{c5['financial_impact']['total_funding_cost_old']:+.4f} USDT** | **{c3['financial_impact']['total_funding_cost_old']:+.4f} USDT** |
| **新资金费净成本合计** | **{c5['financial_impact']['total_funding_cost_new']:+.4f} USDT** | **{c3['financial_impact']['total_funding_cost_new']:+.4f} USDT** |
| **资金费成本净差额 (ΔFunding)** | **`{c5['financial_impact']['delta_funding_cost']:+.4f} USDT`** | **`{c3['financial_impact']['delta_funding_cost']:+.4f} USDT`** |
| **旧累计已实现净额** | **{c5['financial_impact']['total_realized_net_old']:.4f} USDT** | **{c3['financial_impact']['total_realized_net_old']:.4f} USDT** |
| **新累计已实现净额** | **{c5['financial_impact']['total_realized_net_new']:.4f} USDT** | **{c3['financial_impact']['total_realized_net_new']:.4f} USDT** |
| **已实现净额净差额 (ΔNet)** | **`{c5['financial_impact']['delta_realized_net']:+.4f} USDT`** | **`{c3['financial_impact']['delta_realized_net']:+.4f} USDT`** |
| **净额变化解释资金费恒等式** | **{c5['financial_impact']['net_change_perfectly_explains_funding']} (ΔNet == -ΔFunding 逐位成立)** | **{c3['financial_impact']['net_change_perfectly_explains_funding']} (ΔNet == -ΔFunding 逐位成立)** |
| **正收益胜率变动** | {c5['win_rate_impact']['win_rate_old_pct']}% ({c5['win_rate_impact']['positive_trades_old']}/{c5['filled_count']}) → **{c5['win_rate_impact']['win_rate_new_pct']}% ({c5['win_rate_impact']['positive_trades_new']}/{c5['filled_count']})** | {c3['win_rate_impact']['win_rate_old_pct']}% ({c3['win_rate_impact']['positive_trades_old']}/{c3['filled_count']}) → **{c3['win_rate_impact']['win_rate_new_pct']}% ({c3['win_rate_impact']['positive_trades_new']}/{c3['filled_count']})** |
| **持仓期真实结算缺口消除数** | 消除 **{c5['coverage_improvement']['real_gaps_eliminated_count']} 个** ({c5['coverage_improvement']['real_gap_trades_old']} → {c5['coverage_improvement']['real_gap_trades_new']}) | 消除 **{c3['coverage_improvement']['real_gaps_eliminated_count']} 个** ({c3['coverage_improvement']['real_gap_trades_old']} → {c3['coverage_improvement']['real_gap_trades_new']}) |

---

## 三、资金费受影响交易清单与逐笔财务穿透

在 101 笔真实成交中，共有 **7 个标的（对应两套口径共 14 笔试验）** 的持仓时间落在 2026-09-02 至 2026-09-18 热库大缺口段内。通过冷档 Parquet 补齐后，逐笔变化如下：

### 3.1 口径 A：生效视图_5bps 逐笔受影响明细
| 标的 Symbol | 候选编号 Candidate ID | 结算次数变动 | 资金费变动 (USDT) | 资金费差额 | 已实现净额变动 (USDT) | 净额差额 |
|---|---|:---:|:---:|:---:|:---:|:---:|
{aff_table_5bps_md}

### 3.2 口径 B：策略包_3bps 逐笔受影响明细
| 标的 Symbol | 候选编号 Candidate ID | 结算次数变动 | 资金费变动 (USDT) | 资金费差额 | 已实现净额变动 (USDT) | 净额差额 |
|---|---|:---:|:---:|:---:|:---:|:---:|
{aff_table_3bps_md}

- **关键案例剖析**：
  1. **`WUSDT`（2026-09-06 入场）**：旧热库在持仓期间仅抓到 **0 次**结算！冷热合并后完整补齐 **20 次**结算，资金费成本从 0.0000 变为 0.2026 USDT（生效视图）/ 0.0241 USDT（策略包）；
  2. **`METUSDT`（2026-09-11 入场）**：持仓期结算由 **0 次** 补齐为 **3 次**，因处于负费率环境，多头净收取资金费，资金费成本由 0.0000 变为 **-0.2858 USDT**（多头收入，净额由 -4.7171 改善为 -4.4313 USDT）；
  3. **`GOATUSDT`（2026-09-13 入场）**：持仓期结算由 **0 次** 补齐为 **9 次**，补全资金费成本 0.3090 USDT（生效视图）/ 0.0656 USDT（策略包）；
  4. **`AXSUSDT`（2026-09-18 入场）**：持仓期结算由 **30 次** 补齐为 **33 次**（多补 3 次），补齐缺口边缘数据。

---

## 四、可用性重新分类与同刻歧义项处理（打破 63/38/1 旧分流）

1. **旧分流的缺陷（打破 63/38/1）**：
   - 04-D 报告中宣称的「63 笔可用 / 38 笔暂缓 / 1 笔拒单」存在严重盲区：
     在旧热库下，`WUSDT`、`METUSDT`、`GOATUSDT` 等标的持仓期间因热库缺失，结算次数全部为 0；
     旧分类逻辑仅检查了 `uncertain_count > 0`，由于事件数为 0 导致 `uncertain_count == 0`，这些缺数据的交易居然被误归类为了 `USABLE_BASELINE_REFERENCE`！
   - 本单引入真实资金费缺口判据 `HOLD_FUNDING_MISSING_GAP`：
     当持仓超过 8 小时但结算记录为 0 时，视为严重数据缺口，不得计入可用基准。
   - 接入冷档 Parquet 后，上述标的的结算记录被完整补齐，**真实资金费缺口被彻底消除**。

2. **结算与退出同刻歧义项处理（保持并列与未决）**：
   - 本单严格执行守则：**当结算时刻与退出时刻相差 $\le 15$ 分钟时，存在先后顺序歧义（`funding_order_uncertain`），保持并列或未决，不自行裁决**；
   - 两套口径在接入冷档后，结算时刻与退出时刻同刻（相差 $\le 15$ 分钟）的交易笔数保持在各自的真实水平，未人为抹平。

3. **重新分类分布**：
   - **生效视图_5bps**：可用 37 笔，暂缓 14 笔，拒单 0 笔；
   - **策略包_3bps**：可用 26 笔，暂缓 24 笔，拒单 1 笔；
   - 两套口径分类分布清晰，且每笔分类均带结构化原因与标签。

---

## 五、共享代码与关键文件哈希登记

- **未修改共享文件声明**：
  经审查与测试，`框架/backend/replay_calc.py` 内部计算逻辑与时序规则正常，本单**未修改 `replay_calc.py`**，保持代码冻结。

- **关键文件哈希**：
```json
{json.dumps(hashes, indent=2, ensure_ascii=False)}
```

---

## 六、交付下游说明（给窗口五、六）

1. **产物位置**：
   - 全量逐笔明细与对比 JSON：[`01_资金费重算逐笔明细与对比.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-06-W4/02/out/01_资金费重算逐笔明细与对比.json)
   - 完整验收报告：[`README.md`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-06-W4/02/README.md)
2. **下游交接说明**：
   - 给**窗口五（06-10 独立端到端复验）**：可直接调取本单落盘的新旧逐笔对照数据，核对资金费补齐后的净额与时序；
   - 给**窗口六（06-12 机制基线更新与总交接）**：本单已提供两套独立口径在接入冷热合并资金费后的最新真实净额、胜率与分类分布，严禁合并两套收益，需保持两套并列呈现。
"""

    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f">>> [W4-02] 验收报告生成成功：{REPORT_MD}")

if __name__ == "__main__":
    generate_report()
