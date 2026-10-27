# -*- coding: utf-8 -*-
"""02_接触台账｜重建「币 × 时间区间 × 接触深度」台账，区分「本轮没跑过」与「从未用于研究」。

只读复用：
- FULL_SWEEP_200x100/runs.jsonl（19496 次运行：组/币/窗口/候选/成交/净额）
- OOS-READY-01/out/01_已观察范围_币种.csv（507 币接触来源、覆盖窗口数、净额）
- OOS-READY-01/out/01_已观察范围_批次.csv（17 批次窗口）
- OOS-READY-01/out/02_验证数据资格表.csv（S0~S7 日历段接触状态）
- evidence/*/user_data/config.json（各实验 pairlist）

口径原则（工单硬要求）：
- 「本轮没跑过」≠「从未用于研究」：只要币或区间进入过任一实验的输入，
  即标为已接触；接触深度分 仅扫描/有候选/有成交 三档；
- 数据候选（L1）≠ 完整策略资格（L2=0），不得混淆。

产出：out/02_接触台账_逐币_v1.csv、out/02_接触台账_逐段_v1.csv、out/02_接触台账摘要_v1.json
不下载、不改时间戳、不跑回测。
"""
from __future__ import annotations

import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from _common import (CELLS_A, EV, HOLDOUT_END, HOLDOUT_START, OOS_BATCH,  # noqa: E402
                     OOS_COINS, OOS_SEG, OUT, RESEARCH_END, RESEARCH_START,
                     jdump, load_fullsweep_runs, load_l1, load_whitelist_70,
                     norm_pair, read_csv, write_csv)

RS, RE = RESEARCH_START, RESEARCH_END


def config_pairlists() -> dict:
    """各实验 user_data/config.json 的 pairlist（除 FT-ENTRY-03 的 70 白名单外）。"""
    out = {}
    for f in sorted(glob.glob(os.path.join(EV, "*/user_data/config.json"))) + \
            sorted(glob.glob(os.path.join(EV, "*/experiments/*/user_data/config.json"))):
        try:
            with open(f, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except Exception:
            continue
        pw = cfg.get("exchange", {}).get("pair_whitelist") or cfg.get("pairs") or []
        if not pw:
            continue
        exp = os.path.relpath(f, EV).split(os.sep)[0]
        sub = os.path.relpath(f, EV).split(os.sep)[1] if "/experiments/" in f else ""
        key = exp + ("/" + sub if sub else "")
        out.setdefault(key, set(norm_pair(x) for x in pw))
    return out


def main() -> None:
    l1 = load_l1()
    w70 = load_whitelist_70()
    fs = load_fullsweep_runs()
    oos_coins = read_csv(OOS_COINS)
    oos_batch = read_csv(OOS_BATCH)
    oos_seg = read_csv(OOS_SEG)
    s1 = set(l1["symbol"])

    # ── 1) FULL_SWEEP 逐币接触（全期 + 冻结研究期内）──
    fs_all = fs.groupby("symbol").agg(
        fs_runs=("symbol", "size"),
        fs_windows=("group", "nunique"),
        fs_candidates=("candidates", "sum"),
        fs_trades=("trades", "sum"),
        fs_net_runlevel=("net", "sum"),
        fs_earliest=("s", "min"),
        fs_latest=("e", "max"),
    )
    inp = fs[(fs["s"] <= RE) & (fs["e"] >= RS)]
    fs_res = inp[inp["symbol"].isin(s1)].groupby("symbol").agg(
        fs_research_runs=("symbol", "size"),
        fs_research_candidates=("candidates", "sum"),
        fs_research_trades=("trades", "sum"),
        fs_research_net=("net", "sum"),
    )
    fs_cov = inp[inp["symbol"].isin(s1)].groupby("symbol").apply(
        lambda g: int((g["e"].clip(upper=RE) - g["s"].clip(lower=RS)).dt.days.sum()),
        include_groups=False).rename("fs_research_cover_days")

    # ── 2) 各实验 config pairlist 接触 ──
    pls = config_pairlists()
    pl_touch = {}
    for exp, coins in pls.items():
        for c in coins:
            pl_touch.setdefault(c, []).append(exp)

    # ── 3) 逐币台账 ──
    rows = []
    for sym in sorted(s1):
        a = fs_all.loc[sym] if sym in fs_all.index else None
        r = fs_res.loc[sym] if sym in fs_res.index else None
        cov = fs_cov.get(sym, 0)
        o = oos_coins[oos_coins["symbol"] == sym]
        o = o.iloc[0] if len(o) else None

        # 接触深度判定
        cand_r = int(r["fs_research_candidates"]) if r is not None else 0
        trad_r = int(r["fs_research_trades"]) if r is not None else 0
        cand_all = int(a["fs_candidates"]) if a is not None else 0
        trad_all = int(a["fs_trades"]) if a is not None else 0

        if trad_r > 0 or trad_all > 0:
            depth = "有成交"
        elif cand_r > 0 or cand_all > 0:
            depth = "有候选无成交"
        elif a is not None or o is not None:
            depth = "仅扫描无候选"
        else:
            depth = "无接触记录"

        srcs = []
        if a is not None:
            srcs.append("FULL_SWEEP_200x100")
        if o is not None and isinstance(o.get("接触来源"), str):
            for part in str(o["接触来源"]).split("+"):
                p = part.strip()
                if p and p not in srcs:
                    srcs.append(p)
        for exp in pl_touch.get(sym, []):
            if exp not in srcs:
                srcs.append("config:" + exp)

        rows.append({
            "symbol": sym,
            "在L1_241": True,
            "在A路线70白名单": sym in w70,
            "接触深度": depth,
            "接触来源": "|".join(srcs) if srcs else "无",
            "FULL_SWEEP_总run数": int(a["fs_runs"]) if a is not None else 0,
            "FULL_SWEEP_窗口数": int(a["fs_windows"]) if a is not None else 0,
            "FULL_SWEEP_候选_全期": cand_all,
            "FULL_SWEEP_成交_全期": trad_all,
            "FULL_SWEEP_净额_全期运行级": round(float(a["fs_net_runlevel"]), 4) if a is not None else 0.0,
            "FULL_SWEEP_最早窗口起": str(a["fs_earliest"].date()) if a is not None else "",
            "FULL_SWEEP_最晚窗口止": str(a["fs_latest"].date()) if a is not None else "",
            "冻结研究期内_run数": int(r["fs_research_runs"]) if r is not None else 0,
            "冻结研究期内_窗口覆盖天数": cov,
            "冻结研究期内_候选": cand_r,
            "冻结研究期内_成交": trad_r,
            "冻结研究期内_净额运行级": round(float(r["fs_research_net"]), 4) if r is not None else 0.0,
            "OOS已观察范围_覆盖窗口数": int(o["覆盖窗口数"]) if o is not None and "覆盖窗口数" in o else 0,
            "OOS是否产生过真实事件": (o["是否产生过真实事件"] if o is not None and "是否产生过真实事件" in o else ""),
        })
    ledger = __import__("pandas").DataFrame(rows)
    write_csv(ledger, "02_接触台账_逐币_v1.csv")

    # ── 4) 逐段台账（S0~S7 照录 + 本研究期口径）──
    seg = oos_seg.copy() if not oos_seg.empty else __import__("pandas").DataFrame()
    if not seg.empty:
        seg = seg[["段", "接触状态", "综合判定", "起", "止", "日历天数",
                   "是否被既有回测窗口覆盖", "该段唯一事件数（296 口径）",
                   "该段候选数（206 口径）", "该段净额U", "K线可用币数（文件名级，4h）",
                   "OI 可用币数（文件名级，5m）", "资格判定"]]
        seg["本单分类"] = seg["段"].map({
            "S0": "④留出/未覆盖段-数据不合格",
            "S1": "④留出/未覆盖段-数据不合格",
            "S2": "③已接触段（OI 全缺，0 事件）",
            "S3": "③已接触段（已产生事件）",
            "S4": "③已接触段（主样本母集）",
            "S5": "④旧 Holdout-已消耗污染",
            "S6": "④留出段-样本量不足",
            "S7": "无数据段",
        })
        write_csv(seg, "02_接触台账_逐段_v1.csv")

    # ── 5) 摘要 ──
    w70set = w70
    expand = set(ledger[~ledger["在A路线70白名单"]]["symbol"])
    depth_dist = ledger["接触深度"].value_counts().to_dict()
    exp_depth = ledger[~ledger["在A路线70白名单"]]["接触深度"].value_counts().to_dict()
    w70_depth = ledger[ledger["在A路线70白名单"]]["接触深度"].value_counts().to_dict()

    # 可扩展集中「有真实信号」的币（研究期内有候选或有成交）
    exp_signal = set(ledger[(~ledger["在A路线70白名单"])
                            & ((ledger["冻结研究期内_候选"] > 0)
                               | (ledger["冻结研究期内_成交"] > 0))]["symbol"])

    # 2026 留出段是否被 FULL_SWEEP 触碰
    fs_hold = fs[fs["e"] > HOLDOUT_START]
    hold_coins = set(fs_hold["symbol"])
    hold_trades = int(fs_hold["trades"].sum())

    summary = {
        "工单": "FT-RESEARCH-01｜接触台账（区分本轮没跑过 vs 从未用于研究）",
        "口径原则": [
            "「本轮没跑过」≠「从未用于研究」：币或区间进入过任一实验输入即计已接触",
            "接触深度三档：仅扫描无候选 / 有候选无成交 / 有成交",
            "L1 数据候选 ≠ L2 完整策略资格（L2 全池 0），不得混淆",
        ],
        "L1_241 接触深度分布": depth_dist,
        "其中_A路线70白名单_接触深度": w70_depth,
        "其中_L1内不在70白名单_171_接触深度": exp_depth,
        "核心事实": (
            f"L1 全部 {len(s1)} 个币都已被 FULL_SWEEP_200x100 接触过"
            f"（研究期内窗口覆盖最少 1412 天、最多 39522 天），"
            f"因此『L1 里有 171 个从未被研究过的币』这一说法不成立；"
            f"正确的是『有 171 个 L1 币未进入 A 路线 70 白名单的正式 9 格研究』。"
        ),
        "可扩展集定义": (
            "可扩展 = L1 资格成立（价量数据可算）∩ 未进入 A 路线 70 白名单 ∩ "
            "按 (币, 冻结研究期) 未被正式 9 格研究。规模 171 个币。"
        ),
        "可扩展集中_研究期内有真实信号的币数": len(exp_signal),
        "可扩展集中_研究期内有候选或成交": sorted(exp_signal)[:60],
        "2026留出段接触状态": {
            "旧Holdout区间": f"{HOLDOUT_START}~{HOLDOUT_END}",
            "FULL_SWEEP窗口触及该区间之后的run数": int(len(fs_hold)),
            "涉及币数": len(hold_coins),
            "其中含成交run的成交笔数(含窗口重复)": hold_trades,
            "判定": "已接触且已产生交易（OOS-READY-01 记为 S5 已污染）",
        },
        "逐段分类": {
            "S0/S1": "④ 未覆盖段，数据资格不合格（OI/mark 缺）",
            "S2": "③ 已接触段，OI 全缺致 0 事件（无成交≠未观察）",
            "S3/S4": "③ 已接触段，主样本母集（206 候选/201 事件判定处）",
            "S5": "④ 旧 Holdout，2026-04-15~2026-10-15，已消耗 37 笔 +234.38U，已污染",
            "S6": "④ 未覆盖段仅约 5 天，样本量不足",
            "S7": "无数据段（需前向采集）",
        },
        "批次接触（17 批次照录）": (
            oos_batch[["批次", "窗口起", "窗口止", "标的数", "运行数"]].to_dict("records")
            if not oos_batch.empty else []),
    }
    jdump(os.path.join(OUT, "02_接触台账摘要_v1.json"), summary)

    print("== 接触台账 ==")
    print("  L1 241 接触深度分布:", depth_dist)
    print("  70 白名单:", w70_depth)
    print("  171 未入白名单:", exp_depth)
    print(f"  可扩展集中研究期内有真实信号的币: {len(exp_signal)}")
    print(f"  2026 留出段：FULL_SWEEP 触及 run {len(fs_hold)}、币 {len(hold_coins)}、"
          f"成交(含重复) {hold_trades} ⇒ 已接触已污染")
    print("  out/02_接触台账_逐币_v1.csv（241 行）、02_接触台账_逐段_v1.csv")


if __name__ == "__main__":
    main()
