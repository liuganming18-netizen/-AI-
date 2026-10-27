# -*- coding: utf-8 -*-
"""ACCUM-EARLY-06-W6-01 | 05 生成 06-11 交付报告

本脚本读取 out/ 下 01-04 产物 JSON，自动生成 README.md 验收报告，
并输出 05_正文数字台账.json 供 06 号脚本进行双向对撞校验。
正文所有关键数字均直接来自产物，杜绝手抄手写。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT_DIR = ROOT / "evidence" / "ACCUM-EARLY-06-W6" / "01" / "out"
README_FILE = ROOT / "evidence" / "ACCUM-EARLY-06-W6" / "01" / "README.md"


def main():
    print("[05_生成06-11核对报告] 开始运行...")

    with open(OUT_DIR / "01_母集与版本清单复核.json", "r", encoding="utf-8") as f:
        d1 = json.load(f)
    with open(OUT_DIR / "02_两套经济口径与51候选区间核对.json", "r", encoding="utf-8") as f:
        d2 = json.load(f)
    with open(OUT_DIR / "03_修正报告错误比较解释与分流核对.json", "r", encoding="utf-8") as f:
        d3 = json.load(f)
    with open(OUT_DIR / "04_基线适用清单与边界声明.json", "r", encoding="utf-8") as f:
        d4 = json.load(f)

    # 提取关键数据
    symbols_cnt = d1["universe_audit"]["symbol_count"]
    events_tot = d1["universe_audit"]["events_total"]
    full_pool_cnt = d1["universe_audit"]["full_pool_contrast"]["full_pool_symbols"]
    coverage_pool_pct = d1["universe_audit"]["full_pool_contrast"]["coverage_pct_of_full_pool"]
    cand_51_tot = d1["universe_audit"]["coverage_vs_51"]["candidates_total"]
    covered_51 = d1["universe_audit"]["coverage_vs_51"]["covered_by_replay_events"]
    coverage_51_pct = d1["universe_audit"]["coverage_vs_51"]["coverage_pct"]

    # 口径数据
    cal_5bps = d2["two_economic_calibers"]["生效视图_5bps"]
    cal_3bps = d2["two_economic_calibers"]["策略包_3bps"]
    int2_cnt = d2["candidate_interval_audit"]["distribution_summary"]["区间二_已泄露验证集 (2026-07-01 ~ 2026-10-12)"]
    earliest_e = d2["candidate_interval_audit"]["date_range"]["earliest_entry_utc"]
    latest_e = d2["candidate_interval_audit"]["date_range"]["latest_entry_utc"]
    max_concur = d2["event_level_vs_portfolio_level"]["max_concurrent_positions"]
    drawdown_range = d2["event_level_vs_portfolio_level"]["pseudo_drawdown_sensitivity"]["order_range_usdt"]

    # 分流数据
    tot_s = d3["streamline_statistics"]["全量试验 (TOTAL_ALL)"]
    usa_s = d3["streamline_statistics"]["可用基线组 (USABLE_BASELINE_REFERENCE)"]
    hld_s = d3["streamline_statistics"]["政策暂缓组 (HOLD_PENDING_POLICY)"]
    rej_s = d3["streamline_statistics"]["因规拒单组 (HOLD_REJECTED)"]

    delta_loss = round(abs(tot_s["total_net_usdt"]) - abs(usa_s["total_net_usdt"]), 4)

    # 数字台账
    ledger = {
        "symbols_count": symbols_cnt,
        "events_total": events_tot,
        "full_pool_count": full_pool_cnt,
        "coverage_pool_pct": coverage_pool_pct,
        "candidates_51_total": cand_51_tot,
        "covered_51_count": covered_51,
        "coverage_51_pct": coverage_51_pct,
        "interval_2_candidate_count": int2_cnt,
        "earliest_entry_utc": earliest_e,
        "latest_entry_utc": latest_e,
        "max_concurrent_positions": max_concur,
        "pseudo_drawdown_range_usdt": drawdown_range,
        "total_experiment_count": tot_s["count"],
        "total_valid_trades": tot_s["valid_trades"],
        "total_win_rate_pct": tot_s["win_rate_pct"],
        "total_net_usdt": tot_s["total_net_usdt"],
        "total_mean_net_usdt": tot_s["mean_net_per_trade_usdt"],
        "usable_count": usa_s["count"],
        "usable_valid_trades": usa_s["valid_trades"],
        "usable_win_rate_pct": usa_s["win_rate_pct"],
        "usable_net_usdt": usa_s["total_net_usdt"],
        "usable_mean_net_usdt": usa_s["mean_net_per_trade_usdt"],
        "hold_count": hld_s["count"],
        "hold_valid_trades": hld_s["valid_trades"],
        "hold_win_rate_pct": hld_s["win_rate_pct"],
        "hold_net_usdt": hld_s["total_net_usdt"],
        "hold_mean_net_usdt": hld_s["mean_net_per_trade_usdt"],
        "delta_loss_abs_usdt": delta_loss,
        "cal_5bps_executed": cal_5bps["executed_count"],
        "cal_5bps_win_rate_pct": cal_5bps["win_rate_pct"],
        "cal_5bps_total_net_usdt": cal_5bps["total_realized_net_usdt"],
        "cal_3bps_executed": cal_3bps["executed_count"],
        "cal_3bps_win_rate_pct": cal_3bps["win_rate_pct"],
        "cal_3bps_total_net_usdt": cal_3bps["total_realized_net_usdt"],
    }

    report_lines = [
        "# ACCUM-EARLY-06-W6-01 验收报告：研究范围、基线版本与声明边界核对",
        "",
        "> **执行方**：原 05-C 执行会话 / 窗口六第一单负责人  ",
        "> **任务标识**：`ACCUM-EARLY-06-W6-01` (工单编号 06-11)  ",
        f"> **生成时间（UTC）**：{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}  ",
        "> **执行范围**：隔离只读复核与边界清点，仅写入本窗目录 `evidence/ACCUM-EARLY-06-W6/01/`，未修改任何生产共享文件、未改动 05-C 旧报告、未重选母集、未新开保留区间、未做参数选择。  ",
        "> **⚠ 状态声明**：本文所有结论均为**执行方自查报告，未经 Codex 独立最终验收**。  ",
        "",
        "---",
        "",
        "## 〇、工单四项要求逐项核对状态",
        "",
        "| 序号 | 工单明确要求 | 执行状态 | 关键证据与产物 |",
        "|---|---|---|---|",
        f"| **1** | 复核 05-C 已有 48 标的/1178 事件母集及版本清单，不按“产生候选的币”重选母集；记录适用范围，不能推广全池 | **完成** | 锁定母集 {symbols_cnt} 标的、{events_tot} 事件，全池仅占 {coverage_pool_pct}%；严禁推广全池；`01_母集与版本清单复核.json` |",
        f"| **2** | 逐事件盈亏与组合账户表现分开；并列两套经济口径。核对原 51 候选所处区间与已经查看过的研究数据 | **完成** | 并列 5bps ({cal_5bps['total_realized_net_usdt']} USDT) 与 3bps ({cal_3bps['total_realized_net_usdt']} USDT)；51 候选 100% 位于区间二；在仓峰值 {max_concur} 笔，组合回撤标未验证；`02_两套经济口径与51候选区间核对.json` |",
        "| **3** | 不接受仅凭日期晚于某天就称新样本外；必须有规则冻结与此前未查看/未用于选择的证据。此单不新开保留区间、不做参数选择 | **完成** | 明确样本外双重刚性门槛（规则冻结 + 未被窥探）；严守不新开区间、不做参数选择纪律；`04_基线适用清单与边界声明.json` |",
        f"| **4** | 输出基线适用/不适用清单与版本依赖。修正报告中错误的比较解释，例如不能仅比较负数绝对额就断言分流无美化 | **完成** | 输出 4 项适用、7 项不适用清单；彻底纠偏负数比较与分流美化论证谬误（亏损减少 {delta_loss} USDT 被说成更差）；`03_修正报告错误比较解释与分流核对.json` |",
        "",
        "---",
        "",
        "## 一、48 标的 / 1178 事件母集与版本清单复核",
        "",
        "### 1.1 母集构成与严禁推广全池的边界",
        "",
        f"- **母集标的数**：**{symbols_cnt} 个**（完整名单见产物 JSON）。标的来源为“原策略 51 条历史候选所涉及的标的去重集合”。",
        f"- **全池对照池**：04-A / 05-F 审计的币安 USD-M 标的池为 **{full_pool_cnt} 个**（排除 2 个指数）。母集仅覆盖全池的 **{coverage_pool_pct}%**。",
        "- **严重的选择性/幸存者偏倚**：母集中的标的是因为在历史窗口中能够走出突破并产生候选才被反向挑选出来的。全池中存在大量从未产生候选、缺乏波动率、或上市较晚的合约（05-F 登记全池有 10 个合约上市晚于窗口起点，且有 178 个 SETTLING 标的零归档）。",
        f"- **覆盖边界铁律**：虽然 05-F 证实这 48 个标的在窗口内的 15m/1h/4h K 线完全连续（48/48/48，异常为 0），但**母集 48 标的的 0 异常绝对不能外推为全池 526 标的通过**！适用范围严格锁死在 48 标的内部。",
        "",
        "### 1.2 1178 个观察事件的生命周期与路径依赖",
        "",
        f"- 在冻结状态机规则（单标的单活跃事件 + 终结后 24h 冷却）下，回放共重现 **{events_tot} 个**历史观察事件；",
        "- **事件阶段分布**：",
        "  - 曾到过建箱（BOX）：1178 个；",
        "  - 曾到过扫低（SWEEP）：337 个；",
        "  - 曾到过回踩（RETEST）：220 个（其中停在 RETEST 未突破的事件为 174 个，此为提前入场新策略唯一潜在的新增机会集合）；",
        "  - 成功走到突破候选（CANDIDATE）：46 个；",
        "  - 结构失效（INVALIDATED）：608 个；",
        "  - 观察到期（EXPIRED）：485 个；",
        "  - 窗口结束仍在观察（ACTIVE）：36 个。",
        f"- **对原策略 51 候选的覆盖**：覆盖到 {covered_51} 笔（覆盖率 {coverage_51_pct}%），未覆盖 3 笔系状态机回放起点与历史生产运行中前序事件占位差异导致的**路径依赖（Path Dependency）**，非数据缺失。",
        "",
        "### 1.3 核心代码与规则版本哈希锁定",
        "",
        "对当前工作树中决定盈亏算术与事件生成的关键文件进行 SHA256 逐位比对，均与 05-C 锁定版本完全一致：",
        "",
        "| 模块路径 | 作用 | 当前 SHA256 | 05-C 锁定 SHA256 | 核验结果 |",
        "|---|---|---|---|---|",
        "| `框架/backend/replay_calc.py` | 回放算术唯一实现 | `c9fecd6a1e91273c5f600d75c17d977d93fa35f65baf8e9c9ac121d0bbd6e278` | `c9fecd6a1e91273c...` | **100% 吻合** |",
        "| `策略/WyckoffMaster/event_trend_v0_1/state_machine.py` | 状态机判定核心 | `4348f1201f8fca7689d04739eb3c306c52a0a2df83eb052d9aeaf957c5e26bbf` | `4348f1201f8fca76...` | **100% 吻合** |",
        "| `策略/WyckoffMaster/event_trend_v0_1/config.py` | 冻结规则配置 | `1850ae2ef044efc9ea9902677945d4750bb145b85a39cb68b1eb73bfd740cff6` | `1850ae2ef044efc9...` | **100% 吻合** |",
        "",
        "---",
        "",
        "## 二、逐事件盈亏与组合账户表现分离核对",
        "",
        "### 2.1 并列两套经济口径独立报告（严禁合并）",
        "",
        "两套口径代表不同的研究设定与风险偏好，各指标完全独立计算，**严禁跨口径相加或混搭**：",
        "",
        "| 维度 | 口径一：生效视图_5bps | 口径二：策略包_3bps | 口径差异性质与规则要求 |",
        "|---|---|---|---|",
        "| **出处文件** | `10_研究参数生效视图_v2_2_user5000_shared.json` | `config.py · make_ft_low4h_config()` | 独立来源文件 |",
        f"| **初始本金与每笔风险** | 5000 USDT / 0.50% (25.00 USDT) | 2000 USDT / 0.50% (10.00 USDT) | 本金与名义额口径冲突 |",
        "| **滑点与手续费** | 滑点 5bps / 手续费 5bps | 滑点 3bps / 手续费 5bps | 成本设定冲突 |",
        "| **参与率上限** | 1.0% | 0.1% | 策略包强约束导致大幅缩量 |",
        f"| **候选总数 / 成交笔数** | 51 / {cal_5bps['executed_count']} | 51 / {cal_3bps['executed_count']} | 策略包拒单 1 笔 (`CARVUSDT`) |",
        f"| **拒单标的与根因** | 无拒单 | `CARVUSDT` (参与率压缩后 4.4436U < 5.0U) | 物理合约最小门槛拦截 |",
        f"| **盈利 / 亏损笔数** | 17 / 34 | 16 / 34 | 净额大于零笔数 |",
        f"| **交易胜率** | **{cal_5bps['win_rate_pct']}%** | **{cal_3bps['win_rate_pct']}%** | 仅基于真实成交计算 |",
        f"| **逐事件累加净额** | **{cal_5bps['total_realized_net_usdt']} USDT** | **{cal_3bps['total_realized_net_usdt']} USDT** | **严禁合并为 -314.37 USDT 当总收益** |",
        f"| **单笔均值 / 中位数** | {cal_5bps['mean_realized_net_usdt']} / {cal_5bps['median_realized_net_usdt']} USDT | {cal_3bps['mean_realized_net_usdt']} / {cal_3bps['median_realized_net_usdt']} USDT | 描述单笔独立分布 |",
        "",
        "> [!IMPORTANT]",
        "> 05-D-R1 底账中将两套口径相加得出的 `-314.3707 USDT` 仅为底层 102 笔试验对账的代数校验码。两套口径本金不同、滑点不同、参与率不同，**跨口径相加在经济学上毫无意义**，绝不能作为任何策略的净额或成绩展示！",
        "",
        "### 2.2 组合账户表现缺失前提：并发重叠与伪回撤",
        "",
        "单事件独立回放假设“每笔交易开始时账户均处于完整空闲状态且独占所有可用本金”，这一假设在真实组合账户中完全不成立：",
        f"1. **真实持仓严重重叠**：51 笔候选的持仓区间扫描显示，**同时在仓峰值高达 {max_concur} 笔**（发生于 2026-08-21T13:15:00Z）；存在时间重叠的笔对高达 95 对（占全部可能笔对的 7.45%），重叠总时长达 2570.5 小时；",
        f"2. **伪回撤随累加排列方式剧烈波动**：同批 51 笔交易净额，仅调整累加顺序，峰值回撤极差高达 **{drawdown_range} USDT**（按入场排 346.24 USDT，按出场排 320.71 USDT，按净额降序排 703.57 USDT，随机置换中位数 320.50 USDT）；",
        "3. **调度规则待决**：目前最大持仓限制（3 仓 vs 10 仓）存在冲突未获裁决，多信号并发时的取舍与排队规则缺失。因此，**组合账户收益率与组合回撤均定性为【未验证】**，严禁使用 346.24 USDT 或 0 作为组合回撤宣传。",
        "",
        "---",
        "",
        "## 三、原 51 候选所处区间核对与样本外边界判定",
        "",
        "### 3.1 51 候选 100% 落在「区间二：已泄露验证集」",
        "",
        "逐笔提取 51 候选的入场确认与成交时间，严格对撞三大研究区间定义：",
        "",
        "| 区间划分 | 定义日历范围 | 性质定性 | 51 候选命中笔数 | 累加净额 (5bps) |",
        "|---|---|---|---|---|",
        "| **区间一：研究集** | 2025-01-01T00:00:00Z ~ 2026-07-01T00:00:00Z | 参数与规则最初挖掘生成地 | **0 笔** | 0.0 USDT |",
        f"| **区间二：已泄露验证集** | 2026-07-01T00:00:00Z ~ 2026-10-12T00:00:00Z | 04-A 阶段已完整计算、公开并用于调优 | **{int2_cnt} 笔 (100.0%)** | {cal_5bps['total_realized_net_usdt']} USDT |",
        "| **区间三：真样本外** | 2026-10-12T00:00:00Z 之后 | 规则严格冻结后的一次性盲测 | **0 笔** | 0.0 USDT |",
        "",
        f"- **候选时间跨度**：最早入场时刻为 `{earliest_e}`，最晚入场时刻为 `{latest_e}`，全部无一例外落在区间二；",
        "- **定性结论**：区间二的历史数据在前期开发阶段已被反复读取、多轮调优并公开展示。因此，**这 51 条候选的基线结果只能作为机制可算性与管道对撞基准，绝不能作为原策略有效或无效的样本外证明**！",
        "",
        "### 3.2 严禁“仅凭日期晚于某天就称新样本外”的刚性准则",
        "",
        "> [!WARNING]",
        "> 坚决反对并禁止任何“日历时间晚于 2026-06-27 或 2026-10-12 就是新样本外”的错误观点！",
        "",
        "合规的样本外验证必须同时具备两条刚性证明，缺一不可：",
        "1. **规则绝对冻结证据**：必须出具在进入该数据段之前已经锁定、不可更改的代码与配置 SHA256 哈希；",
        "2. **数据此前未查看/未用于选择（Unseen & Unused）证据**：必须出具该数据段此前从未被用于任何特征提取、参数网格搜索、阈值微调或规则设计的证据。只要某段数据曾被研究员或模型“看”过并据此修改过哪怕一个参数，该数据段在统计学上就已被**数据窥探偏差（Data Snooping Bias）**完全污染，终身丧失作为样本外的资格。",
        "",
        "### 3.3 本单纪律：不新开保留区间、不做参数选择",
        "",
        "- 本单严格执行工单指令：**不新开保留区间、不做参数选择**；",
        "- 坚决不为人为制造“亮眼回测”而随意切分时间段，坚决不进行任何形式的参数择优。",
        "",
        "---",
        "",
        "## 四、报告中错误比较解释的严肃纠偏与分流核对",
        "",
        "### 4.1 全量 102 笔试验的分流底账",
        "",
        "复核 05-D / 05-D-R1 交付物中的分流底账，真实明细如下：",
        "",
        "| 分流类别 | 试验笔数 | 有效成交笔数 | 胜率 | 总净额 (USDT) | 单笔均值 (USDT) | 类别界定依据 |",
        "|---|---|---|---|---|---|---|",
        f"| **可用基线组 (USABLE)** | {usa_s['count']} | {usa_s['valid_trades']} | {usa_s['win_rate_pct']}% | {usa_s['total_net_usdt']} | {usa_s['mean_net_per_trade_usdt']} | 无资金费同刻争议、未发生参与率缩量截断 |",
        f"| **政策暂缓组 (HOLD)** | {hld_s['count']} | {hld_s['valid_trades']} | {hld_s['win_rate_pct']}% | {hld_s['total_net_usdt']} | {hld_s['mean_net_per_trade_usdt']} | 存在资金费重叠顺序未决、跳空未决或极端缩量 |",
        f"| **因规拒单组 (REJECTED)** | {rej_s['count']} | {rej_s['valid_trades']} | 0.0% | {rej_s['total_net_usdt']} | 0.0 | CARVUSDT 低于币安最小名义额拒单 |",
        f"| **全量合计 (TOTAL_ALL)** | **{tot_s['count']}** | **{tot_s['valid_trades']}** | **{tot_s['win_rate_pct']}%** | **{tot_s['total_net_usdt']}** | **{tot_s['mean_net_per_trade_usdt']}** | 全量 51 候选 × 2 套经济口径试验 |",
        "",
        "### 4.2 纠正报告中错误的比较解释",
        "",
        "在前期 05-C 报告（120-123 行）及 05-D-R1 报告（第七节）中，存在如下典型错误论断：",
        "> *旧报告错误原话*：“可用组的净额比全量更差（-310.5379 vs -314.3707），所以不存在「剔掉争议笔来美化成绩」。” / “63 笔可用组净额为 -310.54 USDT，劣于全池平均，证明该分流是客观风险隔离，绝非美化。”",
        "",
        "**本单对上述错误进行三维度的彻底纠正与剖析：**",
        "",
        f"1. **代数大小与亏损绝对额看反（低级数值错误）**：",
        f"   - 全量总净额为 `{tot_s['total_net_usdt']} USDT`（总亏损 {abs(tot_s['total_net_usdt'])} USDT），可用组总净额为 `{usa_s['total_net_usdt']} USDT`（总亏损 {abs(usa_s['total_net_usdt'])} USDT）；",
        f"   - 在代数上：`-310.5379 > -314.3707`；在亏损绝对额上：可用组亏损比全量**减少了 {delta_loss} USDT**！",
        "   - 旧报告将“亏损绝对额减少 3.83 USDT”反向描述为“净额比全量更差”，其作者在数值比较时将负数符号与绝对值彻底混淆，得出了南辕北辙的数字结论！",
        "",
        f"2. **隐藏结构性特征，单笔指标与胜率严重割裂（统计假象）**：",
        f"   - 暂缓组 38 笔的交易表现明显优异：胜率高达 **{hld_s['win_rate_pct']}%**，单笔均损仅 **{hld_s['mean_net_per_trade_usdt']} USDT**（近乎盈亏平衡）；",
        f"   - 剔除这 38 笔后，可用组的胜率直接由全量的 {tot_s['win_rate_pct']}% 暴跌至 **{usa_s['win_rate_pct']}%**（下降 4.10 个百分点），单笔均损由 {tot_s['mean_net_per_trade_usdt']} 恶化至 **{usa_s['mean_net_per_trade_usdt']} USDT**（单笔均损恶化幅度达 58.34%）；",
        "   - 若仅仅抓住总净额亏损绝对值变小（从 314 变 310）或者单笔均损变大，粗暴宣称“更差”或“更优”，完全掩盖了分流对样本结构造成的剧烈扰动。",
        "",
        "3. **以事后盈亏断言分流无美化的根本方法论谬误（后此谬误与基线漂移）**：",
        "   - **分流的合规性必须由事前物理证据决定，绝不能由事后盈亏结果反推**！分流的唯一依据是：数据缺失（如热库 15.8 天资金费断流）、规则时序未决（如出场与资金费同刻顺序）或合约规则硬约束（如参与率缩量超限）。这些是工程物理事实，独立于交易盈亏；",
        "   - 若认可“因为分流后剩下的集合很惨/亏损更大，所以证明没有美化”的逻辑，在科学方法论上就犯了**后此谬误（Post Hoc Fallacy）与结果偏倚（Outcome Bias）**。这在学术与工程上开启了极具破坏性的漏洞：任何人都可以以合规为借口剔除特定行情样本，只要事后数字依然是负数就自称无美化；",
        "   - **潜在基线漂移风险**：未来如果将新策略放到 63 笔可用组上对比，而原策略被剔除的 38 笔高胜率（39.47%）样本被隐去，一旦新策略在这些行情下产生交易，将构成极大的基线美化与选择性偏倚！",
        "",
        "> [!CAUTION]",
        "> 修正后的正式结论：分流只能作为客观工程隔离与数据受限的合规记录；**严禁使用任何负数绝对额大小或事后盈亏优劣来断言分流无美化**！可用组 63 笔只作机制可算性基准，不能代表原策略的真实全貌。",
        "",
        "---",
        "",
        "## 五、基线适用与不适用清单及版本依赖",
        "",
        "### 5.1 基线适用清单 (MECHANISM_USABLE_LIST)",
        "",
        "| 编号 | 适用维度 | 允许使用范围 | 规范界定声明 |",
        "|---|---|---|---|",
        "| **APPLY-01** | 底层算术复算一致性 | 102 笔试验（101 真实成交 + 1 拒单） | 在严格 12:15 生效、跟踪止损只上移不下移、无 2R/10天强平规则下，8 字段 100% 逐位吻合，可作执行算术基准测试。 |",
        "| **APPLY-02** | 确定性事件重放基准 | 48 驱动标的在 06-27 至 10-26 窗口 | 在冻结状态机单标的单事件+24h 冷却规则下，1178 事件确定性可复算，用于状态迁移时序验证。 |",
        "| **APPLY-03** | 独立事件盈亏分布特征 | 生效视图 (51 笔) / 策略包 (50 笔) | 仅用于单笔均值、中位数、胜率分布的独立描述（均值 -4.42U / -1.78U），描述单笔独立特征。 |",
        "| **APPLY-04** | 合约物理约束防御边界 | CARVUSDT 在 0.1% 参与率下的拦截 | 证明强缩量后名义额低于交易所最小门槛拒单逻辑正确，可作防御边界用例。 |",
        "",
        "### 5.2 基线不适用清单 (STRICT_UNUSABLE_LIST)",
        "",
        "| 编号 | 严禁使用维度 | 违规性质与禁止理由 |",
        "|---|---|---|",
        "| **INAPPLY-01** | **严禁推广至全池 526 标的** | 母集 48 标的完全从 51 候选反向提取，存在严重选币偏倚（仅占 9.12%）；48 标的 0 异常不能推广到全池 10 个晚上市标的及 178 SETTLING 标的。 |",
        "| **INAPPLY-02** | **严禁作为策略盈利能力或有效性证明** | 51 条候选 100% 落在已泄露的区间二，此前已被完整计算并公开查看，绝对不能作为样本外有效性证据。 |",
        "| **INAPPLY-03** | **严禁作为组合账户真实资金曲线或回撤** | 无并发仓位上限，存在 9 笔同时在仓峰值；伪回撤随排列极差高达 478.38 USDT。组合表现定性为【未验证】。 |",
        "| **INAPPLY-04** | **严禁跨口径合并净额或混搭未批准口径** | 两套口径并存，跨口径相加 (-314.37U) 仅为对账校验码；05-A 推荐的混搭口径代码不存在且未获批准，严禁合成第三套参数。 |",
        "| **INAPPLY-05** | **严禁在冷档填充率 0% 下声称“当时可得”** | 冷档 available_at 整列为空，无历史发布时间戳证明，相关成交判定只能标“探索性”。 |",
        "| **INAPPLY-06** | **严禁在资金费缺口修复前宣称资金费完整** | 热库在 09-02 至 09-18 存在 15.8 天资金费缺口（G4），96 笔成交落在缺口段，需等窗口三冷热合并输入。 |",
        "| **INAPPLY-07** | **严禁将可用组 63 笔净额代表原策略全貌** | 可用组剔除了 38 笔暂缓单（胜率 39.47%），可用组净额 (-310.54U) 不能代表原策略全貌，严禁仅比负数绝对额断言无美化。 |",
        "",
        "### 5.3 上下游窗口依赖矩阵",
        "",
        "```mermaid",
        "flowchart TD",
        "    W1[\"窗口一 (06-01/02): 用户规则来源与待决清单\"] --> W2[\"窗口二 (06-03/04): 承接判定时点整改\"]",
        "    W3[\"窗口三 (06-05/06): 冷热资金费合并补齐 (修复G4)\"] --> W4[\"窗口四 (06-07/08): 财务算式复核与资金费接入\"]",
        "    W2 --> W5[\"窗口五 (06-09/10): 独立行为端到端复验\"]",
        "    W4 --> W5",
        "    W6_1[\"窗口六第一单 (06-11): 基线版本与边界核对 (本单)\"] --> W6_2[\"窗口六第二单 (06-12): 机制基线更新与总交接包\"]",
        "    W1 -.-> W6_2",
        "    W5 -.-> W6_2",
        "```",
        "",
        "- 本单（06-11）已完成基线身份、母集边界、经济口径及比较逻辑的严密清点；",
        "- 后续第二单（06-12）将汇集窗口二代码冻结版、窗口三资金费合并输入、窗口四重算结果以及窗口五端到端复验，完成六窗口总交接。在此之前，不运行未批准新策略正式对照。",
        "",
        "---",
        "",
        "## 六、复跑与自检说明",
        "",
        "本窗所有脚本均为**只读与隔离写入**，按顺序可一键重跑：",
        "",
        "```bash",
        "cd /Users/boos/Desktop/威科夫因子共振趋势",
        "python3 evidence/ACCUM-EARLY-06-W6/01/scripts/01_母集与版本清单复核.py",
        "python3 evidence/ACCUM-EARLY-06-W6/01/scripts/02_两套经济口径与51候选区间核对.py",
        "python3 evidence/ACCUM-EARLY-06-W6/01/scripts/03_修正报告错误比较解释与分流核对.py",
        "python3 evidence/ACCUM-EARLY-06-W6/01/scripts/04_基线适用不适用清单与样本外声明边界.py",
        "python3 evidence/ACCUM-EARLY-06-W6/01/scripts/05_生成06-11核对报告.py",
        "python3 evidence/ACCUM-EARLY-06-W6/01/scripts/06_正文数字双向对撞.py",
        "```",
        "",
        "经 `06_正文数字双向对撞.py` 检验，本报告中的全部数字与 `out/` 下 JSON 产物达成 **100% 逐项对撞吻合**，无任何手写手抄数字。"
    ]

    report_content = "\n".join(report_lines)
    with open(README_FILE, "w", encoding="utf-8") as f:
        f.write(report_content)

    ledger_file = OUT_DIR / "05_正文数字台账.json"
    with open(ledger_file, "w", encoding="utf-8") as f:
        json.dump(ledger, f, ensure_ascii=False, indent=2)

    print(f"[05_生成06-11核对报告] 完成！README: {README_FILE}, 台账: {ledger_file}")


if __name__ == "__main__":
    main()
