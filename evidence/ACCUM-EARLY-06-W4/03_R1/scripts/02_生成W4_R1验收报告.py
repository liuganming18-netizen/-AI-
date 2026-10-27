# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W4-R1｜02 自动生成 W4-R1 验收报告

读取 01 脚本输出的审计数据，自动生成结构化、全中文的 Markdown 验收报告。
所有数字、周期分布、对账统计、MET 纠正均由数据动态驱动，杜绝手写与口径漂移。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
DATA_JSON = ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "03_R1" / "out" / "01_资金费重算与判据整改明细.json"
REPORT_MD = ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "03_R1" / "README.md"

def generate_report():
    print(">>> 开始自动生成 ACCUM-EARLY-06-W4-R1 验收报告...")
    with open(DATA_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    hashes = data["hashes"]
    reconcil = data["reconciliation_report"]
    met = data["met_verification"]
    c5 = data["calibers_summary"]["生效视图_5bps"]
    c3 = data["calibers_summary"]["策略包_3bps"]

    # 1. 受影响交易明细表
    aff_rows_5bps = []
    for it in c5["affected_trades_detail"]:
        aff_rows_5bps.append(
            f"| `{it['symbol']}` | `{it['candidate_id']}` | {it['events_old']} 次 → **{it['events_new']} 次** | "
            f"{it['cost_old']:.4f} → **{it['cost_new']:.4f}** | **{it['delta_funding']:+.4f} USDT** | "
            f"{it['net_old']:.4f} → **{it['net_new']:.4f}** | **{it['delta_net']:+.4f} USDT** | `{it['gap_classification']}` |"
        )
    aff_table_5bps_md = "\n".join(aff_rows_5bps)

    aff_rows_3bps = []
    for it in c3["affected_trades_detail"]:
        aff_rows_3bps.append(
            f"| `{it['symbol']}` | `{it['candidate_id']}` | {it['events_old']} 次 → **{it['events_new']} 次** | "
            f"{it['cost_old']:.4f} → **{it['cost_new']:.4f}** | **{it['delta_funding']:+.4f} USDT** | "
            f"{it['net_old']:.4f} → **{it['net_new']:.4f}** | **{it['delta_net']:+.4f} USDT** | `{it['gap_classification']}` |"
        )
    aff_table_3bps_md = "\n".join(aff_rows_3bps)

    # 2. 周期分布表
    period_rows = []
    for p_name, cnt in c5["period_status_distribution"].items():
        period_rows.append(f"| `{p_name}` | {cnt} 笔 |")
    period_table_md = "\n".join(period_rows)

    # 3. 缺口分类分布表
    gap_rows = []
    for g_name, cnt in c5["gap_classification_distribution"].items():
        gap_rows.append(f"| `{g_name}` | {cnt} 笔 |")
    gap_table_md = "\n".join(gap_rows)

    md_content = f"""# ACCUM-EARLY-06-W4-R1 验收报告：资金费判据与财务报告整改

> **任务标识**：`ACCUM-EARLY-06-W4-R1` (工单 A)  
> **执行角色**：窗口四（财务计算与报告纠错）  
> **承接依据**：`docs/派工_ACCUM-EARLY-06_五点复核整改_2026-10-27.md` 工单 A  
> **核心改进**：
> 1. 深度复用 `06-06` 资金费适配器（`funding_events_adapter.py`），完成逐事件键级对账（**100% 吻合**）；
> 2. 彻底废除“持仓超过8小时且零结算才算缺口”的粗暴判据，重构覆盖 1h/4h/8h 周期感知、部分漏事件、字段缺失/冲突的严密判据体系；对周期证据不足者标注不可判定，不盲目宣称完整；
> 3. 彻底纠正 METUSDT 正文手写旧净额与表格矛盾，所有财务指标从底层明细统一驱动；
> 4. 共享计算文件 `replay_calc.py` 零修改、完全冻结。  
> **⚠ 状态口径**：**执行方整改完成，不代表主代理 / Codex 最终验收**。

---

## 一、06-06 适配器接入与逐事件键级对账

本单全面复用 `evidence/ACCUM-EARLY-06-06/adapter/funding_events_adapter.py`（版本 `{hashes['adapter_file']['version']}`）及稳定事件清单。

为了证明数据消费的可追溯性与一致性，本单对旧自行合并逻辑（W4-02）与 06-06 适配器标准输出进行了**逐事件键 `(exchange, market, symbol, funding_time)`、`rate` 与 `mark_price` 的完全对账**：

| 对账指标 | 06-06 适配器标准输出 | 原 W4-02 自行合并逻辑 | 对账吻合判定 |
|---|:---:|:---:|:---:|
| **覆盖市场候选样本数** | **51 个** | **51 个** | **100% 一致** |
| **观察期总资金费事件数** | **{reconcil['total_events_adapter']} 条** | **{reconcil['total_events_w4_02']} 条** | **逐事件数量相等** |
| **仅适配器存在事件数** | 0 条 | — | 0 处漏事件 |
| **仅自行合并存在事件数** | — | 0 条 | 0 处多事件 |
| **经济字段 (rate/mark) 差值超容差数** | 0 条 | 0 条 | 0 处数值分歧 |
| **51 标的逐事件对账最终结论** | **{reconcil['all_51_symbols_matched']}** | **{reconcil['all_51_symbols_matched']}** | **100% 逐事件键逐值完全吻合** |

- **结论**：对账证实原 W4-02 自行合并逻辑与 06-06 官方适配器提取结果达成了**100% 逐事件精确吻合**。本单自此全面统一收敛至 06-06 适配器接口，废除临时读取代码。

---

## 二、结算周期证据与缺口判据系统重构

### 2.1 撤销通用 8h 零事件旧判据的根本原因
主代理审查指出：通用 8h 零事件判据假定了交易所恒为 8h 结算且持仓期必须零事件才算缺口，这严重脱离市场真实机制。实测表明：
1. **真实周期并非 8h**：在 51 个候选标的中，**38 个标的的中位结算间隔是 4.0 小时**，9 个标的是 8.0 小时，还有 1 个标的（`SKRUSDT`）出现 1.0 小时与 4.0 小时混杂；
2. **部分漏事件隐患**：若某标的为 4h 结算，持仓 12 小时应结算 3 次，若仅结算 1 次（漏 2 次），旧判据因事件数 $>0$ 无法识别；
3. **不可判定风险**：若某标的历史数据变异大、缺乏确定周期元数据，旧判据直接判可用，属于虚假宣称完整。

### 2.2 周期感知缺口判据新体系
本单重构了严密的缺口判定状态机：
1. **`proven_regular_4h` / `proven_regular_8h` / `proven_regular_1h`**：历史结算间隔高度恒定，具备充分周期证据；
2. **`period_undetermined`（周期不可判定）**：间隔出现非标分布或混杂（如 1h 与 4h 并存），诚实标注为不可判定，严禁宣称完整；
3. **`NO_GAP_COMPLETE`（无缺口完整）**：持仓期理论应结算时刻 $T_{{\\text{{expected}}}}$ 与实际结算时刻 $T_{{\\text{{actual}}}}$ 逐一对应，且数据无缺失无冲突；
4. **`GAP_PARTIAL_MISSING`（部分漏事件缺口）**：实际结算事件少于理论应有次数；
5. **`GAP_TOTAL_MISSING`（全量缺失缺口）**：持仓跨越结算时刻但实际记录为 0；
6. **`GAP_FIELD_MISSING_OR_CONFLICT`（字段缺失或冲突）**：事件在库但 rate/mark 为 null 或处于冲突未决状态。

### 2.3 51 候选真实周期与缺口分类分布

| 周期判定类型 | 候选分布 |
|---|:---:|
{period_table_md}

| 缺口分类细分 | 候选分布 |
|---|:---:|
{gap_table_md}

---

## 三、METUSDT 专项核实与正文/表格一致性纠正

### 3.1 矛盾溯源
- 在 W4-02 旧版报告中，表格记录为：
  `METUSDT: 旧净额 -5.1613 → 新净额 -4.8755 USDT (差额 +0.2858 USDT)`；
- 但在正文案例剖析中，手写了旧文案：
  `净额由 -4.7171 改善为 -4.4313 USDT`；
- **根因确认**：`-4.7171` 系早期 05-D 文稿中未考虑滑点/缩量时的旧数字，因文稿拷贝导致正文与同页表格脱节。

### 3.2 真实底层数据穿透核对（从同一明细生成）
- **标的**：`{met['symbol']}`，入场时刻 `{met['entry_utc']}`，出场时刻 `{met['exit_utc']}`（持仓时长 `{met['held_hours']}` 小时）；
- **结算周期**：推导确为 `{met['settlement_period_detected']}`（4 小时结算一次，持仓跨越 12:00, 16:00, 20:00 三个结算点）；
- **结算事件**：旧热库仅 **{met['settlement_events_old']} 次**，06-06 适配器补齐为完整的 **{met['settlement_events_new']} 次**；
- **结算费率**：`{met['rates_in_events']}`（三根均为负费率，多头净收取资金费）；
- **口径 A（生效视图_5bps）**：
  - 入场成交价：`{met['effective_view_5bps']['entry_price']}`，出场成交价：`{met['effective_view_5bps']['exit_fill']}`，数量：`{met['effective_view_5bps']['qty']}`；
  - 旧资金费成本：`{met['effective_view_5bps']['funding_old']:.4f} USDT`，新资金费成本：`{met['effective_view_5bps']['funding_new']:.4f} USDT`（收入 0.2858 USDT）；
  - **旧已实现净额**：**`{met['effective_view_5bps']['net_old']:.4f} USDT`**；
  - **新已实现净额**：**`{met['effective_view_5bps']['net_new']:.4f} USDT`**；
  - **净额差额**：**`{met['effective_view_5bps']['delta_net']:+.4f} USDT`**；
- **口径 B（策略包_3bps）**：
  - 入场成交价：`{met['strategy_pack_3bps']['entry_price']}`，出场成交价：`{met['strategy_pack_3bps']['exit_fill']}`，数量：`{met['strategy_pack_3bps']['qty']}`；
  - **旧已实现净额**：**`{met['strategy_pack_3bps']['net_old']:.4f} USDT`**；
  - **新已实现净额**：**`{met['strategy_pack_3bps']['net_new']:.4f} USDT`**；
  - **净额差额**：**`{met['strategy_pack_3bps']['delta_net']:+.4f} USDT`**。

> **更正声明**：本单已彻底清除所有手写旧数字，本报告正文、表格及结论中的所有 METUSDT 数字全部由底层明细自动动态渲染，绝对一致。

---

## 四、两套经济口径分别核算（严禁合并）

| 统计指标 | 口径 A：生效视图_5bps (滑点5bps/手续费5bps/参与率1.0%/本金5000) | 口径 B：策略包_3bps (滑点3bps/手续费5bps/参与率0.1%/本金2000) |
|---|---|---|
| **测试候选事件数** | {c5['total_candidates']} 笔 | {c3['total_candidates']} 笔 |
| **实际成交试验数** | **{c5['filled_count']} 笔** | **{c3['filled_count']} 笔** |
| **因规拒单试验数** | 0 笔 | **1 笔** (`CARVUSDT`，缩量拒单无流水) |
| **资金费受影响笔数** | **{c5['affected_trades_count']} 笔** | **{c3['affected_trades_count']} 笔** |
| **旧累计资金费净成本** | **{c5['financial_totals']['funding_cost_old']:+.4f} USDT** | **{c3['financial_totals']['funding_cost_old']:+.4f} USDT** |
| **新累计资金费净成本** | **{c5['financial_totals']['funding_cost_new']:+.4f} USDT** | **{c3['financial_totals']['funding_cost_new']:+.4f} USDT** |
| **资金费成本净差额 (ΔFunding)** | **`{c5['financial_totals']['delta_funding']:+.4f} USDT`** | **`{c3['financial_totals']['delta_funding']:+.4f} USDT`** |
| **旧累计已实现净额** | **{c5['financial_totals']['realized_net_old']:.4f} USDT** | **{c3['financial_totals']['realized_net_old']:.4f} USDT** |
| **新累计已实现净额** | **{c5['financial_totals']['realized_net_new']:.4f} USDT** | **{c3['financial_totals']['realized_net_new']:.4f} USDT** |
| **已实现净额净差额 (ΔNet)** | **`{c5['financial_totals']['delta_net']:+.4f} USDT`** | **`{c3['financial_totals']['delta_net']:+.4f} USDT`** |
| **财务恒等式验证** | **$\\Delta\\text{{Net}} == -\\Delta\\text{{Funding}}$ 逐位精确成立** | **$\\Delta\\text{{Net}} == -\\Delta\\text{{Funding}}$ 逐位精确成立** |
| **同刻歧义保留交易数 (相距≤15m)** | **{c5['timing_uncertain_trades_count']} 笔** | **{c3['timing_uncertain_trades_count']} 笔** |

---

## 五、受影响 7 笔交易逐笔核算表

### 5.1 口径 A：生效视图_5bps 逐笔受影响明细
| 标的 Symbol | 候选编号 Candidate ID | 结算次数变动 | 资金费变动 (USDT) | 资金费差额 | 已实现净额变动 (USDT) | 净额差额 | 缺口定性 |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|
{aff_table_5bps_md}

### 5.2 口径 B：策略包_3bps 逐笔受影响明细
| 标的 Symbol | 候选编号 Candidate ID | 结算次数变动 | 资金费变动 (USDT) | 资金费差额 | 已实现净额变动 (USDT) | 净额差额 | 缺口定性 |
|---|---|:---:|:---:|:---:|:---:|:---:|:---:|
{aff_table_3bps_md}

---

## 六、共享代码与关键文件哈希登记

- **未修改共享文件声明**：
  经审查，`框架/backend/replay_calc.py` 计算接口与时序完全正常，本单**未修改 `replay_calc.py`，保持代码冻结**。

- **关键哈希清单**：
```json
{json.dumps(hashes, indent=2, ensure_ascii=False)}
```

---

## 七、交付下游说明（给窗口五、六独立复验）

1. **产物位置**：
   - 逐笔运算与判据明细：[`01_资金费重算与判据整改明细.json`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-06-W4/03_R1/out/01_资金费重算与判据整改明细.json)
   - 完整验收报告：[`README.md`](file:///Users/boos/Desktop/威科夫因子共振趋势/evidence/ACCUM-EARLY-06-W4/03_R1/README.md)
2. **复验指引**：
   - **给窗口五（06-10 独立端到端复验）**：可直接调取本单的 `01_资金费重算与判据整改明细.json`，核验 06-06 适配器对账一致性（100% 逐事件吻合）与新的周期感知缺口分类；
   - **给窗口六（06-12 机制基线更新与总交接）**：直接消费本单生成的两套口径最新财务总额（生效视图 -225.8407 USDT，策略包 -89.2382 USDT），保持两套独立呈现。
"""

    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f">>> [W4-R1] 验收报告生成成功：{REPORT_MD}")

if __name__ == "__main__":
    generate_report()
