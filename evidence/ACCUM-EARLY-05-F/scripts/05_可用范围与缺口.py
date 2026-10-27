# -*- coding: utf-8 -*-
"""ACCUM-EARLY-05-F｜05 可用范围与阻塞判定（只读，汇总 01–04 的产物）。

输出三张表：
  A. 05-C 窗口的可用范围（周期 × 数据项 × 四层状态）；
  B. 缺口清单，逐条判「是否真正阻塞本次实验」；
  C. 给 05-C 的使用约束（哪些结论能下、哪些不能）。

判「阻塞」的标准只有一条：**不解决它，这次实验的结论会不会是错的**。
「数据不完美」不等于「阻塞」；反过来，「能跑出数」也不等于「结论可信」。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parents[1] / "out"


def iso(ms):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ms) / 1000))


def load(name):
    p = OUT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def main():
    d00 = load("00_范围锚定.json")
    d01 = load("01_币池与退市.json")
    d02 = load("02_四层覆盖.json")
    d03 = load("03_读取链与可得时点.json")
    d04 = load("04_资金费口径.json")

    w = d02["windows"]["05C"]
    r = d02["results"]["05C"]                    # 526 全池（对照口径）
    ru = d02["results"]["05C_universe"]          # 05-C 母集（主口径，48 标的）
    c15, c1h, c4h = (ru["by_timeframe"][k]["cold"] for k in ("15m", "1h", "4h"))
    h15, h1h, h4h = (ru["by_timeframe"][k]["hot"] for k in ("15m", "1h", "4h"))
    pool_n = d02["pool"]["n"]
    uni_n = ru["universe"]["symbol_count"]
    warm = r["warmup_4h"]          # 526 全池对照
    warmu = ru["warmup_4h"]        # 05-C 母集（主口径）

    # ── A. 可用范围 ────────────────────────────────────────────────────
    availability = [
        {"数据项": "15m K 线（成交/止损）", "来源层": "冷档 + 热库合并（research_15m）",
         "文件存在": f"{c15['with_data']}/{uni_n}",
         "窗口连续": f"完全连续 {c15['continuous']}/{uni_n}，"
                     f"上市晚 {c15['late_start']}，中间断流 {c15['mid_gap']}，"
                     f"尾部提前结束 {c15['tail_short']}",
         "当时可得": f"冷档 available_at 填充 {c15['available_at_filled']}/{c15['available_at_total']}；"
                     f"热库 {h15['available_at_filled']}/{h15['available_at_total']} 有值",
         "实际读取": "是（04-A 成交/止损；热库 15m 起点之前只能来自冷档）",
         "可用": "可用（受限）",
         "限制": "成交 bar 若落在 2026-09-12 之前，必然来自冷档 ⇒ 无抓取时刻证据"},
        {"数据项": "1h K 线（压制位/承接/确认）", "来源层": "冷档 + 热库合并",
         "文件存在": f"{c1h['with_data']}/{uni_n}",
         "窗口连续": f"完全连续 {c1h['continuous']}/{uni_n}，"
                     f"上市晚 {c1h['late_start']}，中间断流 {c1h['mid_gap']}",
         "当时可得": f"冷档 {c1h['available_at_filled']}/{c1h['available_at_total']}；"
                     f"热库 {h1h['available_at_filled']}/{h1h['available_at_total']} 有值",
         "实际读取": "是（局部压制位、承接占比、确认收盘）",
         "可用": "可用（受限）",
         "限制": "同上：窗口前段全部依赖冷档"},
        {"数据项": "4h K 线（箱体/ATR/跟踪止损）", "来源层": "冷档 + 热库合并",
         "文件存在": f"{c4h['with_data']}/{uni_n}",
         "窗口连续": f"完全连续 {c4h['continuous']}/{uni_n}，"
                     f"上市晚 {c4h['late_start']}，中间断流 {c4h['mid_gap']}",
         "当时可得": f"冷档 {c4h['available_at_filled']}/{c4h['available_at_total']}；"
                     f"热库 {h4h['available_at_filled']}/{h4h['available_at_total']} 有值",
         "实际读取": "是（42 根箱体、Wilder ATR、跟踪止损）",
         "可用": "可用（受限）",
         "限制": f"预热段在母集里全部完整（{warmu['symbols_full']}/{uni_n}）；"
                 f"扩到全池才有 {warm['symbols_short']}/{pool_n} 个标的不足，见报告 2.4 节"},
        {"数据项": "成交量 / 主动买卖额（quote_volume / taker_buy_quote_volume）",
         "来源层": "K 线自带列（不是 metrics 指标归档）",
         "文件存在": "与 1h/4h K 线同源，同一批行",
         "窗口连续": "同 K 线（同一行里的列，不会单独缺）",
         "当时可得": "随该根 bar 收盘即可知；但冷档没有抓取时刻证据",
         "实际读取": "是（承接占比 = 主动买额/成交额；卖压为推导量）",
         "可用": "可用（受限）",
         "限制": "不要与「指标归档（metrics parquet）」混为一谈，后者停更且策略不用"},
        {"数据项": "资金费", "来源层": "热库 funding_history / 冷档 funding parquet",
         "文件存在": f"冷档 {d03['non_bar_inputs']['funding']['cold_files']} 个文件；"
                     f"热库 {d03['non_bar_inputs']['funding']['hot_rows']} 行",
         "窗口连续": "热库有 15.8 天缺口（2026-09-02T20:00Z→2026-09-18T16:00Z，"
                     f"{d03['funding_coverage_gap']['hot_gap_segments'][0]['symbols']} 个标的）；"
                     "冷档同段完整",
         "当时可得": f"热库 fetched_at 100% 有值，但 "
                     f"{d03['non_bar_inputs']['funding']['fetch_lag']['gt_24h_pct']}% 是事后 >24h 回填",
         "实际读取": "04-A 不读；04-C/04-D 只读热库",
         "可用": "部分可用",
         "限制": "只读热库会漏掉 15.8 天；必须冷热合并才完整"},
        {"数据项": "合约规格（步进/最小量/最小金额）", "来源层": "CSV 单时点快照",
         "文件存在": f"{d03['non_bar_inputs']['contract_spec']['rows']} 行（526 币）",
         "窗口连续": "不适用（快照，不是时间序列）",
         "当时可得": f"官方材料日期 {d03['non_bar_inputs']['contract_spec']['official_material_dates']}，"
                     "文件自写「历史变化已证明=False」",
         "实际读取": "04-A 不读（R 倍数口径）；04-C/D 读（取整/拒单）",
         "可用": "近似可用",
         "限制": "对 2025 年的回放等于用今天的规格；误差方向不可判"},
        {"数据项": "持仓量 OI", "来源层": "热库 oi_history / 冷档 oi",
         "文件存在": f"热库 {d03['non_bar_inputs']['open_interest']['hot_rows']} 行，"
                     f"起 {d03['non_bar_inputs']['open_interest']['first_utc']}",
         "窗口连续": "热库起点晚于 05-C 窗口起点 ⇒ 窗口前段无 OI",
         "当时可得": "fetched_at 有值但有大量为空；历史可得时点不可证",
         "实际读取": "否（04-A 三组规则与基线都不读 OI）",
         "可用": "不参与本次实验",
         "限制": "按规则用途说明即可；不因缺证明而给策略加门槛"},
    ]

    # ── B. 缺口清单 + 阻塞判定 ─────────────────────────────────────────
    gaps = [
        {"编号": "G1", "缺口": f"冷档全部 K 线无抓取时刻（available_at 填充率 0%）",
         "取证": "02 号脚本：15m/1h/4h 的 available_at_filled 均为 0",
         "阻塞什么": "阻塞「当时确实拿得到这根 bar」的声明；不阻塞「计算没用未来数据」",
         "阻塞等级": "限制声明，不阻塞回放",
         "能否补": "不能。冷档本身没存这个字段，补不了；只能如实标注"},
        {"编号": "G2", "缺口": f"全池 {pool_n} 里有 {r['by_timeframe']['15m']['cold']['late_start']} 个新币在窗口前段无数据"
                            f"（05-C 母集 {uni_n} 标的里是 {c15['late_start']} 个）",
         "取证": "02 号脚本 anomalies（kind=late_start），名单见 out/02",
         "阻塞什么": "只阻塞「这些标的在窗口全段可观察」的说法",
         "阻塞等级": "不阻塞（新币本来就不该有上市前数据）",
         "能否补": "不需要补；按上市时刻裁剪即可"},
        {"编号": "G3", "缺口": "热库 15m/1h/4h 起点晚于窗口起点（09-07/09-09/09-12）",
         "取证": "03 号脚本 hot_first_bar",
         "阻塞什么": "阻塞「用热库就能跑完窗口」；不阻塞「冷热合并后能跑完」",
         "阻塞等级": "不阻塞（合并读取器已存在且被 04-A 实际使用）",
         "能否补": "不需要补；继续走 research_15m 合并读取"},
        {"编号": "G4",
         "缺口": ("热库资金费 15.8 天缺口（2026-09-02T20:00Z→2026-09-18T16:00Z，"
                  f"{d03['funding_coverage_gap']['hot_gap_segments'][0]['symbols']} 个标的）"),
         "取证": ("03 号脚本 funding_coverage_gap：同区间冷档 "
                  f"{d03['funding_coverage_gap']['cold_rows']} 行 > 热库 "
                  f"{d03['funding_coverage_gap']['hot_rows']} 行；"
                  f"04 号脚本 96 笔资金费缺口 100% 落在该段"),
         "阻塞什么": "阻塞「资金费已完整计入」的声明；使任何只读热库的资金费核算偏低",
         "阻塞等级": "阻塞资金费结论（不阻塞价格链回放）",
         "能否补": "能，且不需要新采集：冷档同段有数据，改读取为冷热合并即可"},
        {"编号": "G5", "缺口": "资金费无「结算当时可得」证据（99.79% 是事后 >24h 回填）",
         "取证": "03 号脚本 fetch_lag",
         "阻塞什么": "阻塞把资金费写进「当时可得」的严格有效性成绩",
         "阻塞等级": "限制声明，不阻塞成本核算",
         "能否补": "不能补历史；只能在今后的自然运行里累积真实抓取时刻"},
        {"编号": "G6", "缺口": "合约规格是 2026-10-11 单时点快照，历史变化未证明",
         "取证": "03 号脚本 contract_spec（文件自写 历史变化已证明=False）",
         "阻塞什么": "阻塞「精确实盘还原」；不阻塞「近似回测」",
         "阻塞等级": "限制声明，不阻塞近似结论",
         "能否补": "需要交易所历史 exchangeInfo，本单不启动；成本高、收益低"},
        {"编号": "G7", "缺口": "178 个已退市标的没有任何 K 线归档（冷档 0 目录、热库 0 行）",
         "取证": "01 号脚本 delisted",
         "阻塞什么": "阻塞「结论覆盖历史全池」；方向明确（对只做多高估收益）",
         "阻塞等级": "限制外推，不阻塞本次实验",
         "能否补": "需要重新下载已下架合约历史，本单不启动"},
        {"编号": "G8", "缺口": f"4h 预热段：母集 {uni_n} 标的全部完整（{warmu['symbols_full']}/{uni_n}，不足 "
                            f"{warmu['symbols_short']}、无数据 {warmu['symbols_no_data']}）；"
                            f"扩到全池则有 {warm['symbols_short']}/{pool_n} 个标的不足 "
                            f"{warm['expected_bars']} 根（其中 {warm['symbols_no_data']} 个完全无数据）",
         "取证": "02 号脚本 warmup_4h（05C_universe 与 05C 两组）",
         "阻塞什么": "对本次实验不构成缺口（母集 ATR/箱体起点是满的）；只在扩到全池时阻塞「所有标的从同一起点递推」",
         "阻塞等级": "不阻塞本次实验",
         "能否补": "不能补历史；扩到全池时只能按标的实际起点标注"},
        {"编号": "G9", "缺口": "指标归档（主动买卖比率 / 多空比）停在 2026-10-14T23:55Z",
         "取证": "03-A 已登记；本单复核：策略读的是 K 线自带列，不是指标归档",
         "阻塞什么": "不阻塞本次实验（策略不用这条链）",
         "阻塞等级": "不阻塞",
         "能否补": "跨界，本单不做"},
        {"编号": "G10", "缺口": "okx 286 个合约不参与；币安股票/商品类 213 个合约无 K 线",
         "取证": "01 号脚本 denominators",
         "阻塞什么": "不阻塞（策略对象是币安 USD-M 加密币永续）",
         "阻塞等级": "不阻塞",
         "能否补": "不需要"},
    ]

    # ── C. 给 05-C 的使用约束 ──────────────────────────────────────────
    constraints = [
        "可以下：在 2026-06-27T08:00Z ~ 2026-10-26T09:00Z 内，"
        f"05-C 母集的 {uni_n} 个标的的 15m/1h/4h 冷档在窗口内**全部连续**（完全连续 "
        f"{c15['continuous']}/{c1h['continuous']}/{c4h['continuous']}），"
        "中间断流 0、上市晚 0、尾部提前结束 0。",
        f"可以下：51 条候选的成交 bar 在冷档里全部存在（{ru.get('candidate_entry_bars', {}).get('found')}"
        f"/{ru.get('candidate_entry_bars', {}).get('candidates')}）。",
        "不可以下：这些 K 线「当时本机就能拿到」。冷档没有抓取时刻，只能声明计算不超前。",
        "可以下：成交 bar 落在 2026-09-12 之前的，必然来自冷档（热库 15m 起点之前无数据）。",
        "不可以下：把冷档成交算进「严格有效性成绩」。这类成交只能标探索性。",
        "可以下：资金费能按结算时刻对齐，且能逐笔算。",
        "不可以下：说「资金费已完整计入」。热库缺 15.8 天，04-C/04-D 只读热库。",
        "可以下：04-A 的 R 结果与 04-C/D 的 USDT 结果各自内部自洽。",
        "不可以下：把两者直接横比。计量单位、本金、滑点、参与率、候选集、区间六项都不同。",
        f"可以下：本单统计的覆盖情况适用于 05-C 母集 {uni_n} 个标的 + 05-C 窗口。",
        f"不可以下：把这 {uni_n} 个标的的「0 异常」当成全池结论。"
        f"扩到 {pool_n} 全池就有 {r['by_timeframe']['15m']['cold']['late_start']} 个标的窗口前段无数据。",
        "不可以下：把它推广到「全池历史都覆盖」。178 个退市标的完全无数据。",
    ]

    facts = {
        "generated_at_utc": iso(int(time.time() * 1000)),
        "object": {
            "window_05C": w,
            "universe_05C": ru["universe"],
            "pool_526_for_reference": d02["pool"],
            "note": (f"主口径 = 05-C 的统一观察母集（{uni_n} 标的、"
                     f"{ru['universe']['events_total']} 事件）；"
                     f"{pool_n} 全池只作「若扩到全池」的对照"),
            "candidate_entry_bars": ru.get("candidate_entry_bars"),
            "universe_anomaly_count": ru.get("anomaly_count"),
        },
        "availability": availability,
        "gaps": gaps,
        "constraints": constraints,
        "blocking_summary": {
            "真正阻塞结论的": [g["编号"] for g in gaps if g["阻塞等级"].startswith("阻塞")],
            "只限制声明的": [g["编号"] for g in gaps if g["阻塞等级"].startswith("限制")],
            "不阻塞的": [g["编号"] for g in gaps if g["阻塞等级"].startswith("不阻塞")],
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "05_可用范围与缺口.json").write_text(
        json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(facts["blocking_summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
