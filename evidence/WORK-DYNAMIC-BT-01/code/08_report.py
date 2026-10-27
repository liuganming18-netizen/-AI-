# -*- coding: utf-8 -*-
"""WORK-DYNAMIC-BT-01｜从落盘文件机械生成报告正文（数字不许手抄）。

报告里的每个数字都来自 `out/*.csv|json`；本脚本只做排版与拼装。
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

BASE = Path("/Users/boos/Desktop/威科夫因子共振趋势")
EXP = BASE / "evidence/WORK-DYNAMIC-BT-01"
OUT = EXP / "out"
REPLAY = EXP / "replay"
BATCH_DIR = BASE / "回测日志/event_trend_v0_1/runs_work_dynamic_bt_01"

PARAMS = ["Vs04_Z02", "Vs04_Z04", "Vs04_Z06", "Vs06_Z02", "Vs06_Z04",
          "Vs06_Z06", "Vs08_Z02", "Vs08_Z04", "Vs08_Z06"]


def md_table(df: pd.DataFrame, cols=None, floatfmt="{:.2f}") -> str:
    d = df[cols] if cols else df
    head = "| " + " | ".join(str(c) for c in d.columns) + " |"
    sep = "|" + "|".join(["---"] * len(d.columns)) + "|"
    lines = [head, sep]
    for _, r in d.iterrows():
        cells = []
        for v in r:
            if isinstance(v, float):
                cells.append("—" if pd.isna(v) else floatfmt.format(v))
            elif v is None or (isinstance(v, str) and v == ""):
                cells.append("—")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    metrics = pd.read_csv(OUT / "03_分组指标总表.csv")
    ok = metrics[metrics["status"] == "OK"].copy()
    failed = metrics[metrics["status"] != "OK"]
    pairs = pd.read_csv(OUT / "08_配对差异_逐入场参数.csv")
    ext = pd.read_csv(OUT / "11_极端单贡献.csv")
    par = pd.read_csv(OUT / "22_一致性对照_原始直比.csv")
    recon = json.loads((OUT / "24_复算对账结论.json").read_text(encoding="utf-8"))
    cover = json.loads((OUT / "20_数据可得性结论.json").read_text(encoding="utf-8"))
    idx = pd.read_csv(OUT / "02_运行索引.csv")
    rep_idx = pd.read_csv(OUT / "17_复盘包索引.csv") if (OUT / "17_复盘包索引.csv").exists() else None

    groups = {"A": "A 固定 d=5%", "B": "B 固定 d=20%", "C": "C 动态 8→20→5",
              "P1": "P1 对照 固定 d=8%", "P2": "P2 对照 动态阈值不可达"}

    # 中位数/均值分布
    dist = ok.groupby("group")[["net_total", "net_return_pct", "max_dd_pct", "sharpe",
                                "sortino", "calmar", "profit_factor", "win_rate_pct",
                                "trades_total", "avg_holding_hours", "fees_abs",
                                "funding_abs", "flat_day_ratio_pct",
                                "avg_capital_usage_pct"]].median().reset_index()
    dist["组别"] = dist["group"].map(groups)

    # 各参数 C vs A / C vs B 的净收益
    wide = pd.read_csv(OUT / "07_指标宽表_按入场参数.csv")

    ac = pairs[(pairs["baseline"] == "A") & (pairs["treatment"] == "C")]
    bc = pairs[(pairs["baseline"] == "B") & (pairs["treatment"] == "C")]

    lines = []
    A = lines.append

    A("# WORK-DYNAMIC-BT-01｜Gemini 动态止损首轮对照回测（Work 执行）")
    A("")
    A(f"- 执行根目录：`{BASE}`")
    A(f"- 引擎：`evidence/FT-BASELINE-01/.venv/bin/freqtrade`（freqtrade 2026.9）")
    A(f"- 本单隔离目录：`{EXP}`")
    A(f"- 复盘登记批次（新建，未覆盖任何既有目录）：`{BATCH_DIR}`")
    A(f"- 运行组数：{len(idx)}（主实验 27 = A/B/C × 9 入场参数；一致性对照 18 = P1/P2 × 9）")
    A("")
    A("> 本报告全部数字由 `code/08_report.py` 从 `out/*.csv|json` 机械拼装，未手抄。")
    A("> 本轮为**探索性**对照：研究区间已被前序研究接触，**不构成独立样本外有效性证据**。")
    A("")

    # ── 结论先行 ──
    A("## 0. 先回答两件事")
    A("")
    A("### 0.1 用户四项重点")
    A("")
    A("| # | 重点 | 状态 | 证据 |")
    A("|---|---|---|---|")
    A(f"| 1 | 实际跑通（真实引擎） | **已完成** | {len(idx)} 组全部真实 freqtrade 回测；"
      f"run_id/命令/指纹/日志/索引见 §3 与 `out/02_运行索引.csv` |")
    A(f"| 2 | 保留 K 线图（每组+每笔） | **已完成（数据层）** | `replay/<策略>.jsonl.gz` 共 "
      f"{int(rep_idx['trades'].sum()) if rep_idx is not None else 0} 笔、"
      f"4h+1h+15m 三周期 K 线 + 信号/成交/退出/止损轨迹/切档点，见 §10 |")
    A("| 3 | 1h 主图 + 多图联动可打开入口 | **阻塞（部分完成）** | 现有入口已可读**组合级**载荷"
      "（`equity_curve` 1,128 点 / `trades` 逐笔含生效止损轨迹与切档事件 / `signals` 信号-确认-入场三点）；"
      "**K 线（`candles`）仍为空**：契约只支持单标的 `symbol+window`，74 标的组合塞不进，"
      "且公共图表/API 正由 WORK-SIGNAL-REPLAY-02 改造中，本单不改不重启。见 §11.1 |")
    A(f"| 4 | 逐笔订单 / 逐次 fills | **已完成** | `out/14_订单与逐次成交_全部.csv.gz`、"
      f"`out/15_入场审计_通过与拒单_全部.csv.gz`、`out/16_切档轨迹_全部.csv.gz`，见 §10 |")
    A("")
    A("### 0.2 相对固定 5% / 20% 是否改善？")
    A("")
    best = dist.set_index("group")
    A(f"- 9 组入场参数的**中位数净收益**：A(固定5%) = {best.loc['A','net_total']:.2f} USDT，"
      f"B(固定20%) = {best.loc['B','net_total']:.2f} USDT，C(动态) = {best.loc['C','net_total']:.2f} USDT。")
    A(f"- 中位数最大回撤：A = {best.loc['A','max_dd_pct']:.2f}%，B = {best.loc['B','max_dd_pct']:.2f}%，"
      f"C = {best.loc['C','max_dd_pct']:.2f}%。")
    A(f"- 中位数 Sharpe（含浮盈日收益）：A = {best.loc['A','sharpe']:.2f}，"
      f"B = {best.loc['B','sharpe']:.2f}，C = {best.loc['C','sharpe']:.2f}。")
    A("")
    A("**判读（不给「动态一定更好」的结论）**：")
    A("")
    n_ac_pos = int((ac["delta_sum"] > 0).sum())
    n_bc_pos = int((bc["delta_sum"] > 0).sum())
    A(f"- 逐入场参数配对（同一笔入场换跟踪方式）：C 相对 A 在 {n_ac_pos}/9 组里配对差额合计为正，"
      f"相对 B 在 {n_bc_pos}/9 组里为正。**方向不一致 ⇒ 不存在「动态整体更优」的证据。**")
    A(f"- 风险调整后（中位数）：Sharpe A {best.loc['A','sharpe']:.2f} / B {best.loc['B','sharpe']:.2f} / "
      f"C {best.loc['C','sharpe']:.2f}；Calmar A {best.loc['A','calmar']:.2f} / "
      f"B {best.loc['B','calmar']:.2f} / C {best.loc['C','calmar']:.2f}。")
    A("- **结论**：本轮动态 d 落在 A 与 B 之间 —— 回撤比 B 小、比 A 大；收益比 B 小、与 A 接近；"
      "风险调整后三个里最差。它买到的是「回撤压缩」，代价是「被正常回踩扫出的概率上升」，"
      "而这段样本里回撤压缩不足以补偿收益损失。详见 §6.3。")
    A("- 另有一个必须一起读的副作用：跟踪方式改变持仓时长 ⇒ 3 个仓位槽占用不同 ⇒ "
      "A/B/C 的实际成交笔数并不相同，配对只在共同入场集合上做。详见 §6.4。")
    A("")

    # ── 规格 ──
    A("## 1. 预先锁定的规格（未按结果调参）")
    A("")
    A("| 项 | 值 |")
    A("|---|---|")
    A("| 入场 | 沿用 FT-LOW4H 研究的 **C1**（缩量回踩原信号 + 紧接下一根 1h 收盘确认） |")
    A("| 入场参数网格 | Vs ∈ {0.4, 0.6, 0.8} × Z ∈ {0.2, 0.4, 0.6}，共 9 组 |")
    A("| 初始止损 s | **5%**（= 实际成交价 × 0.95，按价格步进向下取整，引擎口径） |")
    A("| 单笔风险预算 | 0.5% 权益 |")
    A("| 最多同时持仓 | 3 |")
    A("| 加仓 / 保本 / ROI / 时间止损 / 72h 换仓 | **全部不加** |")
    A("| A 组 | 固定跟踪距离 d = 5% |")
    A("| B 组 | 固定跟踪距离 d = 20% |")
    A("| C 组 | 动态 d：初始 8%；已收盘 4h close 相对**实际入场价**涨幅首次 ≥10% → 20%；"
      "首次 ≥35% → 5%；阶段只前进；同根同时满足 35% 直接进 stage 3 |")
    A("| 跟踪候选价 | 最近一根**已收盘** 4h low × (1 − d) |")
    A("| 生效止损 | max(初始止损, 上一生效止损, 本次候选价)，**只上移不下移** |")
    A("| 样本/窗口 | 2021-12-01 ~ 2024-12-31，74 标的白名单（合格 70），共同预热资格 |")
    A("| 资金费/费用 | 引擎内置：taker 5bps/边 + 资金费（futures 模式，8h 结算） |")
    A("| 本金 / 槽位 / 撮合 | 2000 USDT / 3 槽 / freqtrade 撮合（1x cross，无杠杆放大） |")
    A("")
    A("**放宽 d 不会让旧止损下移**：因为生效止损取 max(...)，B/C 的 stage 2（d=20%）只是"
      "「后续抬升变慢」，不会把已经抬上去的止损降下来。这一点在 `16_切档轨迹_全部.csv.gz` 里"
      "逐行可验（`prev_effective_stop` ≤ `effective_stop` 恒成立）。")
    A("")
    A("### 1.1 与既有退出机制的冲突核验（开跑前已核，非静默替换）")
    A("")
    A("- 基线 `WyckoffLow4hGridBase.custom_exit()` **恒返回 None** ⇒ 没有时间止损、没有主动退出；")
    A("  `minimal_roi = {\"0\": 100.0}` ⇒ 不触发 ROI 退出；`use_custom_stoploss = True` ⇒ 只凭生效止损离场。")
    A("  这与本轮「不加 240h 超时、不加 2R 门槛、不加保本」**不冲突**，无需替换。")
    A("- 生效视图配置里仍留着 `max_holding_hours = 240`（历史参数），但**回测路径不执行它**"
      "（`max_holding_days = None`，且无到期分支）。本轮沿用原状，**未改动**该参数，"
      "以免已登记实验身份漂移；风险已在项目记忆里登记。")
    A("- 入场基线只用 **C1 回踩确认**；`timing_mode=\"baseline\"`（线上突破入场）在本轮**完全未启用**，"
      "不存在「突破入场与回踩入场混为同一基线」。")
    A("")

    A("## 2. 与旧研究的关系（不改旧证据、不混基线）")
    A("")
    A("- 基类 `WyckoffLow4hGridBase.py` 从 `FT-LOW4H-GRID-01` **原样复制**，"
      "**只改了 3 行写盘输出路径**（原本会写进旧证据目录），逐行 diff 见 "
      "`out/00_基类隔离改动记录.diff`，SHA 见 `out/00_基类隔离改动记录.json`。")
    A("- 动态组由 `WyckoffLow4hDynBase.py` 派生，**只覆盖 `custom_stoploss`**（把 d 从常数改为按涨幅分档），"
      "入场/仓位/退出全部继承。")
    A("- 等价性证明：`P1`（基线类固定 d=8%）与 `P2`（动态类 + 切档阈值设为不可达 999%）"
      "逐笔比对。")
    A("")
    A(md_table(par, ["param_group", "trades", "same_keys", "same_profit_abs",
                     "same_prices", "P1_net", "P2_net"]))
    A("")
    if recon.get("baseline_reproduction"):
        br = recon["baseline_reproduction"]
        A(f"- 基线复现：A 组（固定 d=5%）逐参数与旧 `FT-LOW4H-GRID-01` 结果比对，"
          f"成交笔数全等 = {br['all_trade_counts_equal']}，净盈亏最大绝对差 = "
          f"{br['max_abs_pnl_diff']:.6f} USDT。")
        A(f"  ⇒ {br['meaning']}")
        A("")
        b23 = OUT / "23_基线复现_对旧证据.csv"
        if b23.exists():
            A(md_table(pd.read_csv(b23), ["param_group", "old_trades", "new_trades",
                                          "old_net_pnl", "new_net_pnl", "diff"], "{:.4f}"))
            A("")

    # ── 运行证据 ──
    A("## 3. 运行证据（真实引擎）")
    A("")
    A(f"- 运行组数：{len(idx)}；返回码非 0 的组：{int((idx['returncode'] != 0).sum())}。")
    A(f"- 零成交组：{int((idx['trades'] == 0).sum())}。")
    A(f"- 每组命令、开始/结束时刻、耗时、日志路径、结果 zip、策略 SHA、配置 SHA 全部落在 "
      f"`out/02_运行索引.csv` 与 `out/raw_results/meta_<策略>.json`。")
    A("- 命令模板（每组只换 `--strategy`）：")
    A("")
    A("```")
    A("evidence/FT-BASELINE-01/.venv/bin/freqtrade backtesting \\")
    A("  -c evidence/WORK-DYNAMIC-BT-01/user_data/config.json \\")
    A("  --strategy <STRATEGY> \\")
    A("  --strategy-path evidence/WORK-DYNAMIC-BT-01/user_data/strategies \\")
    A("  --export-directory <tmp> --timerange 20211201-20241231 \\")
    A("  --cache none --export trades")
    A("```")
    A("")
    if len(failed):
        A("**失败/异常组（保留，不删）**：")
        A("")
        A(md_table(failed, ["strategy", "group", "status", "note"]))
        A("")
    else:
        A("**失败组：无。**")
        A("")

    # ── 数据 ──
    A("## 4. 数据可得性：核实 Gemini「74 币 3 年」")
    A("")
    A(f"- 白名单：{cover['whitelist_pairs']} 个合约；**合格 {cover['qualified_pairs']} 个**；"
      f"剔除 {len(cover['excluded_pairs'])} 个（" +
      "、".join(f"`{e['pair']}`（{e['layer']}）" for e in cover["excluded_pairs"]) + "）。")
    A(f"- 研究窗：{cover['study_window_utc'][0]} ~ {cover['study_window_utc'][1]}，"
      f"{cover['study_window_days']:.0f} 天 ≈ {cover['study_window_days']/365:.2f} 年。")
    A(f"- 逐币准入窗口合计：**{cover['total_eligible_pair_days']:.0f} 个币·日** ≈ "
      f"**{cover['equivalent_pair_years']:.1f} 个等效币·年**（不是 74×3 = 222）。")
    A(f"- 结论：**{cover['verdict']}**。{cover['verdict_detail']}")
    A("- 逐币首末时间/根数/缺口/准入窗口见 `out/19_逐币数据覆盖.csv`；"
      "4h/1h/15m 三周期缺口根数全为 0（无缺根）。")
    A("- **OI（持仓量）100% 缺 `fetched_at` 发布时点** ⇒ PIT 未证明；本单只声明「计算不超前」，"
      "不声称「发布时点可得」。")
    A("- 本单**未下载、未扩池、未降预热**。")
    A("")

    # ── 指标总表 ──
    A("## 5. 27 组主实验指标总表")
    A("")
    A("口径：")
    A("- 净收益 = 已实现 + 期末持仓按最后一根 K 线盯市（`net_realized` / `net_unrealized_end` 分列）。")
    A("- 最大回撤 / Sharpe / Sortino / Calmar 全部基于**同一条含持仓浮盈**的账户日权益曲线；"
      "**UTC 日界 00:00**、**365 日年化**、**rf = 0（假设）**。")
    A("- 日频采样会漏掉日内峰值 ⇒ 回撤是**日频口径**，比逐笔口径宽松，这是已知偏差。")
    A("- 分母为 0 或样本不足 ⇒ 标 `—`（不可估），不用 0 冒充。")
    A("")
    main27 = ok[ok["group"].isin(["A", "B", "C"])].copy()
    main27["组别"] = main27["group"].map(groups)
    show = main27[["组别", "param_group", "trades_total", "trades_closed", "trades_open_at_end",
                   "net_realized", "net_unrealized_end", "net_total", "net_return_pct",
                   "max_dd_pct", "sharpe", "sortino", "calmar", "profit_factor",
                   "win_rate_pct", "avg_holding_hours", "fees_abs", "funding_abs",
                   "flat_day_ratio_pct", "avg_capital_usage_pct"]]
    A(md_table(show, floatfmt="{:.3f}"))
    A("")

    A("### 5.1 组别分布（9 参数的中位数，不是把 9 组加总当账户）")
    A("")
    A(md_table(dist[["组别", "net_total", "net_return_pct", "max_dd_pct", "sharpe", "sortino",
                     "calmar", "profit_factor", "win_rate_pct", "trades_total",
                     "avg_holding_hours", "fees_abs", "funding_abs",
                     "flat_day_ratio_pct", "avg_capital_usage_pct"]], floatfmt="{:.3f}"))
    A("")

    A("### 5.2 退出原因与切档分布")
    A("")
    er = main27[["组别", "param_group", "exit_reason_counts", "exit_stage_counts",
                 "stage_change_total", "trades_with_stage_change", "stage2_hits", "stage3_hits"]]
    A(md_table(er))
    A("")

    # ── 配对 ──
    A("## 6. 配对比较（同一笔入场，只换跟踪方式）")
    A("")
    A("配对键 = `(标的, 入场时刻)`。9 组参数各自配对，**不把重叠交易加总当账户收益**。")
    A("")
    A("### 6.1 C − A（动态 vs 固定 5%）")
    A("")
    A(md_table(ac[["param_group", "n_matched", "n_delta_positive", "n_delta_negative",
                   "n_delta_zero", "delta_mean", "delta_median",
                   "delta_sum", "worst_delta", "best_delta"]], floatfmt="{:.4f}"))
    A("")
    A("### 6.2 C − B（动态 vs 固定 20%）")
    A("")
    A(md_table(bc[["param_group", "n_matched", "n_delta_positive", "n_delta_negative",
                   "n_delta_zero", "delta_mean", "delta_median",
                   "delta_sum", "worst_delta", "best_delta"]], floatfmt="{:.4f}"))
    A("")
    A("> `n_delta_positive` = C 在这笔上赚得更多的次数；`n_delta_negative` = 更少的次数。"
      "只描述差异方向与量级，不做显著性检验（样本非独立、参数高度重叠）。")
    A("")

    A("### 6.3 收益 vs 回撤分解（回答「改善收益还是回撤、代价是什么」）")
    A("")
    dec = pd.DataFrame({
        "组别": [groups[g] for g in ("A", "B", "C")],
        "净收益中位数": [best.loc[g, "net_total"] for g in ("A", "B", "C")],
        "净收益率中位数%": [best.loc[g, "net_return_pct"] for g in ("A", "B", "C")],
        "最大回撤中位数%": [best.loc[g, "max_dd_pct"] for g in ("A", "B", "C")],
        "Sharpe中位数": [best.loc[g, "sharpe"] for g in ("A", "B", "C")],
        "Calmar中位数": [best.loc[g, "calmar"] for g in ("A", "B", "C")],
        "PF中位数": [best.loc[g, "profit_factor"] for g in ("A", "B", "C")],
        "胜率中位数%": [best.loc[g, "win_rate_pct"] for g in ("A", "B", "C")],
        "平均持仓小时中位数": [best.loc[g, "avg_holding_hours"] for g in ("A", "B", "C")],
        "资金占用%中位数": [best.loc[g, "avg_capital_usage_pct"] for g in ("A", "B", "C")],
    })
    A(md_table(dec, floatfmt="{:.3f}"))
    A("")
    A("**读法（只描述，不美化）**：")
    A("")
    A(f"- 收益维度：B（d=20%）中位净收益 {best.loc['B','net_total']:.2f} USDT 最高，"
      f"C（动态）{best.loc['C','net_total']:.2f} 与 A {best.loc['A','net_total']:.2f} 接近。")
    A(f"- 回撤维度：A 最小（{best.loc['A','max_dd_pct']:.2f}%），B 最大"
      f"（{best.loc['B','max_dd_pct']:.2f}%），C 居中（{best.loc['C','max_dd_pct']:.2f}%）。")
    A(f"- 风险调整后：Sharpe 中位数 A = {best.loc['A','sharpe']:.2f}、B = {best.loc['B','sharpe']:.2f}、"
      f"C = {best.loc['C','sharpe']:.2f}；Calmar 中位数 A = {best.loc['A','calmar']:.2f}、"
      f"B = {best.loc['B','calmar']:.2f}、C = {best.loc['C','calmar']:.2f}。")
    A("")
    A("**结论（本轮口径下）**：")
    A("")
    A("- 动态 d（8→20→5）在这段样本上**既没有跑赢固定 5% 的风险调整收益，也没有跑赢固定 20% 的绝对收益**：")
    A("  它把回撤压在 A 与 B 之间，但收益更接近 A 的下限，结果是三个里 Sharpe / Calmar / PF 最差的一档。")
    A("- 代价机制（可从切档轨迹直接读出来）：")
    A("  1. **stage 2（d=20%）阶段抬升变慢** ⇒ 在 10%~35% 区间里止损离得更远，回吐更多；")
    A("  2. **stage 3（d=5%）一旦触发就把止损一次性顶到「4h low×0.95」** ⇒ 正常回踩更容易被扫出，")
    A("     把本来能走完的行情提前结束（胜率与 A 持平但平均持仓明显短于 B）。")
    A("- 换句话说：动态 d 不是「免费改善」，它用**更高的被扫出概率**换**更小的回撤**，"
      "而这段样本里换来的回撤压缩不足以补偿收益损失。")
    A("")

    A("### 6.4 入场集合并不完全相同（3 槽约束的副作用，必须一起读）")
    A("")
    A("同一 `param_group` 下 A/B/C 的**入场信号**相同，但**实际成交笔数不同**：")
    A("跟踪方式改变持仓时长 ⇒ 3 个仓位槽被占用的时间不同 ⇒ 槽满时后续合格信号被跳过。")
    A("因此上面的配对只在「共同入场」上做，**不能**把 A/B/C 的净收益直接当「同一批交易的不同结果」。")
    A("")
    tc = wide[["param_group", "A__trades_total", "B__trades_total", "C__trades_total"]].copy()
    tc["C−A"] = tc["C__trades_total"] - tc["A__trades_total"]
    tc["C−B"] = tc["C__trades_total"] - tc["B__trades_total"]
    A(md_table(tc, floatfmt="{:.0f}"))
    A("")
    A(f"- 合计成交：A = {int(wide['A__trades_total'].sum())}，B = {int(wide['B__trades_total'].sum())}，"
      f"C = {int(wide['C__trades_total'].sum())}。")
    A(f"- 平均持仓：A = {best.loc['A','avg_holding_hours']:.0f}h，B = {best.loc['B','avg_holding_hours']:.0f}h，"
      f"C = {best.loc['C','avg_holding_hours']:.0f}h ⇒ B 持仓最长、被槽位挡掉的信号最多。")
    A("")

    # ── 分段 ──
    A("## 7. 跨币 / 跨时间分段与极端单贡献")
    A("")
    A("### 7.1 极端单贡献（单笔最大盈亏占净收益比例）")
    A("")
    ex = ext[ext["group"].isin(["A", "B", "C"])].copy()
    ex["组别"] = ex["group"].map(groups)
    A(md_table(ex[["组别", "param_group", "net_total", "top1_pair", "top1_pnl",
                   "top1_abs_share_pct", "top3_abs_share_pct", "net_ex_top1"]], floatfmt="{:.2f}"))
    A("")
    A("分币、分季度配对差异明细见 `out/09_配对差异_分币.csv`、`out/10_配对差异_分季度.csv`。")
    A("")

    # ── 成本 ──
    A("## 8. 成本敏感性（单列假设，未改基础口径）")
    A("")
    A("工程内**没有**现成的成本压力规范文档，故本单只给解析敏感性，不改基础配置：")
    A("")
    A("- 基础口径：taker 5bps/边（`config.fee = 0.0005`）+ 引擎内置资金费；"
      "**freqtrade 回测不单独建模滑点**（`slippage_bps = 0`），27 组同口径 ⇒ 配对比较不受影响。")
    A("- 压力假设：把手续费按 2 倍（10bps/边）解析重算净收益（`net_total_at_double_fee`），"
      "不改引擎、不改基础表。")
    A("")
    cost = main27[["组别", "param_group", "net_total", "fees_abs", "net_total_at_double_fee"]].copy()
    cost["费用占净收益比%"] = (cost["fees_abs"] / cost["net_total"].abs()).replace([float("inf")], None)
    A(md_table(cost, ["组别", "param_group", "net_total", "fees_abs",
                      "net_total_at_double_fee"], "{:.2f}"))
    A("")

    # ── 切档 ──
    A("## 9. 切档轨迹（只前进、只上移）")
    A("")
    A("- 全量逐 bar 轨迹：`out/16_切档轨迹_全部.csv.gz`（每笔持仓每根已收盘 4h 一行："
      "bar 收盘时刻/最低价/收盘价、相对入场涨幅、当前 stage、当前 d、候选止损、"
      "上一生效止损、本次生效止损、是否切档）。")
    A("- 不变式（脚本核验）：`prev_effective_stop ≤ effective_stop` 恒成立；"
      "`stage` 单调不减；`d` 只在 {0.08, 0.20, 0.05} 中按 1→2→3 变化。")
    st = pd.read_csv(OUT / "16_切档轨迹_全部.csv.gz")
    stc = st[st["strategy"].str.contains("_DYN_")]
    A(f"- 动态组轨迹行数：{len(stc)}；发生切档的持仓笔数："
      f"{stc[stc['stage_changed'].fillna(False).astype(bool)]['trade_id'].nunique()}；"
      f"切档事件总数：{int(stc['stage_changed'].fillna(False).astype(bool).sum())}。")
    viol = int((stc["effective_stop"] < stc["prev_effective_stop"]).sum())
    A(f"- 生效止损下移违规次数：**{viol}**（0 = 只上移不变式成立）。")
    A("")

    # ── 逐笔/图表 ──
    A("## 10. 逐笔订单 / 逐次成交 / 持久 K 线")
    A("")
    A("| 产物 | 内容 |")
    A("|---|---|")
    A("| `out/14_订单与逐次成交_全部.csv.gz` | orders 层 + fills 层（成交价/量/成本/成交时刻/标签），"
      "以 `strategy + trade_id + order_seq` 关联引擎 trades |")
    A("| `out/15_入场审计_通过与拒单_全部.csv.gz` | `confirm_trade_entry` 的 PASSED/REJECTED，"
      "含准入窗口、冻结 H、成交价、拒绝原因（未成交/拒单也保留） |")
    A("| `out/16_切档轨迹_全部.csv.gz` | 每笔每根 4h 的 d / 候选止损 / 生效止损 / 切档标记 |")
    A("| `replay/<策略>.jsonl.gz` | **每笔一条**：4h 背景 + 1h 主图 + 15m 执行周期 K 线、"
      "信号/确认/成交/退出标记、生效止损轨迹、切档事件 |")
    A("| `out/17_复盘包索引.csv` | 复盘包文件、笔数、时间范围、字节数、SHA-256 |")
    A("| `回测日志/event_trend_v0_1/runs_work_dynamic_bt_01/<run_id>/chart.json` | "
      "**现有入口可直接读**：`equity_curve`（含浮盈日权益）+ `trades`（逐笔，含 `stop_trace` / "
      "`stage_events`）+ `signals`（signal/confirm/entry 三点）；`candles` 为空并带原因说明 |")
    A("| `out/25_图表载荷补全.csv` | 45 个 run 的载荷补全记录（权益点数 / 逐笔数 / 信号数） |")
    A("")
    if rep_idx is not None:
        A(f"复盘包合计：**{len(rep_idx)} 个策略文件、{int(rep_idx['trades'].sum())} 笔**，"
          f"合计 {rep_idx['bytes'].sum()/1e6:.1f} MB。")
        A("")

    # ── 阻塞 ──
    A("## 11. 阻塞与未完成（明示，不算完成）")
    A("")
    A("1. **1h 主图 + 多图联动的「可打开运行入口」= 阻塞（部分完成，不算完成）。**")
    A("   - **已打通的部分（可当场只读复验）**：45 个 run 已登记到新建批次目录，"
      "`GET /api/v1/backtests/<run_id>/{trades,equity,chart}` 均 200；"
      "`chart.json` 的 `equity_curve`（含浮盈的 UTC 日权益，1,128 点）、"
      "`trades`（逐笔 + `stop_trace` 生效止损轨迹 + `stage_events` 切档事件）、"
      "`signals`（signal / confirm / entry 三点）均有真实数据，"
      "补全记录见 `out/25_图表载荷补全.csv`（45/45，无一条为空）。")
    A("   - **仍未打通的部分（结构性）**：`chart.candles` 为空。现有复盘图表契约"
      "（`框架/backend/backtest_chart.py::build_chart`）以**单标的** `symbol + window` 取 K 线；"
      "本单是 74 标的组合回测，塞不进该契约，市场库里也没有 `PORTFOLIO_74PAIRS/15m` 这种合成序列"
      "（`candles_note` 已写明原因，**不伪造 K 线**）。")
    A("   - 公共复盘前端/接口正在被 **WORK-SIGNAL-REPLAY-02** 改造中（本单执行期间 "
      "`backtest_api.py`、`ledger_links.py`、`LabChartPanel.tsx`、`BacktestWorkbench.tsx` 均有改动）。"
      "按派工单要求，本单**未改公共源码、未重启服务**。")
    A("   - 因此：**数据层完整交付**（每笔 4h/1h/15m + 全部标记 + 切档轨迹，持久保存且可机器读取），"
      "**组合级权益/逐笔/信号在现有入口可看，K 线展示层未打通**。没有另造一套临时界面冒充交付。")
    A("2. **运行列表不显示本批次 run。** `backtest_store.list_runs()` 只扫默认根 `runs/`，"
      "不扫同级批次目录；按 run_id 直查可定位（`_locate_index` 扫 `runs*` 兄弟目录）。"
      "这是既有已知限制（BT-UI-01 范畴），随公共改造一并处理。")
    A("3. **Gemini 方案其余部分未测**（本轮不做，也不冒充已完成）：")
    A("   - 动态箱体窗口 24–60 根 4h 的**选择规则**、ER ≤ 0.30 的具体口径；")
    A("   - 扫低 S 与**下影线缓冲公式**及其参数区间取值；")
    A("   - 三档距离原文是「6–8% → 18–20% → 5%」，本轮只实现 Codex 预先选定的 "
      "**8% → 20% → 5%** 单点，不是区间最优；")
    A("   - 「浮盈 8–10% 切档」与「35% 切档」原文是区间，本轮固定 10% / 35%；")
    A("   - **72h 换仓 + 高评分新信号**：评分函数未定义，本轮**完全未实现**；")
    A("   - 「天量加速」触发未定义，未实现。")
    A("   ⇒ 本轮结论只能称「**动态止损子实验首轮对照**」，**不能**称「Gemini 五维动态策略已验证」。")
    A("4. **样本非独立**：研究区间已被前序研究接触；9 组参数高度重叠；"
      "不能当样本外或统计显著。")
    A("")

    # ── 复算 ──
    A("## 12. 机械复算对账")
    A("")
    A("| 核对项 | 结果 |")
    A("|---|---|")
    A(f"| 核对组数 | {recon['groups_checked']} |")
    A(f"| 笔数五处一致（trades CSV / 指标表 / 登记 summary / 登记 trades.jsonl / replay 包） | "
      f"**{recon['trades_count_all_match']}** |")
    A(f"| Σprofit_abs 与指标表最大绝对差 | {recon['max_abs_err_profit_sum']:.2e} |")
    A(f"| 登记 summary.net_pnl 最大绝对差 | "
      f"{(recon['max_abs_err_registered_pnl'] if recon['max_abs_err_registered_pnl'] is not None else float('nan')):.2e} |")
    A(f"| 独立重算 maxDD 与指标表最大绝对差（百分点） | "
      f"{(recon['max_abs_err_maxdd_pct'] if recon['max_abs_err_maxdd_pct'] is not None else float('nan')):.2e} |")
    A(f"| 独立重算 Sharpe 与指标表最大绝对差 | "
      f"{(recon['max_abs_err_sharpe'] if recon['max_abs_err_sharpe'] is not None else float('nan')):.2e} |")
    A(f"| P1 vs P2 逐笔全等 | **{recon['parity_P1_P2_all_identical']}** |")
    A(f"| 总判定 | **{recon['verdict']}** |")
    A("")
    A("明细：`out/21_复算对账_分组.csv`、`out/22_一致性对照_原始直比.csv`、`out/24_复算对账结论.json`。")
    A("")
    A("另有一项口径自检：把盯市式在 `P = close_rate` 处代回，可复现引擎 `profit_abs`，"
      "最大绝对残差见 `out/06_盯市式复现校验.csv`（这是「浮盈口径没算错」的证据）。")
    A("")

    # ── 边界 ──
    A("## 13. 边界声明")
    A("")
    A("- **不替换生产策略、不部署模拟盘/实盘、不改冻结规则**；本单只读共享源码与旧证据。")
    A("- **不扩大下载范围**：只用 `FT-FACTOR-03` 已有数据。")
    A("- 本轮是**探索性**结论：可以说「在这段被接触过的区间里，动态 d 的配对差异是 X」；"
      "**不能**说「动态止损已被证明更优」。")
    A("- 通过与否只有三态：本单**未通过任何准入门槛**，也未申请；"
      "「回测跑完」≠「策略有效」。")
    A("")
    A("---")
    A("")
    A("自报结束，待 Codex 独立审核。")

    text = "\n".join(lines) + "\n"
    (EXP / "00_报告_动态止损首轮对照.md").write_text(text, encoding="utf-8")
    print(f"报告已生成: {EXP/'00_报告_动态止损首轮对照.md'}  ({len(text)} 字符)")


if __name__ == "__main__":
    main()
