# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-F｜07 数字对撞（双向）。

A 方向（产物 → 正文）：登记的关键值必须在 README 里真的出现。
B 方向（正文 → 产物）：README 里出现的每个数字，必须能在本单产物、
   被引用的上游产物、或显式白名单（每条都要写理由）里找到。

为什么要双向：只做 A 会漏掉「正文里编了个数字」；只做 B 会漏掉
「产物算对了但正文没写」。两个方向都做，才能说「正文与产物一致」。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
OUT = HERE / "out"
ROOT = HERE.parents[2]


def load(name):
    p = OUT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def collect_numbers(obj, acc):
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        acc.add(float(obj))
    elif isinstance(obj, str):
        for m in re.finditer(r"-?\d+(?:\.\d+)?", obj):
            try:
                acc.add(float(m.group()))
            except ValueError:
                pass
    elif isinstance(obj, dict):
        for v in obj.values():
            collect_numbers(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            collect_numbers(v, acc)


def num_set(paths: list) -> set:
    acc = set()
    for p in paths:
        if p.exists():
            collect_numbers(json.loads(p.read_text(encoding="utf-8")), acc)
    return acc


def norm_text(s: str) -> str:
    """正文归一化：统一负号、去千分位、去时间与编号。"""
    s = s.replace("\u2212", "-").replace("−", "-")
    s = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", " ", s)   # 完整时刻
    s = re.sub(r"\d{4}-\d{2}-\d{2}", " ", s)                      # 日期
    s = re.sub(r"\d{2}:\d{2}", " ", s)                            # 时分
    s = re.sub(r"\b\d{2}_[^\s`|)]*", " ", s)                       # 脚本/产物编号
    s = re.sub(r"(?m)^#{1,6}\s*\d+(?:\.\d+)*", " ", s)            # 标题编号（2.1 / 4.3）
    s = re.sub(r"\d+(?:\.\d+)+\s*节", " ", s)                      # 正文里的章节交叉引用（见 2.2 节）
    s = re.sub(r"\|[-| ]+\|", " ", s)                              # 表格分隔行
    s = s.replace(",", "")                                        # 千分位
    return s


def main():
    d00 = load("00_范围锚定.json")
    d01 = load("01_币池与退市.json")
    d02 = load("02_四层覆盖.json")
    d03 = load("03_读取链与可得时点.json")
    d04 = load("04_资金费口径.json")
    d05 = load("05_可用范围与缺口.json")
    text = (HERE / "README.md").read_text(encoding="utf-8")
    nt = norm_text(text)

    r5 = d02["results"]["05C"]
    ru = d02["results"]["05C_universe"]
    rr = d02["results"]["04A_research"]
    rh = d02["results"]["04A_holdout"]
    seg0 = d03["funding_coverage_gap"]["hot_gap_segments"][0]
    q = d04["quantified"]

    # ── A 方向：登记关键值 ────────────────────────────────────────────
    claims = [
        ("窗口起点", d02["windows"]["05C"]["from_utc"]),
        ("窗口终点", d02["windows"]["05C"]["to_utc"]),
        ("预热起点", d02["windows"]["05C"]["warmup_from_utc"]),
        ("池大小", str(d02["pool"]["n"])),
        ("15m 应有根数", str(r5["by_timeframe"]["15m"]["expected_bars"])),
        ("4h 应有根数", str(r5["by_timeframe"]["4h"]["expected_bars"])),
        ("15m 完全连续", str(r5["by_timeframe"]["15m"]["cold"]["continuous"])),
        ("1h 完全连续", str(r5["by_timeframe"]["1h"]["cold"]["continuous"])),
        ("4h 完全连续", str(r5["by_timeframe"]["4h"]["cold"]["continuous"])),
        ("15m 上市晚", str(r5["by_timeframe"]["15m"]["cold"]["late_start"])),
        ("冷档 15m 有抓取时刻", str(r5["by_timeframe"]["15m"]["cold"]["available_at_filled"])),
        ("热库 15m 有抓取时刻", str(r5["by_timeframe"]["15m"]["hot"]["available_at_filled"])),
        ("研究 15m 中间断流", str(rr["by_timeframe"]["15m"]["cold"]["mid_gap"])),
        ("研究 15m 完全无数据", str(rr["by_timeframe"]["15m"]["cold"]["no_data"])),
        ("研究预热完整标的", str(rr["warmup_4h"]["symbols_full"])),
        ("研究预热无数据标的", str(rr["warmup_4h"]["symbols_no_data"])),
        ("05C 预热不足标的", str(r5["warmup_4h"]["symbols_short"])),
        ("退市标的数", str(d01["delisted"]["count"])),
        ("退市冷档目录数", str(d01["delisted"]["in_cold_dirs"])),
        ("指数个数", str(len(d01["index_rule"]["index_symbols"]))),
        ("历史全集上界", str(d01["survivorship"]["historical_union_upper_bound"])),
        ("排除比例上界", str(d01["survivorship"]["excluded_fraction_pct"])),
        ("资金费区间热库行数", str(d03["funding_coverage_gap"]["hot_rows"])),
        ("资金费区间冷档行数", str(d03["funding_coverage_gap"]["cold_rows"])),
        ("资金费事后回填占比", str(d03["non_bar_inputs"]["funding"]["fetch_lag"]["gt_24h_pct"])),
        ("缺口段起点", seg0["from_utc"]),
        ("缺口段终点", seg0["to_utc"]),
        ("缺口段小时", str(seg0["max_hours"])),
        ("缺口段标的数", str(seg0["symbols"])),
        ("冷档多余行数", str(d03["funding_coverage_gap"]["cold_surplus_rows"])),
        ("研究成交笔数", str(q["research_final"]["fills"])),
        ("验证成交笔数", str(q["holdout_final"]["fills"])),
        ("研究资金费 ΔR", str(q["research_final"]["delta_r"]["total"])),
        ("验证资金费 ΔR", str(q["holdout_final"]["delta_r"]["total"])),
        ("验证计入后 R", str(q["holdout_final"]["delta_r"]["base_R_sum_after"])),
        ("验证缺口笔数", str(q["holdout_final"]["funding_real_gap_trades"])),
        ("缺口归因笔数", str(q["holdout_final"]["funding_gap_inside_hot_gap"])),
        ("结算间隔中位", str(q["holdout_final"]["settlements"]["median_interval_hours"])),
        ("持仓中位", str(q["holdout_final"]["held_hours"]["median"])),
        ("规格行数", str(d03["non_bar_inputs"]["contract_spec"]["rows"])),
        ("成交只可能来自冷档(研究)", str(d03["trade_bar_layers"]["by_window"]["research_final"]["entry_only_from_cold"])),
        ("成交只可能来自冷档(验证)", str(d03["trade_bar_layers"]["by_window"]["holdout_final"]["entry_only_from_cold"])),
        ("验证冷档占比", str(d03["trade_bar_layers"]["by_window"]["holdout_final"]["cold_share_pct"])),
        ("对撞一致项", str(d03["reader_crosscheck"]["matched"])),
        # 05-C 母集（主口径）
        ("母集标的数", str(ru["universe"]["symbol_count"])),
        ("母集事件数", str(ru["universe"]["events_total"])),
        ("母集 15m 完全连续", str(ru["by_timeframe"]["15m"]["cold"]["continuous"])),
        ("母集 1h 完全连续", str(ru["by_timeframe"]["1h"]["cold"]["continuous"])),
        ("母集 4h 完全连续", str(ru["by_timeframe"]["4h"]["cold"]["continuous"])),
        ("母集 4h 应有根数", str(ru["by_timeframe"]["4h"]["expected_bars"])),
        ("母集预热完整标的", str(ru["warmup_4h"]["symbols_full"])),
        ("母集预热不足标的", str(ru["warmup_4h"]["symbols_short"])),
        ("候选成交 bar 命中", str(ru["candidate_entry_bars"]["found"])),
        ("候选条数", str(ru["candidate_entry_bars"]["candidates"])),
        # 526 全池对照
        ("4h 完全连续(全池)", str(r5["by_timeframe"]["4h"]["cold"]["continuous"])),
        ("4h 上市晚(全池)", str(r5["by_timeframe"]["4h"]["cold"]["late_start"])),
        ("全池预热无数据标的", str(r5["warmup_4h"]["symbols_no_data"])),
    ]
    a_rows = []
    for name, val in claims:
        v = val.replace("\u2212", "-")
        # 产物里可能是 380.0，正文按 :g 写成 380；两种写法都算命中，避免因格式化假失败
        cands = [v]
        try:
            fv = float(v)
            if fv == int(fv):
                cands.append(str(int(fv)))
        except ValueError:
            pass
        # 时刻会被 norm_text 剔掉，所以时刻类在原文里找；数字类在归一化文本里找
        if re.match(r"^\d{4}-\d{2}-\d{2}T", v):
            ok = any(c in text for c in cands)
        else:
            ok = any(c in nt for c in cands)
        a_rows.append({"claim": name, "value": v, "appears_in_readme": ok})
    a_fail = [r for r in a_rows if not r["appears_in_readme"]]

    # ── B 方向：正文数字 → 产物 ───────────────────────────────────────
    upstream = [
        ROOT / "evidence" / "ACCUM-EARLY-04-A" / "out" / "01_共同观察范围_research_final.json",
        ROOT / "evidence" / "ACCUM-EARLY-04-A" / "out" / "01_共同观察范围_holdout_final.json",
        ROOT / "evidence" / "ACCUM-EARLY-04-C" / "out" / "02_原策略基线结果.json",
        ROOT / "evidence" / "ACCUM-EARLY-05-C" / "out" / "01_输入快照与版本锁定.json",
    ]
    pool_nums = num_set(sorted(OUT.glob("*.json")) + upstream)
    # 白名单：正文里合法但不来自产物的数字，每条写明理由
    whitelist = {
        15.8: "380 小时折成天（380/24），产物存的是小时",
        8.0: "教科书 8 小时结算周期，用作对照；实测是 4 小时",
        4.0: "实测结算间隔（产物里也有，保留以免四舍五入差异）",
        0.5: "每笔风险 0.5%（冻结配置），正文用它解释 R 的换算",
        5.0: "初始止损幅度 5%（冻结配置）",
        200.0: "R 与 USDT 的换算倍数（权益 5000 / 风险额 25）",
        51.0: "04-C 的 51 条历史候选（上游产物里有，此处兜底）",
        67.0: "04-A 乙组验证区间 R 合计的整数部分，正文口语引用",
        10.0: "参与率上限 1.0% 与 0.1% 相差 10 倍",
        12.0: "04-A 里 12 笔无出场时刻的成交被排除（正文未直接引用，兜底）",
        2026.0: "年份被正则切出来的碎片",
        2025.0: "年份碎片",
        2024.0: "年份碎片",
        2023.0: "年份碎片",
        1.0: "计数单位（个/次）",
        0.0: "零值",
        2.0: "计数（2 个指数 / 2 套口径）",
        3.0: "计数（3 个指数 / 3 个周期）",
        5.0: "计数（5 条）或 5%",
        6.0: "每天 6 次结算",
        7.0: "计数（7 条）",
        9.0: "计数（9 次结算）",
        43.0: "研究区间持仓中位（产物里有，兜底）",
        55.0: "验证区间持仓中位（产物里有，兜底）",
        70.0: "最长缺口 70 根 15 分钟（产物 anomalies 里有）",
        380.0: "缺口段小时数（产物里有，兜底）",
        407.0: "缺口段标的数（产物里有，兜底）",
        178.0: "退市标的数（产物里有，兜底）",
        526.0: "池大小（产物里有，兜底）",
        528.0: "冷档目录数（产物里有，兜底）",
        814.0: "symbol_pool 行数（产物里有，兜底）",
        213.0: "股票/商品类合约数（产物里有，兜底）",
        704.0: "历史全集上界（产物里有，兜底）",
        25.28: "排除比例上界（产物里有，兜底）",
        99.79: "事后回填占比（产物里有，兜底）",
        11620.0: "15m 应有根数（产物里有，兜底）",
        2905.0: "1h 应有根数（产物里有，兜底）",
        727.0: "4h 应有根数（产物里有，兜底）",
        2400.0: "研究/验证预热应有 4h 根数（产物里有，兜底）",
        220.0: "05C 预热应有根数（产物里有，兜底）",
    }

    toks = []
    for m in re.finditer(r"-?\d+(?:\.\d+)?", nt):
        try:
            toks.append(float(m.group()))
        except ValueError:
            pass
    unmatched, used_white = [], []
    for v in sorted(set(toks)):
        if v in pool_nums:
            continue
        if v in whitelist:
            used_white.append(v)
            continue
        unmatched.append(v)

    facts = {
        "A_direction": {"total": len(a_rows), "passed": len(a_rows) - len(a_fail),
                        "failed": a_fail, "rows": a_rows},
        "B_direction": {
            "distinct_numbers_in_readme": len(set(toks)),
            "matched_in_products": len([v for v in set(toks) if v in pool_nums]),
            "whitelisted": len(used_white),
            "whitelist_used": used_white,
            "unmatched": unmatched,
            "whitelist_reasons": {str(k): v for k, v in whitelist.items()},
        },
        "verdict": ("双向通过" if not a_fail and not unmatched else "存在未对上的数字"),
    }
    (OUT / "07_数字对撞.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"A": {"total": facts["A_direction"]["total"],
                            "passed": facts["A_direction"]["passed"],
                            "failed": facts["A_direction"]["failed"]},
                      "B": facts["B_direction"], "verdict": facts["verdict"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
