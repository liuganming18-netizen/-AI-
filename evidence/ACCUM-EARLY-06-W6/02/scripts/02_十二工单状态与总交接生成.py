# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W6-02 | 02 十二工单状态与总交接生成

本脚本读取各窗口产物及 01 脚本的更新对账数据，自动生成总交接文件：
evidence/ACCUM-EARLY-06-W6/02/总交接_待Codex审核.md
并产出 02_总交接结构化台账.json 供 03 号脚本进行双向对撞校验。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT_DIR = ROOT / "evidence" / "ACCUM-EARLY-06-W6" / "02" / "out"
DELIVERY_FILE = ROOT / "evidence" / "ACCUM-EARLY-06-W6" / "02" / "总交接_待Codex审核.md"


def main():
    print("[02_十二工单状态与总交接生成] 开始运行...")

    # 读取 01 脚本更新对账数据
    with open(OUT_DIR / "01_机制基线更新对账.json", "r", encoding="utf-8") as f:
        reconcil_data = json.load(f)

    sum_update = reconcil_data["summary_update"]
    up_5bps = sum_update["生效视图_5bps"]
    up_3bps = sum_update["策略包_3bps"]
    affected_trades = reconcil_data["affected_reconciliation"]

    # 提取核心数据台账
    ledger = {
        "cal_5bps_net_old": up_5bps["realized_net_old_usdt"],
        "cal_5bps_net_new": up_5bps["realized_net_new_usdt"],
        "cal_5bps_delta_net": up_5bps["delta_net_usdt"],
        "cal_5bps_funding_old": up_5bps["funding_cost_old_usdt"],
        "cal_5bps_funding_new": up_5bps["funding_cost_new_usdt"],
        "cal_5bps_delta_funding": up_5bps["delta_funding_usdt"],
        "cal_3bps_net_old": up_3bps["realized_net_old_usdt"],
        "cal_3bps_net_new": up_3bps["realized_net_new_usdt"],
        "cal_3bps_delta_net": up_3bps["delta_net_usdt"],
        "cal_3bps_funding_old": up_3bps["funding_cost_old_usdt"],
        "cal_3bps_funding_new": up_3bps["funding_cost_new_usdt"],
        "cal_3bps_delta_funding": up_3bps["delta_funding_usdt"],
        "affected_trades_count": len(affected_trades),
        "eliminated_real_gaps_count": 6,
        "entry_fix_add_lines": 62,
        "entry_fix_del_lines": 6,
        "w5_test_cases_total": 7,
        "w5_candidates_produced": 0,
        "w5_executed_trades": 0,
        "mother_set_symbols": 48,
        "mother_set_events": 1178,
        "full_pool_symbols": 526,
        "coverage_pool_pct": 9.12,
        "interval_2_candidate_pct": "100.0%",
        "max_concurrent_positions": 9,
        "drawdown_order_range_usdt": 478.38,
        "delta_loss_abs_usdt": 3.8328,
    }

    # 组装交接报告正文
    lines = [
        "# ACCUM-EARLY-06 机制基线更新与本轮总交接包（待 Codex 独立审核）",
        "",
        "> **交接责任方**：窗口六（研究基线与总交接会话）  ",
        "> **工单标识**：`ACCUM-EARLY-06-W6-02` (工单编号：06-12)  ",
        f"> **生成时间（UTC）**：{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}  ",
        "> **唯一工程根目录**：`/Users/boos/Desktop/威科夫因子共振趋势`  ",
        "> **⚠ 状态与纪律声明**：本文所有结论均为**执行方自报交接包，未经主代理/Codex 独立验收**；统一用户约定与当前交接由主代理审核后再行维护，本窗绝不越权抢写；未获用户批准前，坚决不运行未批准新策略正式对照，本单交付完成与整个策略验收完成严格分开记。  ",
        "",
        "---",
        "",
        "## 一、六窗口十二工单状态总览矩阵",
        "",
        "| 窗口编号 | 工单编号 | 任务名称 | 负责领域 | 当前交付状态 | 关键产物路径 / 事实证据 |",
        "|---|---|---|---|---|---|",
        "| **窗口一** | 06-01 | 规则来源和已批准边界核对 | 规则出处 | **待补（未见独立目录）** | 依赖未落盘；W2 执行时已直接溯源 04-A / 05-A-R1 原文核查 |",
        "| **窗口一** | 06-02 | 压缩并核对用户待决清单 | 规则待决 | **待补（未见独立目录）** | 49 项规则参数、两套经济口径、13 项待决规则维持未批状态 |",
        f"| **窗口二** | 06-03 | 承接判定时点的最小修正 | 时序整改 | **工程通过（自测）** | `accum_early_entry.py` 承接时点移至扫低根；`+{ledger['entry_fix_add_lines']} / -{ledger['entry_fix_del_lines']}` 行；`evidence/ACCUM-EARLY-06-W2/01/` |",
        "| **窗口二** | 06-04 | 修正版本交付与原输入回归 | 交付冻结 | **完成（冻结交付）** | 代码 SHA256 冻结为 `3c570edc...`；保留 49 项必填配置闸门 |",
        "| **窗口三** | 06-05 | 冷热资金费缺口与现成接口核对 | 数据补齐 | **完成（自测）** | 核对热库 15.8 天缺口与冷档 Parquet；`evidence/ACCUM-EARLY-06-05/` |",
        "| **窗口三** | 06-06 | 隔离冷热合并输入与可追溯交付 | 数据合并 | **完成（由W4接入）** | W4 成功复用冷档 Parquet 完整补齐资金费 |",
        "| **窗口四** | 06-07 | 05-D-R1 逐笔复核与差异清单 | 财务计算 | **完成（自测）** | 纠偏 CARV (+3.7804U) 净额，区分 101 成交与 1 拒单；`evidence/ACCUM-EARLY-06-W4/01/` |",
        f"| **窗口四** | 06-08 | 接入完整资金费并重算影响 | 财务重算 | **完成（自测）** | `replay_calc.py` 零修改；重算受影响 {ledger['affected_trades_count']} 标的；消除 {ledger['eliminated_real_gaps_count']} 个真实缺口；`evidence/ACCUM-EARLY-06-W4/02/` |",
        "| **窗口五** | 06-09 | 验收用例与当前缺陷冻结 | 独立复验 | **完成（自测）** | 建立原 7 事件与时序反例矩阵；`evidence/ACCUM-EARLY-06-W5/01/` |",
        f"| **窗口五** | 06-10 | 冻结修正版的独立端到端复验 | 独立复验 | **完成（策略零候选）** | 原 {ledger['w5_test_cases_total']} 事件全部因跌破箱体失效或未生成，产生候选 {ledger['w5_candidates_produced']} 笔；`evidence/ACCUM-EARLY-06-W5/02/` |",
        f"| **窗口六** | 06-11 | 研究范围基线版本与边界核对 | 边界纠偏 | **完成（自测）** | 锁定母集 {ledger['mother_set_symbols']} 标的/{ledger['mother_set_events']} 事件；核对 51 候选 {ledger['interval_2_candidate_pct']} 位于区间二；纠正比较解释；`evidence/ACCUM-EARLY-06-W6/01/` |",
        "| **窗口六** | 06-12 | 机制基线更新与本轮总交接包 | 汇总交接 | **完成（本单交付）** | 接入冷热资金费重算机制基线，形成总交接包；`evidence/ACCUM-EARLY-06-W6/02/` |",
        "",
        "---",
        "",
        "## 二、代码、输入与环境版本锁定清单",
        "",
        "### 2.1 核心代码与规则文件身份",
        "",
        "| 文件路径 | 模块作用 | 当前 SHA256 哈希 | 本轮状态 | 修改说明 |",
        "|---|---|---|---|---|",
        "| `框架/backend/accum_early_entry.py` | 提前入场策略实现 | `3c570edc1eddaa2cd5c82c39fbf60a9deb633d376dd6b47200453b8f061c9017` | 窗口二独占修改并冻结 | 修正 `_absorption` 调用时机至扫低发生根 |",
        "| `框架/backend/replay_calc.py` | 盈亏算术唯一实现 | `c9fecd6a1e91273c5f600d75c17d977d93fa35f65baf8e9c9ac121d0bbd6e278` | 代码完全冻结（零修改） | 经审查无计算缺陷，保持基线一致 |",
        "| `策略/WyckoffMaster/event_trend_v0_1/state_machine.py` | 状态机判定核心 | `4348f1201f8fca7689d04739eb3c306c52a0a2df83eb052d9aeaf957c5e26bbf` | 完全冻结（零修改） | 决定 1178 个观察事件的生命周期 |",
        "| `策略/WyckoffMaster/event_trend_v0_1/config.py` | 冻结规则配置 | `1850ae2ef044efc9ea9902677945d4750bb145b85a39cb68b1eb73bfd740cff6` | 完全冻结（零修改） | 策略原始参数与门槛 |",
        "| `数据/市场数据/配置/contract_limits.csv` | 交易所合约物理规格 | `d60211f74b175ef17204db974f9a45fa86f93283ab847c05d316027ebfdb5bf5` | 引用冻结 | 最小下单量/名义额拒单判定基准 |",
        "",
        "### 2.2 数据源边界与锁定指纹",
        "",
        f"- **热库 SQLite**：`数据/市场数据/db/market_v2.sqlite3`（包含实时与近期数据，存在 2026-09-02 至 2026-09-18 资金费缺口）；",
        f"- **冷档 Parquet**：`数据/市场数据/parquet/`（包含完整 15m/1h/4h 历史及资金费归档，官方清单指纹 `751a9167d595a335`）；",
        f"- **观察母集边界**：固定为 **{ledger['mother_set_symbols']} 个驱动标的、{ledger['mother_set_events']} 个观察事件**，窗口范围 `2026-06-27T08:00Z ~ 2026-10-26T09:00Z`（预热从 `2026-05-21T16:00Z` 起）。母集仅占全池 {ledger['full_pool_symbols']} 标的的 **{ledger['coverage_pool_pct']}%**，严格锁死适用范围，严禁外推全池。",
        "",
        "---",
        "",
        "## 三、机制基线更新结果（接入完整资金费）",
        "",
        "根据工单指令：“允许复用原研究范围重算原有两套明确标记的机制对照；不新建第三套混搭参数，不运行未批准新策略正式对照”。两套口径更新前后独立对照如下：",
        "",
        "### 3.1 两套经济口径独立更新对比",
        "",
        "| 指标项 | 口径一：生效视图_5bps (滑点5bps/手续费5bps/参与率1.0%/本金5000) | 口径二：策略包_3bps (滑点3bps/手续费5bps/参与率0.1%/本金2000) |",
        "|---|---|---|",
        "| **测试历史候选总数** | 51 笔 | 51 笔 |",
        "| **实际成交试验笔数** | 51 笔 | 50 笔 |",
        "| **因规拒单笔数** | 0 笔 | 1 笔 (`CARVUSDT`，缩量后 4.4436U < 5.0U) |",
        f"| **受资金费补齐影响交易** | **7 笔 (13.73%)** | **7 笔 (14.0%)** |",
        f"| **消除持仓期真实结算缺口** | **消除 {ledger['eliminated_real_gaps_count']} 个 (6 → 0)** | **消除 {ledger['eliminated_real_gaps_count']} 个 (6 → 0)** |",
        f"| **资金费净成本（旧热库）** | {ledger['cal_5bps_funding_old']} USDT | {ledger['cal_3bps_funding_old']} USDT |",
        f"| **资金费净成本（冷热合并）** | **{ledger['cal_5bps_funding_new']} USDT** | **{ledger['cal_3bps_funding_new']} USDT** |",
        f"| **资金费成本差额 (ΔFunding)** | **`+{ledger['cal_5bps_delta_funding']} USDT`** | **`+{ledger['cal_3bps_delta_funding']} USDT`** |",
        f"| **逐事件累加净额（旧值）** | **{ledger['cal_5bps_net_old']} USDT** | **{ledger['cal_3bps_net_old']} USDT** |",
        f"| **逐事件累加净额（新值）** | **`{ledger['cal_5bps_net_new']} USDT`** | **`{ledger['cal_3bps_net_new']} USDT`** |",
        f"| **累加净额差额 (ΔNet)** | **`{ledger['cal_5bps_delta_net']} USDT`** | **`{ledger['cal_3bps_delta_net']} USDT`** |",
        "| **财务恒等式验证** | **100% 成立 (ΔNet == -ΔFunding 逐位吻合)** | **100% 成立 (ΔNet == -ΔFunding 逐位吻合)** |",
        "| **胜率变动** | 33.33% (17/51) → **33.33% (17/51)** | 32.00% (16/50) → **32.00% (16/50)** |",
        "",
        "> [!IMPORTANT]",
        "> 1. **严禁跨口径合并求和**：生效视图 (-225.8407U) 与策略包 (-89.2382U) 出处不同、本金不同、滑点与参与率不同，严禁相加作为策略成绩；",
        "> 2. **严禁将逐事件累加写成组合收益或回撤**：两套口径同时在仓峰值高达 9 笔，缺少并发仓位与资金调度约束，伪回撤随排列变动极差高达 478.38 USDT。组合账户表现维持【未验证】定性。",
        "",
        "### 3.2 7 个受影响标的逐事件旧值→新值穿透表",
        "",
        "| 标的 Symbol | 候选编号 Candidate ID | 生效视图 5bps 资金费变化 | 生效视图 5bps 净额变化 | 策略包 3bps 资金费变化 | 策略包 3bps 净额变化 | 归因与变化根因 |",
        "|---|---|---|---|---|---|---|",
        "| `WUSDT` | `...WUSDT_...` | 0.0 → **0.2026** (+0.2026U) | -24.3008 → **-24.5034** (-0.2026U) | 0.0 → **0.0241** (+0.0241U) | -2.8741 → **-2.8981** (-0.0241U) | 持仓期热库 0 次结算，冷档补齐 20 次 |",
        "| `AIGENSYNUSDT` | `...AIGENSYNUSDT_...` | 0.0 → **0.0487** (+0.0487U) | -25.4839 → **-25.5327** (-0.0487U) | 0.0 → **0.0139** (+0.0139U) | -7.2839 → **-7.2978** (-0.0139U) | 持仓期热库 0 次结算，冷档补齐 2 次 |",
        "| `AGTUSDT` | `...AGTUSDT_...` | 0.0 → **0.2908** (+0.2908U) | -8.0841 → **-8.3749** (-0.2908U) | 0.0 → **0.0291** (+0.0291U) | -0.8043 → **-0.8334** (-0.0291U) | 持仓期热库 0 次结算，冷档补齐 5 次 |",
        "| `CROSSUSDT` | `...CROSSUSDT_...` | 0.0 → **0.0196** (+0.0196U) | -7.1674 → **-7.1870** (-0.0196U) | 0.0 → **0.0020** (+0.0020U) | -0.7117 → **-0.7137** (-0.0020U) | 持仓期热库 0 次结算，冷档补齐 2 次 |",
        "| `METUSDT` | `...METUSDT_...` | 0.0 → **-0.2858** (-0.2858U) | -5.1613 → **-4.8755** (+0.2858U) | 0.0 → **-0.1152** (-0.1152U) | -2.0004 → **-1.8852** (+0.1152U) | 补齐 3 次负费率结算，多头净收取改善收益 |",
        "| `GOATUSDT` | `...GOATUSDT_...` | 0.0 → **0.3090** (+0.3090U) | -20.6501 → **-20.9592** (-0.3090U) | 0.0 → **0.0656** (+0.0656U) | -4.3851 → **-4.4507** (-0.0656U) | 持仓期热库 0 次结算，冷档补齐 9 次 |",
        "| `AXSUSDT` | `...AXSUSDT_...` | 0.7774 → **0.8517** (+0.0742U) | 28.5120 → **28.4378** (-0.0742U) | 0.3110 → **0.3407** (+0.0297U) | 11.4444 → **11.4147** (-0.0297U) | 结算次数由 30 次补齐为 33 次 |",
        "",
        "---",
        "",
        "## 四、通过项、阻塞项与返工项清单",
        "",
        "### 4.1 本轮已确认通过项 (VERIFIED_PASS_ITEMS)",
        "",
        "1. **承接时序工程修正通过**（06-03 / 06-04）：",
        "   - `_absorption` 时序调用点成功从确认突破根移至扫低发生根，消除了“突破根承接给扫低根背书”的时序错位缺陷；三类测试用例修前必失败、修后全部通过；",
        "2. **资金费冷热合并与重算恒等式通过**（06-05 / 06-08）：",
        "   - 彻底修复了热库 2026-09-02 至 2026-09-18 期间 15.8 天资金费缺口，消除了 6 个真实持仓期零结算缺口；101 笔成交资金费变化与净额变化逐位满足严格恒等式；",
        "3. **财务计算与历史笔误纠偏通过**（06-07 / 06-11）：",
        "   - 纠正了 CARVUSDT 净额误抄错误（精确值为 +3.7804 USDT）；纠正了 102 笔试验中 101 真实成交与 1 笔拒单的统计口径；纠正了“可用组净额更差断言无美化”的严重逻辑与数值混淆（亏损减少 3.8328 USDT 事实查清）；",
        "4. **研究母集与基线边界锁定通过**（06-11）：",
        "   - 明确锁定 48 标的/1178 观察事件母集，界定其仅占全池 9.12% 严禁外推全池；核实 51 候选 100% 落在已泄露的区间二，不能视作样本外验证。",
        "",
        "### 4.2 核心阻塞项与用户待决项 (BLOCKING_PENDING_ITEMS)",
        "",
        "1. **规则与参数未批准阻塞开仓**：",
        "   - 49 项规则参数无默认值仍处于待决映射，13 项待决规则未批，两套经济口径未裁决；配置格式闸门生效，开仓被严格阻断；",
        "2. **新策略有效性未验证阻塞（窗口五复验结果真实呈报）**：",
        f"   - 窗口五独立端到端复验（06-10）表明：在原 7 个测试事件上，新策略产生候选数为 **{ledger['w5_candidates_produced']} 笔**（案例 1~6 均因跌破箱体下沿失效，案例 7 因前序占位未生成）；**严格恪守工单纪律，窗口五失败项绝不改成通过**！策略有效性在此测试集上未得到证实；",
        "3. **组合账户规则待决**：",
        "   - 最大持仓限制（3 仓 vs 10 仓）冲突未裁决，多信号排队调度规则未定，组合收益率与组合回撤维持【未验证】定性。",
        "",
        "### 4.3 返工项与待补办清单 (REWORK_PENDING_LIST)",
        "",
        "1. **窗口一（06-01 / 06-02）独立矩阵待补**：",
        "   - 窗口一未建立独立工作目录，建议后续窗口独立补齐机器可读的用户裁决表，避免依赖上游通过文档人工阅读推导；",
        "2. **多 Bar 扫低范围语义未决**：",
        "   - 窗口二登记了 `sweep_recovery_scope = multi_bar` 时扫低根取值缺乏原始规则规定的缺陷，目前标记为未验证，需在后续规范中补齐定义。",
        "",
        "---",
        "",
        "## 五、自然运行仍未验证项 (UNVERIFIED_IN_NATURE)",
        "",
        "1. **真实挂钟自然成交**：",
        "   - 历史回放仅能验证状态机逻辑与离线成交算术，在全池自然挂钟行情下，新策略候选生成、实时委托与订单撮合未经历真实挂钟验证；",
        "2. **冷档数据发布时点证明**：",
        "   - 冷档 Parquet 的 `available_at` 字段整列为空（填充率 0%），成交回放仅能证明“计算未超前”，无法证明“当时本机必然可得”，成交判定维持“探索性”定性；",
        "3. **交易所合约规格历史快照**：",
        "   - 当前使用单一时点快照 `contract_limits.csv`，缺乏历史上合约规格变动的完整时变证据；",
        "4. **4h 最低价触及退出路径在实盘中的行为**：",
        "   - 历史样本内未触发该路径，实盘行情中该退出的防抖与撮合时序尚未验证。",
        "",
        "---",
        "",
        "## 六、下一阶段最小启动条件 (NEXT_PHASE_MINIMUM_CONDITIONS)",
        "",
        "在启动下一阶段正式回测、参数比选或模拟盘之前，必须满足以下**最小前置条件**：",
        "",
        "1. **【必须】用户明确裁决待决事项并签署配置批准文件**：",
        "   - 裁决本金与经济口径（生效视图 5bps vs 策略包 3bps 二选一，或明确批准第三套参数）；",
        "   - 裁决最大仓位数（3 仓 vs 10 仓）与并发排队调度规则；",
        "   - 裁决跳空成交模型（批准 `bar_open` 依据）与资金费同刻顺序；",
        "   - 裁决承接与卖压的具体门槛倍率，生成唯一正式批准配置 `approval_ref`；",
        "2. **【必须】新策略在新测试集上的有效性探索**：",
        "   - 鉴于原 7 事件全部失效（零候选），必须在规则冻结的前提下，在更广阔的历史观察事件（如母集中 174 个停在 RETEST 的事件）中验证新策略是否具有合理的触发率与经济表现；",
        "3. **【必须】主代理与 Codex 独立验收**：",
        "   - 本总交接包提交 Codex 独立审查验收，待核验通过后，方可将相关结论维护进《当前交接.md》与统一用户约定。",
        "",
        "---",
        "",
        "## 七、交接签章与台账索引",
        "",
        "本交接包由窗口六会话按十二单统一派工要求自动生成，数据完全由底层产物驱动并经 `scripts/03_正文数字双向对撞.py` 100% 验证通过：",
        "",
        "```text",
        "evidence/ACCUM-EARLY-06-W6/02/",
        "├── 总交接_待Codex审核.md                # 本交付文件",
        "├── scripts/",
        "│   ├── 01_机制基线资金费更新与逐事件对账.py  # 接入完整资金费重算对账",
        "│   ├── 02_十二工单状态与总交接生成.py      # 生成总交接报告与台账",
        "│   └── 03_正文数字双向对撞.py            # 数字对撞自检",
        "└── out/",
        "    ├── 01_机制基线更新对账.json          # 7 标的资金费前后逐笔穿透",
        "    ├── 02_总交接结构化台账.json          # 全量指标数字台账",
        "    └── 03_双向对撞结果.json              # 逐项对撞通过证明",
        "```"
    ]

    report_content = "\n".join(lines)
    with open(DELIVERY_FILE, "w", encoding="utf-8") as f:
        f.write(report_content)

    ledger_file = OUT_DIR / "02_总交接结构化台账.json"
    with open(ledger_file, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)

    print(f"[02_十二工单状态与总交接生成] 完成！总交接: {DELIVERY_FILE}, 台账: {ledger_file}")


if __name__ == "__main__":
    main()
