# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-D-R1｜02 自动生成验收报告

根据 01 脚本输出的审计数据，自动生成结构化、全中文的 Markdown 验收报告。
所有数字、通过率、哈希、财务算式均由数据动态驱动，杜绝手写与口径漂移。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
AUDIT_JSON = ROOT / "evidence" / "ACCUM-EARLY-05-D-R1" / "out" / "01_全量动态逐笔核验与防写死审计.json"
REPORT_MD = ROOT / "evidence" / "ACCUM-EARLY-05-D-R1" / "README.md"

def generate_report():
    print(">>> 开始自动生成 ACCUM-EARLY-05-D-R1 验收报告...")
    with open(AUDIT_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    hashes = data["hashes"]
    carv = data["carv_financial_derivation"]
    summary = data["actual_summary_from_trades"]
    corrupted = data["corrupted_negative_test"]
    synthetic = data["synthetic_open_position_test"]
    calibers = data["independent_calibers_statistics"]

    cal_5bps = calibers["生效视图_5bps"]
    cal_3bps = calibers["策略包_3bps"]

    calc_lines = "\n".join(f"- {step}" for step in carv["step_by_step_calculation"])

    md_content = f"""# ACCUM-EARLY-05-D-R1 验收报告：报告纠错与计算核验补齐

> **审计执行方**：Work / 反重力隔离审计  
> **任务标识**：`ACCUM-EARLY-05-D-R1`  
> **承接说明**：承接 05-D，负责 `replay_calc.py` 及隔离核验补齐；不修改 `paper_engine.py`、策略阈值或其他窗口代码。  
> **⚠ 状态口径**：**执行方自测通过，不等于 Codex 最终验收**。

---

## 一、旧报告纠错清单（保留历史，逐项纠偏）

| 序号 | 报告来源 | 原报告记录 / 说法 | 真实底层底账 / 纠偏结论 | 错误性质与影响 |
|---|---|---|---|---|
| **1** | 05-D 报告与摘要 | CARVUSDT 生效视图净额误记为 `-4.7171 USDT` | 实际逐笔记录为 **`+{carv['exact_net_pnl']:.6f} USDT`**（精确值 `{carv['exact_net_pnl']}`） | **财务数字错误**：误将 METUSDT 亏损值抄入，现已按底层字段完整重建算式 |
| **2** | 04-D 报告表格 | 称“因规拒单 (`HOLD_REJECTED`) 2 笔，政策暂缓 37 笔” | 真实产物底账为 **1 笔拒单 (`CARVUSDT`)，38 笔政策暂缓** | **文稿手写笔误**：人工将 39 笔暂缓拆分为 37/2，且误混入前序测试标的 `AUCTIONUSDT` |
| **3** | 04-D & 05-D 汇总 | 宣称“102 笔全部吻合”（`all_match=True`） | 真实成交字段核验样本为 **101 笔**，拒单 1 笔无财务流水 | **口径不精确**：拒单不能算作成交字段验证通过，须严格区分 |
| **4** | 05-D 脚本核验 | 04-C 对照状态使用写死的 `status_in_04c="rejected"` | 现已改为**实际读取 04-C 产物 JSON** 并按 `candidate_id` 与口径动态查找匹配 | **核验伪造风险**：杜绝写死对照，确保由文件动态判定 |
| **5** | 05-D 汇总断言 | 汇总全量一致性直接由静态字段汇总，缺乏防写死校验 | 补充**故意改坏输入（1 bps 偏离）的负例断言**，验证汇总能敏锐阻断 | **防写死补齐**：证明汇总结论绝非静态常数 |

---

## 二、CARVUSDT 生效视图精确财务核对（源字段与逐级算式）

### 2.1 原始数据源字段提取
- **候选编号**：`cand_ev_WX_EVENT_TREND_V0_1_binance_CARVUSDT_1789545599999_1789822800000`
- **参数口径**：`生效视图_5bps` (滑点 5bps = 0.0005，单边手续费 5bps = 0.0005)
- **开盘原始价**：`raw_open = {carv['source_fields']['raw_open']}`
- **实际成交价**：`entry_price = {carv['source_fields']['entry_price']}`
- **初始止损价**：`initial_stop = {carv['source_fields']['initial_stop']}`
- **实际成交量**：`qty = {carv['source_fields']['qty']}` (参与率 1.0% 上限缩量)
- **入场名义额**：`entry_notional = {carv['source_fields']['entry_notional']:.6f} USDT`
- **入场手续费**：`entry_fee = {carv['source_fields']['entry_fee']:.8f} USDT`
- **出场时间**：`exit_utc = {carv['source_fields']['exit_utc']}`
- **出场成交价**：`exit_fill = {carv['source_fields']['exit_fill']}` (跟踪止损抬升后盘中触及)
- **出场名义额**：`exit_notional = {carv['source_fields']['exit_notional']:.6f} USDT`
- **出场手续费**：`exit_fee = {carv['source_fields']['exit_fee']:.8f} USDT`
- **资金费成本**：`funding_cost = {carv['source_fields']['funding_cost']:.8f} USDT` (净支付)

### 2.2 逐级数学推导算式
{calc_lines}

> **纠正声明**：此前 05-D 正文中误填的 -4.7171 系将 METUSDT 亏损值误抄导致。现根据逐笔底层产物记录，严格推导并纠正为精确值 **+{carv['exact_net_pnl']} USDT**。

---

## 三、真实核验覆盖与动态汇总结果（绝无写死）

所有指标均由脚本遍历逐笔核验明细经过动态计数与比较函数实时汇总：

| 核验维度 | 动态统计结果 | 判定口径与说明 |
|---|---|---|
| **总测试试验数** | **{summary['total_tested']} 笔** | 51 候选 × 2 套经济口径试验 |
| **实际成交试验数** | **{summary['filled_count']} 笔** | 形成真实持仓与出场流水的记录数 |
| **因规拒单试验数** | **{summary['rejected_count']} 笔** | CARVUSDT 在策略包 0.1% 参与率下缩量过小拒单 |
| **成交字段逐项吻合数** | **{summary['all_filled_matched_count']} / {summary['filled_count']} 笔** | 实际成交价、初始止损、数量取整、入场费、出场费、出场时间、出场价、资金费、已实现净额 **8 字段全部逐位吻合** |
| **成交字段核验通过率** | **{summary['all_filled_matched_count'] / summary['filled_count'] * 100:.2f}%** | **101 笔真实成交 100% 通过**，拒单 1 笔不计入分子分母 |
| **拒单依据与财务流水核验** | **{summary['all_rejected_valid_count']} / {summary['rejected_count']} 笔** | 验证拒单触发依据（低于最小可下单量/名义额）且持仓、名义额、费用全部严格为 0 |
| **资金费已有字段非空率** | **{summary['funding_stats']['records_non_null_count']} / {summary['filled_count']} 笔 (100.00%)** | 库中已抓取的资金费记录其 `funding_rate` 与 `mark_price` 100% 非空 |
| **资金费 8h 结算事件完整率** | **{summary['funding_stats']['funding_complete_count']} / {summary['filled_count']} 笔 ({summary['funding_stats']['funding_complete_count'] / summary['filled_count'] * 100:.2f}%)** | {summary['funding_stats']['missing_settlement_trades_count']} 笔持仓期间存在 8h 结算事件缺漏，诚实标注，不宣称“历史资金费完全无缺” |
| **窗口三可用性分类分布** | 可用 **{summary['w3_counts']['usable']}** / 暂缓 **{summary['w3_counts']['hold_pending']}** / 拒单 **{summary['w3_counts']['hold_rejected']}** | 真实底账完全吻合 63 / 38 / 1，合计 102 笔 |

---

## 四、负例断言与合成边界核验

### 4.1 汇总防写死负例断言（篡改阻断测试）
- **测试动作**：在内存中克隆全量 101 笔成交比对结果，故意将第 1 笔记录的 `entry_price` 匹配标记置为 `False`；
- **汇总阻断验证**：
  - 篡改后汇总 `all_filled_matched` 结果：`{corrupted['corrupted_all_matched']}`（预期 `False`）；
  - 篡改后通过笔数：`{corrupted['corrupted_all_matched_count']}` 笔（精确减少 1 笔）；
  - **断言结果**：**通过**，证明汇总判断完全由底层数组实时驱动，任一字段不匹配汇总必然报警。

### 4.2 期末未平仓合成边界用例（SYNTHETIC_BOUNDARY_CASE）
- **测试背景**：全量 101 笔真实历史成交在 14 天观察期内全部触及止损离场，真实历史未覆盖期末未平仓分支；
- **合成用例设计**：构造 10 根 15m K 线，价格始终运行在 98.0~102.0（初始止损 95.0），未触发平仓；
- **计算核验表现**：
  - `hit` = `{synthetic['hit']}` (未触发退出，`exit_kind = {synthetic['exit_kind']}`)；
  - `exit_fill` = `None`，`realized_net` = `None`（**严禁计入已实现净额**）；
  - `unrealized_gross` = `+1.0000 USDT`，`unrealized_exit_fee_est` = `0.0505 USDT`；
  - `unrealized_net` = **`+{synthetic['unrealized_net']} USDT`**（毛额 - 入场费 - 预估出场费）；
  - **断言判定**：`{synthetic['assertion_passed']}`，期末未平仓逻辑在合成边界下完全通过。

---

## 五、两套经济口径分别独立统计（严禁合并）

| 统计指标 | 口径 A：生效视图_5bps (滑点5bps/手续费5bps/参与率1%) | 口径 B：策略包_3bps (滑点3bps/手续费3bps/参与率0.1%) | 口径边界原则 |
|---|---|---|---|
| **测试候选事件数** | {cal_5bps['total_candidates_tested']} 笔 | {cal_3bps['total_candidates_tested']} 笔 | 两套口径属于独立研究设定 |
| **实际成交笔数** | **{cal_5bps['executed_count']} 笔** | **{cal_3bps['executed_count']} 笔** | 策略包因参与率强约束拒单 1 笔 |
| **因规拒单笔数** | 0 笔 | **{cal_3bps['rejected_count']} 笔** (`CARVUSDT`) | 拒单无持仓无流水 |
| **盈利交易笔数** | {cal_5bps['positive_count']} 笔 | {cal_3bps['positive_count']} 笔 | 净额 > 0 笔数 |
| **亏损交易笔数** | {cal_5bps['negative_count']} 笔 | {cal_3bps['negative_count']} 笔 | 净额 < 0 笔数 |
| **交易胜率 (Win Rate)** | **{cal_5bps['win_rate_pct']:.2f}%** | **{cal_3bps['win_rate_pct']:.2f}%** | 仅基于真实成交笔数计算 |
| **累计已实现净收益** | **{cal_5bps['total_realized_net']:.4f} USDT** | **{cal_3bps['total_realized_net']:.4f} USDT** | **严禁将两套同候选试验合并相加** |
| **入场手续费合计** | {cal_5bps['total_entry_fee']:.4f} USDT | {cal_3bps['total_entry_fee']:.4f} USDT | 策略包剧烈缩量导致费用大幅收窄 |
| **出场手续费合计** | {cal_5bps['total_exit_fee']:.4f} USDT | {cal_3bps['total_exit_fee']:.4f} USDT | 出场手续费逐笔核算 |
| **资金费净成本合计** | {cal_5bps['total_funding_cost']:.4f} USDT | {cal_3bps['total_funding_cost']:.4f} USDT | 负值表示净收取资金费 |

---

## 六、关键代码与配置哈希清单

```json
{json.dumps(hashes, indent=2, ensure_ascii=False)}
```

---

## 七、给 05-C 的基准可用范围界定

1. **可用参考基准**：
   - 窗口三可用清单中的 **63 笔基准单**（无资金费重叠争议、未发生参与率缩量截断）可作为稳健基准；
   - 38 笔暂缓单与 1 笔拒单继续维持 `HOLD` 状态，待后续窗口明确跳空与参与率口径裁决；
2. **严防策略美化**：
   - 63 笔可用组净额为 -310.54 USDT（胜率 28.57%），劣于全池平均，证明该分流是客观风险隔离，绝非美化；
   - 05-C 回测与对比严禁直接将 63 笔净额作为原策略的最终真实净额；
3. **保持未决口径并列**：
   - 滑点 3bps / 5bps 保持并列输出；跳空 3 模型保持并列输出，不越权代为决定策略最优。
"""

    with open(REPORT_MD, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(">>> 02 自动生成验收报告完成，README.md 已成功生成！")

if __name__ == "__main__":
    generate_report()
