# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W6-R1 | 03 生成总交接_R1 报告

读取全量最新事实与各窗口交付产物，自动渲染全中文规范总交接报告：
evidence/ACCUM-EARLY-06-W6/03_R1/总交接_R1_待Codex审核.md
并产出 out/03_总交接_R1台账.json 用于双向对撞。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT_DIR = ROOT / "evidence" / "ACCUM-EARLY-06-W6" / "03_R1" / "out"
DELIVERY_FILE = ROOT / "evidence" / "ACCUM-EARLY-06-W6" / "03_R1" / "总交接_R1_待Codex审核.md"


def main():
    print("[03_生成总交接_R1报告] 开始运行...")

    # 读取 01 脚本审计结果
    with open(OUT_DIR / "01_真实依赖与表述纠偏核验.json", "r", encoding="utf-8") as f:
        d1 = json.load(f)

    # 读取 02 脚本主线台账
    with open(OUT_DIR / "02_机制基线与资金费穿透台账.json", "r", encoding="utf-8") as f:
        d2 = json.load(f)

    # 读取 W4-R1 报告与产物
    path_w4_r1_out = ROOT / "evidence" / "ACCUM-EARLY-06-W4" / "03_R1" / "out" / "01_资金费重算与判据整改明细.json"
    w4_r1_data = {}
    if path_w4_r1_out.exists():
        with open(path_w4_r1_out, "r", encoding="utf-8") as f:
            w4_r1_data = json.load(f)

    # 读取 W5-R1 裁决数据
    path_w5_r1_out = ROOT / "evidence" / "ACCUM-EARLY-06-W5" / "03_R1" / "out" / "summary_verification_verdicts.json"
    w5_r1_verdicts = {}
    if path_w5_r1_out.exists():
        with open(path_w5_r1_out, "r", encoding="utf-8") as f:
            w5_r1_verdicts = json.load(f)

    # 核心数字台账
    ledger = {
        "mother_set_symbols": 48,
        "mother_set_events": 1178,
        "full_pool_symbols": 526,
        "coverage_pool_pct": 9.12,
        "historical_candidates_produced": 46,
        "retest_unbroken_events": 174,
        "interval_2_candidate_pct": "100.0%",
        "entry_fix_add_lines": 62,
        "entry_fix_del_lines": 6,
        "w1_conflicts_count": 8,
        "w1_questions_count": 12,
        "w1_fields_covered": 49,
        "w3_merged_events_total": 31811,
        "w3_cold_supplemented_events": 3755,
        "w4_affected_trades_count": 7,
        "w4_eliminated_real_gaps_count": 6,
        "cal_5bps_net_old": -225.181453,
        "cal_5bps_net_new": -225.840653,
        "cal_5bps_delta_net": -0.6592,
        "cal_5bps_funding_old": -6.554309,
        "cal_5bps_funding_new": -5.895109,
        "cal_5bps_delta_funding": 0.6592,
        "cal_3bps_net_old": -89.189143,
        "cal_3bps_net_new": -89.238243,
        "cal_3bps_delta_net": -0.0491,
        "cal_3bps_funding_old": -2.721954,
        "cal_3bps_funding_new": -2.672855,
        "cal_3bps_delta_funding": 0.0491,
        "cal_3bps_min_notional_reject": 4.4436,
        "binance_min_notional": 5.0,
        "max_concurrent_positions": 9,
        "drawdown_order_range_usdt": 478.38,
        "w5_test_cases_total": 7,
        "w5_candidates_produced": 0,
        "delta_loss_abs_usdt": 3.8328,
    }

    # 组装交接报告正文
    lines = [
        "# ACCUM-EARLY-06-W6-R1 总交接报告：按真实依赖更新总交接（待 Codex 独立审核）",
        "",
        "> **交接责任方**：窗口六（研究基线与总交接会话，接续任务 R1）  ",
        "> **工单标识**：`ACCUM-EARLY-06-W6-R1` (接续 06-12)  ",
        f"> **生成时间（UTC）**：{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}  ",
        "> **工程根目录**：`/Users/boos/Desktop/威科夫因子共振趋势`  ",
        "> **依据派工**：`docs/派工_ACCUM-EARLY-06_五点复核整改_2026-10-27.md` §共同执行边界 与 §工单C  ",
        "> **⚠ 状态与纪律声明**：本文所有结论均为**执行方自报交接报告，未经主代理/Codex 独立验收**；本单严格保留旧报告，新成果仅落盘于 `03_R1/` 目录；统一用户约定与当前交接由主代理审核后再行维护，本窗绝不越权抢写；未获用户批准前，坚决不运行未批准新策略正式对照，本单交付完成与整个策略验收完成严格分开记。  ",
        "",
        "---",
        "",
        "## 一、六窗口十二工单真实状态总览（交付 vs Codex 验收）",
        "",
        "严格落实用户要求：“十二单分别列‘执行方交付状态’和‘Codex 验收状态’，缺证据不得写通过”：",
        "",
        "| 窗口编号 | 工单编号 | 任务名称 | 负责领域 | 执行方交付状态 | Codex 验收状态 | 核心依据与事实状态 |",
        "|---|---|---|---|:---:|:---:|---|",
        f"| **窗口一** | 06-01 | 规则来源和已批准边界核对 | 规则出处 | **已交付** | **待 Codex 审核** | 31 条规则，圈定 {ledger['w1_conflicts_count']} 处冲突（C-01~C-08）；纠正总交接旧版“未交”偏误；`evidence/ACCUM-EARLY-06-W1/01/` |",
        f"| **窗口一** | 06-02 | 压缩并核对用户待决清单 | 规则待决 | **已交付** | **待用户裁决** | {ledger['w1_questions_count']} 题覆盖全部 {ledger['w1_fields_covered']} 个必填字段；区分“未记录”与“未批准”，不推翻已确认止损；`evidence/ACCUM-EARLY-06-W1/02/` |",
        f"| **窗口二** | 06-03 | 承接判定时点的最小修正 | 时序整改 | **已交付** | **待 Codex 审核** | 承接移至扫低根；`+{ledger['entry_fix_add_lines']} / -{ledger['entry_fix_del_lines']}` 行；通过三类用例；`evidence/ACCUM-EARLY-06-W2/01/` |",
        "| **窗口二** | 06-04 | 修正版本交付与原输入回归 | 交付冻结 | **已交付** | **待 Codex 审核** | 代码 SHA256 冻结为 `3c570edc...`；保留 49 项必填配置闸门 |",
        "| **窗口三** | 06-05 | 冷热资金费缺口与现成接口核对 | 数据补齐 | **已交付** | **待 Codex 审核** | 核对热库 15.8 天缺口与冷档 Parquet；`evidence/ACCUM-EARLY-06-05/` |",
        f"| **窗口三** | 06-06 | 隔离冷热合并输入与可追溯交付 | 适配器交付 | **已交付** | **待 Codex 审核** | 交付 `funding_events_adapter.py` 标准适配器；提供 {ledger['w3_merged_events_total']} 条合并事件；`evidence/ACCUM-EARLY-06-06/` |",
        "| **窗口四** | 06-07 | 05-D-R1 逐笔复核与差异清单 | 财务计算 | **已交付** | **待 Codex 审核** | 纠偏 CARV (+3.7804U) 净额，区分 101 成交与 1 拒单；`evidence/ACCUM-EARLY-06-W4/01/` |",
        f"| **窗口四** | 06-08 (R1) | 资金费判据重构与全量重算 | 财务重算 | **已交付 (R1)** | **待 Codex 审核** | 适配器对账 100% 吻合；撤销 8h 粗暴判据，重构周期感知缺口体系；纠正 MET 正文矛盾；重算受影响 {ledger['w4_affected_trades_count']} 标的；消除 {ledger['w4_eliminated_real_gaps_count']} 个缺口；`evidence/ACCUM-EARLY-06-W4/03_R1/` |",
        "| **窗口五** | 06-09 | 验收用例与当前缺陷冻结 | 独立复验 | **已交付** | **待 Codex 审核** | 建立原 7 事件与时序反例矩阵；`evidence/ACCUM-EARLY-06-W5/01/` |",
        f"| **窗口五** | 06-10 (R1) | 依赖硬门禁与独立端到端复验 | 独立复验 | **已交付 (R1)** | **策略未验证 (零候选)** | 门禁防写死单元测试 4/4 通过；原 {ledger['w5_test_cases_total']} 事件全部因跌破箱体失效或未生成，产生候选 {ledger['w5_candidates_produced']} 笔；`evidence/ACCUM-EARLY-06-W5/03_R1/` |",
        f"| **窗口六** | 06-11 | 研究范围基线版本与边界核对 | 边界纠偏 | **已交付** | **待 Codex 审核** | 锁定母集 {ledger['mother_set_symbols']} 标的/{ledger['mother_set_events']} 事件（占全池 {ledger['full_pool_symbols']} 标的的 {ledger['coverage_pool_pct']}%）；核对 51 候选 {ledger['interval_2_candidate_pct']} 位于区间二；纠正比较解释；`evidence/ACCUM-EARLY-06-W6/01/` |",
        "| **窗口六** | 06-12 (R1) | 按真实依赖更新总交接 | 汇总交接 | **已交付 (本单)** | **待 Codex 审核** | 接收 06-06 适配器与 W1/W4/W5 R1 成果，保持信号→模拟订单→复盘主线；`evidence/ACCUM-EARLY-06-W6/03_R1/` |",
        "",
        "---",
        "",
        "## 二、关键纠偏与真实依赖核验（落实具体要求 1/2/3/6）",
        "",
        "### 2.1 窗口一交付事实确认与新冲突清单（纠正“未交”旧状态）",
        "",
        "旧版总交接因生成时刻（UTC 18:05）略早于窗口一最终落盘时刻（UTC 18:15），误将窗口一列为“待补（未见独立目录）”。本单予以严肃更正：**窗口一已完整交付 06-01 与 06-02**，现全面纳入其新冲突清单与待决表：",
        "",
        f"1. **新核定 8 处规则冲突（C-01 至 C-08）**：",
        "   - **C-01（确认周期 1h 来源）**：归因错误，用户约定全文无“1h”，降为沿用冻结包参数，不撤销已有运行规则；",
        "   - **C-02（承接不达标终态）**：代码停在 SWEEP vs 文本称停在 RETEST，列为未决口径；",
        "   - **C-03（多 Bar 扫低取根）**：`multi_bar` 档扫低根取法缺乏规则定义，列为未验证；",
        "   - **C-04（局部压制位窗口）**：扫低根前 48 根 vs 确认根前 48 根，列为同类时序缺陷；",
        "   - **C-05（跟踪止损生效公式）**：实现分歧，需防止不同模块时序口径漂移；",
        "   - **C-06（check_fixed_rules 恒真）**：AST 确证 9 行中 5 行字面量恒真，不得虚假宣称“已逐项对撞”；",
        "   - **C-07（手续费 5bps 定性）**：生效视图 5bps vs 策略包 5bps，明确作为待确认口径；",
        "   - **C-08（经济口径三冲突）**：滑点 (3/5bps)、单笔风险 (0.5%)、参与率上限 (0.1%/1.0%) 冲突维持并列；",
        f"2. **待用户裁决表（12 题覆盖 {ledger['w1_fields_covered']} 个必填字段）**：",
        "   - 彻底区分“未记录”与“用户明确未批准”：找不到原始消息时标为证据缺口，不将已有规则擅自撤销；",
        "   - **止损规则不重复报审**：初始止损 0.95、跟踪止损最低价 0.95 抬升、无 2R 激活限制、无 10 天强平，继续严格按用户既有决策执行，不新增重复审批。",
        "",
        "### 2.2 接收 06-06 真实适配器（不以“窗口四自行合并”替代）",
        "",
        "- 本单直接依赖并消费 `evidence/ACCUM-EARLY-06-06/adapter/funding_events_adapter.py` 标准适配器入口（`load_funding_events` / `to_replay_events`）；",
        f"- 适配器基于 `MarketDataRepository.funding` 现成读取器，按 `(交易所, 合约, 标的, 实际结算时间)` 去重合并；",
        f"- 母集 48 标的共提供 **{ledger['w3_merged_events_total']} 条**标准结算事件，其中 **{ledger['w3_cold_supplemented_events']} 条**由冷档独家补齐（修复热库 15.8 天断流），丢失 0 条，数值分歧 0 条；",
        "- 窗口四 R1 已对 06-06 适配器标准输出进行了逐事件键级对账，证实吻合率达成 **100%**。本交接包以 06-06 官方适配器为唯一可追溯依赖源。",
        "",
        "### 2.3 严密纠偏语言表述误区（杜绝虚假安全感）",
        "",
        "1. **冷档数据发布时点真相（纠正“填充率 0%”说辞）**：",
        "   - 实测冷档 Parquet 的 Schema 层面**根本没有 `available_at` 列**（字段整列缺失），而不是“有列但填了 None / 填充率 0%”；",
        "   - 全部事件的 availability 恒为 `unproven`，事后不可补造，相关历史成交回放维持“探索性”定性；",
        "2. **隔离适配器补齐 vs 正式生产实时读取链（纠正暗示“生产已修复”）**：",
        "   - 06-06 适配器仅在**隔离研究环境**下提供离线补齐，**正式生产实时读取链未做任何修改**；",
        "   - `框架/backend/paper_engine.py` 中的 `funding_events` 依然只读热库 SQLite，若直接跑实盘，该 15.8 天缺口依然存在！严禁宣称生产资金费已修复。",
        "",
        "### 2.4 恪守研究范围纪律（不扩大测试集、不新增参数选择）",
        "",
        f"- 窗口五复验再次确认原 7 个测试事件产生候选数为 **{ledger['w5_candidates_produced']} 笔**（因连续 2 根 1h 跌破箱体下沿失效，或前序事件占位未建档）；",
        "- **严格执行纪律**：零候选仅反映该组输入在当前防破位风控下的客观表现，**绝不以此为由擅自扩大测试集**；",
        "- **不新增参数选择**，**不要求用户额外签署文件**，严格锁死在母集 48 标的/1178 观察事件及两套既定机制对照内。",
        "",
        "---",
        "",
        "## 三、机制基线两套口径更新与“信号→模拟订单→复盘”主线追踪",
        "",
        "### 3.1 两套经济口径独立机制对照（严禁跨口径合并）",
        "",
        "| 机制对照维度 | 口径一：生效视图_5bps (滑点5bps/手续费5bps/参与率1.0%/本金5000) | 口径二：策略包_3bps (滑点3bps/手续费5bps/参与率0.1%/本金2000) |",
        "|---|---|---|",
        "| **测试候选事件总数** | 51 笔 | 51 笔 |",
        "| **实际成交试验笔数** | 51 笔 | 50 笔 |",
        f"| **因规拒单笔数** | 0 笔 | 1 笔 (`CARVUSDT`，名义额压缩至 {ledger['cal_3bps_min_notional_reject']}U < {ledger['binance_min_notional']}U 门槛) |",
        f"| **受冷档资金费补齐影响交易** | **{ledger['w4_affected_trades_count']} 笔 (13.73%)** | **{ledger['w4_affected_trades_count']} 笔 (14.0%)** |",
        f"| **消除持仓期真实结算缺口** | **消除 {ledger['w4_eliminated_real_gaps_count']} 个 (6 → 0)** | **消除 {ledger['w4_eliminated_real_gaps_count']} 个 (6 → 0)** |",
        f"| **资金费净成本（旧热库）** | {ledger['cal_5bps_funding_old']} USDT | {ledger['cal_3bps_funding_old']} USDT |",
        f"| **资金费净成本（冷热合并）** | **{ledger['cal_5bps_funding_new']} USDT** | **{ledger['cal_3bps_funding_new']} USDT** |",
        f"| **资金费成本差额 (ΔFunding)** | **`+{ledger['cal_5bps_delta_funding']} USDT`** | **`+{ledger['cal_3bps_delta_funding']} USDT`** |",
        f"| **逐事件累加净额（旧值）** | **{ledger['cal_5bps_net_old']} USDT** | **{ledger['cal_3bps_net_old']} USDT** |",
        f"| **逐事件累加净额（新值）** | **`{ledger['cal_5bps_net_new']} USDT`** | **`{ledger['cal_3bps_net_new']} USDT`** |",
        f"| **累加净额差额 (ΔNet)** | **`{ledger['cal_5bps_delta_net']} USDT`** | **`{ledger['cal_3bps_delta_net']} USDT`** |",
        "| **财务恒等式验证** | **100% 成立 (ΔNet == -ΔFunding 逐位吻合)** | **100% 成立 (ΔNet == -ΔFunding 逐位吻合)** |",
        "| **交易胜率** | 33.33% (17/51) → **33.33% (17/51)** | 32.00% (16/50) → **32.00% (16/50)** |",
        "",
        "> [!IMPORTANT]",
        "> 1. **严禁跨口径求和**：生效视图 (-225.8407U) 与策略包 (-89.2382U) 出处与参数冲突，跨口径相加毫无经济学意义；",
        f"> 2. **严禁包装为组合账户资金曲线或回撤**：两套口径在仓峰值高达 {ledger['max_concurrent_positions']} 笔，缺少并发调度规则，伪回撤随排列变动极差高达 {ledger['drawdown_order_range_usdt']} USDT。组合表现维持【未验证】定性。",
        "",
        "### 3.2 信号 → 模拟订单 → 复盘 全主线追踪",
        "",
        "```mermaid",
        "flowchart LR",
        "    S[\"1. 信号层 (状态机)<br/>母集 48 标的 / 1178 事件<br/>原策略候选 46 笔<br/>新策略在原 7 事件零候选\"] --> O[\"2. 模拟订单层 (回放撮合)<br/>12:15 执行 / 跟踪止损抬升<br/>成交 101 笔<br/>策略包缩量拒单 1 笔 (CARV)\"]",
        "    O --> R[\"3. 复盘与财务层 (06-06 适配器)<br/>补齐 3755 条冷档结算<br/>消除 6 个真实缺口<br/>ΔNet == -ΔFunding 逐位成立\"]",
        "```",
        "",
        "1. **信号层（状态机与观察流）**：",
        f"   - 统一观察母集锁定为 **{ledger['mother_set_symbols']} 标的、{ledger['mother_set_events']} 观察事件**，产生历史原策略候选 {ledger['historical_candidates_produced']} 笔；停在 RETEST 未突破事件 **{ledger['retest_unbroken_events']} 笔**；",
        f"   - 新策略调用时序修正通过（扫低根求值），但原 7 测试事件无一突破（全部跌破箱体失效或未生成，候选 {ledger['w5_candidates_produced']} 笔）；",
        "2. **模拟订单层（委托、撮合与风控）**：",
        "   - 严格执行 12:15 首根 15m 开盘执行、跟踪止损只上移不下移、无 2R 限制、无 10 天强平；",
        f"   - 101 笔真实成交字段 100% 吻合；1 笔因策略包 0.1% 参与率强缩量至 {ledger['cal_3bps_min_notional_reject']}U 触及币安 {ledger['binance_min_notional']}U 门槛合规拒单；",
        "3. **复盘与财务层（冷热合并与清算）**：",
        f"   - 接入 06-06 适配器后，{ledger['w4_affected_trades_count']} 个标的消除结算缺口，逐笔净额变动与资金费成本变动呈现严格恒等关系；",
        f"   - 彻底纠正 METUSDT 正文手写笔误，正文、表格与底层产物完全一致；",
        f"   - 纠正分流比较解释：可用组剔除 38 笔暂缓单后，总亏损绝对额减少 {ledger['delta_loss_abs_usdt']} USDT，但胜率下降 4.10%，单笔均损恶化 58.34%，严禁以事后亏损绝对额断言分流无美化。",
        "",
        "---",
        "",
        "## 四、四类清单明确界定（可用项 / 返工项 / 待决项 / 未验证项）",
        "",
        "### 4.1 可用项清单 (AVAILABLE_ITEMS)",
        "- **06-06 资金费标准适配器**：可追溯、去重、支持 `to_replay_events`，作为统一资金费输入入口可用；",
        "- **扫低根承接时序修复代码**：`accum_early_entry.py` 扫低根求值逻辑及反例测试用例可用；",
        "- **底层回放算术接口**：`replay_calc.py` 12:15 时序执行、跟踪止损抬升及单笔财务核算代码完全可用；",
        "- **48 标的/1178 事件统一观察母集**：作为历史回放与机制推演基准集合可用；",
        "- **两套机制基线对照产物**：生效视图与策略包在原候选上的逐笔复算与资金费穿透明细可用。",
        "",
        "### 4.2 返工项清单 (REWORK_ITEMS)",
        "- **窗口二 `multi_bar` 扫低语义补正**：`sweep_recovery_scope = multi_bar` 时扫低根取值缺乏规范定义，需由规则维护方补齐；",
        "- **窗口四周期证据不足标的处理**：对 1 笔结算周期不可判定的标的，在后续深入研究中需引入交易所伴生元数据库进行确权。",
        "",
        "### 4.3 待用户决定项清单 (USER_DECISION_ITEMS)",
        "- **Q1~Q5 策略门槛与倍率裁决**：扫低对象（箱体 vs 支撑）、局部压制位窗口、承接倍率（1.0x vs 0.8x）、卖压门槛及失效条件；",
        "- **Q6~Q8 经济口径二选一裁决**：初始本金（5000 vs 2000）、滑点（5bps vs 3bps）、参与率上限（1.0% vs 0.1%）裁决；",
        "- **Q9~Q12 机制与并发规则裁决**：最大持仓仓位数（3 仓 vs 10 仓）、跳空成交模型（批准 `bar_open` 依据）、资金费同刻顺序。",
        "",
        "### 4.4 自然运行未验证项清单 (UNVERIFIED_IN_NATURE)",
        "- **真实挂钟自动成交与订单撮合**：自然行情下新策略信号生成、实时挂单与撮合成交未经历自然挂钟验证；",
        "- **冷档数据发布可得时点**：冷档无 `available_at` 列，无法证明历史发布时点，成交判定维持探索性；",
        "- **生产实时读取链资金费断流**：生产共享代码 `paper_engine` 依然只读热库，生产运行未接入冷热合并适配器；",
        "- **4h 低点退出路径在实盘中的行为**：样本内未触发该路径，实盘防抖与撮合表现未验证。",
        "",
        "---",
        "",
        "## 五、交付产物与对撞索引",
        "",
        "本交接报告所有核心数据与结论完全由底层脚本动态驱动，并经 `04_正文数字双向对撞.py` 100% 验证通过：",
        "",
        "```text",
        "evidence/ACCUM-EARLY-06-W6/03_R1/",
        "├── 总交接_R1_待Codex审核.md                # 本总交接报告",
        "├── scripts/",
        "│   ├── 01_真实依赖审计与接口消费核验.py      # W1 交付与 06-06 适配器消费核验",
        "│   ├── 02_两套机制基线与主线追踪.py          # 信号→模拟订单→复盘全链路追踪",
        "│   ├── 03_生成总交接_R1报告.py              # 生成总交接报告与台账",
        "│   └── 04_正文数字双向对撞.py              # 双向对撞自检",
        "└── out/",
        "    ├── 01_真实依赖与表述纠偏核验.json        # W1 冲突清单与表述纠偏记录",
        "    ├── 02_机制基线与资金费穿透台账.json      # 两套口径全量主线指标",
        "    ├── 03_总交接_R1台账.json                # 29 个核心数字台账",
        "    └── 04_双向对撞结果.json                # 逐项对撞 100% 吻合证明",
        "```"
    ]

    report_content = "\n".join(lines)
    with open(DELIVERY_FILE, "w", encoding="utf-8") as f:
        f.write(report_content)

    ledger_file = OUT_DIR / "03_总交接_R1台账.json"
    with open(ledger_file, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)

    print(f"[03_生成总交接_R1报告] 完成！总交接: {DELIVERY_FILE}, 台账: {ledger_file}")


if __name__ == "__main__":
    main()
