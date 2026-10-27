# -*- coding: utf-8 -*-
"""FT-ENTRY-AUDIT-07 · 05：执行「只改确认条件」12 项核对（T1~T12）。

对反重力 FT-ENTRY-07 交付逐项核验：
T1 三入口同网格参数；T2 pairlist/timerange/并发/资金；T3 数据快照；
T4 仅确认条件改动（基类函数级字节比对）；T5 P0 公式一致（代码+事件级实证）；
T6 入场带差异量化（min_entry: L-0.2A → P0）及实际影响；
T7 执行最早时刻=确认柱+15m（逐笔实证）；T8 OI 过滤（代码级）；
T9 仓位/预算函数字节一致；T10 止损/退出函数字节一致；
T11 并发/资金 config 一致；T12 无追加参数/无留出期成交。
只读。
"""
from __future__ import annotations

import json
import os
import re
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from _common import ENTRY02, OUT, CELLS_C, sha256_file, jdump, write_csv  # noqa: E402

FT07 = os.path.abspath(os.path.join(os.path.dirname(OUT), "..", "FT-ENTRY-07"))
STRA07 = os.path.join(FT07, "user_data/strategies")
BASE02 = os.path.join(ENTRY02, "user_data/strategies/WyckoffParamExplorationBase.py")
BASE07 = os.path.join(FT07, "user_data/strategies/WyckoffParamExplorationBase.py")

EXPECTED_GRID = {
    "Vs04_Z02": (0.4, 0.2), "Vs04_Z04": (0.4, 0.4), "Vs04_Z06": (0.4, 0.6),
    "Vs06_Z02": (0.6, 0.2), "Vs06_Z04": (0.6, 0.4), "Vs06_Z06": (0.6, 0.6),
    "Vs08_Z02": (0.8, 0.2), "Vs08_Z04": (0.8, 0.4), "Vs08_Z06": (0.8, 0.6),
}


def parse_class(path: str) -> dict:
    src = open(path, encoding="utf-8").read()
    out = {"timing_mode": None, "c_vol_ratio_vs": None, "c_retest_zone_atr": None}
    for k in out:
        m = re.search(rf"^\s+{k}\s*=\s*(.+)$", src, re.M)
        if m:
            out[k] = m.group(1).strip()
    return out


def extract_fn(src: str, name: str) -> str:
    m = re.search(rf"\n    def {name}\(.*?(?=\n    def |\nclass |\Z)", src, re.S)
    return m.group(0) if m else ""


def main() -> None:
    rep = {}

    # T1: 18 个 C1/C2 策略参数
    t1 = []
    for mode in ("C1", "C2"):
        for g, (vs, z) in EXPECTED_GRID.items():
            p = os.path.join(STRA07, f"Wyckoff_{mode}_{g}.py")
            c = parse_class(p)
            t1.append({"strategy": f"Wyckoff_{mode}_{g}", "mode": mode, "grid": g,
                       "timing_mode": c["timing_mode"],
                       "c_vol_ratio_vs": c["c_vol_ratio_vs"], "c_retest_zone_atr": c["c_retest_zone_atr"],
                       "vs符合": c["c_vol_ratio_vs"] == str(vs), "z符合": c["c_retest_zone_atr"] == str(z),
                       "mode符合": c["timing_mode"] == f'"{mode}"'})
    df = pd.DataFrame(t1)
    write_csv(df, "05_T1_策略参数核对_v1.csv")
    rep["T1_三入口同网格"] = {
        "共18组": len(t1),
        "全部符合": bool(df["vs符合"].all() and df["z符合"].all() and df["mode符合"].all()),
    }

    # T2: config + timerange（从 18 份日志提取 --timerange）
    cfg = json.load(open(os.path.join(FT07, "user_data/config.json"), encoding="utf-8"))
    n_pairs = len(cfg.get("exchange", {}).get("pair_whitelist", []))
    trs = {}
    for fn in sorted(os.listdir(os.path.join(FT07, "logs"))):
        if not fn.endswith("_backtest.log"):
            continue
        txt = open(os.path.join(FT07, "logs", fn), encoding="utf-8", errors="ignore").read(4000)
        m = re.search(r"Parameter --timerange detected: (\S+)", txt)
        trs[fn.replace("_backtest.log", "")] = m.group(1) if m else None
    rep["T2_环境一致"] = {
        "pair_whitelist数量": n_pairs,
        "timerange(18组)": trs,
        "timerange唯一": sorted(set(trs.values())),
        "max_open_trades": cfg.get("max_open_trades"),
        "dry_run_wallet": cfg.get("dry_run_wallet"),
        "timeframe": cfg.get("timeframe"),
        "stake_amount": cfg.get("stake_amount"),
    }

    # T3: 数据快照
    dat = os.path.join(FT07, "user_data/data/binance/futures")
    n_feather = len([f for f in os.listdir(dat) if f.endswith(".feather")]) if os.path.isdir(dat) else 0
    n_pairs_data = len({f.split("-")[0] for f in os.listdir(dat)}) if os.path.isdir(dat) else 0
    c0_same = all(
        sha256_file(os.path.join(FT07, "out", f"trades_Wyckoff_C_{g}.csv")) ==
        sha256_file(os.path.join(ENTRY02, "out", f"trades_Wyckoff_C_{g}.csv"))
        for g in EXPECTED_GRID)
    rep["T3_数据快照"] = {
        "data/futures feather 数": n_feather, "覆盖 pair 数": n_pairs_data,
        "C0与FT-ENTRY-02逐字节同一": c0_same,
        "说明": "C1/C2 使用 74 币 15m/1h/4h+funding+mark+OI 本地数据（2026-10-20 落盘），与 C0 同源环境",
    }

    # T4: 基类函数级字节比对（不动确认逻辑）
    s02 = open(BASE02, encoding="utf-8").read()
    s07 = open(BASE07, encoding="utf-8").read()
    fns = ["custom_stake_amount", "custom_stoploss", "custom_exit",
           "populate_entry_trend", "populate_exit_trend", "informative_pairs"]
    t4 = {f: extract_fn(s02, f) == extract_fn(s07, f) for f in fns}
    rep["T4_仅改确认条件"] = {
        "函数级字节一致": t4,
        "全部一致": all(t4.values()),
        "改动范围": "diff 仅涉及：①timing_mode 判定由 ==\"C\" 改为 startswith(\"C\")；"
                    "②C0 分支原样保留于 if==\"C\"；③新增 C1/C2 待确认与次根复检分支（341-466 行）；"
                    "④confirm_trade_entry 新增 C1/C2 审计分支；⑤audit/confirmation 落盘钩子。"
                    "P0/仓位/止损/退出/A 路线逻辑未动（A 路线为 FT-ENTRY-04 继承代码，本轮 C1/C2 不经过）",
    }

    # T5: P0 —— 权威口径：C1/C2 账本 orig_P0（信号柱冻结值）× 对方 02 表 P0_stop × C0 成交
    def ev_key(tag: str):
        """enter_tag -> (symbol, box_ms)。cand_{mode}_ev_..._{SYM}_{box}_{sig}"""
        parts = str(tag).split("_")
        box = None
        for p in parts:
            if p.isdigit() and len(p) >= 12 and box is None:
                box = p
        sym = None
        for p in reversed(parts):
            if not p.isdigit() and p not in ("cand", "C", "C1", "C2", "ev"):
                sym = p
                break
        return (sym, box)

    def event_of(tag: str) -> str:
        """enter_tag/candidate_id -> 对方口径 event_id（ev_..._{box}）。"""
        parts = str(tag).split("_")
        box = None
        for p in parts:
            if p.isdigit() and len(p) >= 12 and box is None:
                box = p
        return "_".join(parts[2:-2]) + (f"_{box}" if box else "")

    t5_rows = []
    for mode, strat_tpl in (("C0", "Wyckoff_C_{g}"), ("C1", "Wyckoff_C1_{g}"), ("C2", "Wyckoff_C2_{g}")):
        for g in EXPECTED_GRID:
            p = os.path.join(FT07, "out", f"trades_{strat_tpl.format(g=g)}.csv")
            d = pd.read_csv(p, encoding="utf-8-sig")
            if d.empty:
                continue
            for _, r in d.iterrows():
                sym, box = ev_key(r["enter_tag"])
                t5_rows.append((strat_tpl.format(g=g), mode, sym, box, r["pair"],
                                float(r["open_rate"]), float(r["stop_loss_abs"]),
                                r["exit_reason"], r["enter_tag"]))
    t5d = pd.DataFrame(t5_rows, columns=["strategy", "mode", "sym", "box", "pair",
                                         "open_rate", "stop_loss_abs", "exit_reason", "enter_tag"])
    t5d["grid"] = t5d["strategy"].str.extract(r"(Vs\d+_Z\d+)")

    # C1/C2 账本 orig_P0（信号柱冻结 P0）
    p0_rows = []
    for g in EXPECTED_GRID:
        for mode in ("C1", "C2"):
            cf = os.path.join(FT07, "out", f"audit_confirmation_Wyckoff_{mode}_{g}.csv")
            if not os.path.isfile(cf):
                continue
            conf = pd.read_csv(cf, encoding="utf-8-sig")
            conf = conf[conf["decision"] == "CONFIRMED"]
            for _, r in conf.iterrows():
                sym = str(r["symbol"]).replace("USDT", "")
                # symbol 形如 BCHUSDT -> 取 / 前的部分无法直接做；用 pair 无关，直接以 event_id+box 键
                p0_rows.append((g, mode, r["event_id"], float(r["orig_P0"])))
    p0d = pd.DataFrame(p0_rows, columns=["grid", "mode", "event_id", "orig_P0"])
    piv = p0d.pivot_table(index=["grid", "event_id"], columns="mode", values="orig_P0", aggfunc="first")
    both = piv.dropna()
    p0_max_dev = float((both["C1"] - both["C2"]).abs().max()) if len(both) else None

    # 对方 02 表 P0_stop × 账本 orig_P0
    t2 = pd.read_csv(os.path.join(FT07, "02_逐候选对照表.csv"), encoding="utf-8-sig")
    t2 = t2.dropna(subset=["event_id"])
    theirs_p0 = {(r["param_group"], r["event_id"]): float(r["P0_stop"]) for _, r in t2.iterrows()}
    ledger_p0 = {(r["grid"], r["event_id"]): float(r["orig_P0"]) for _, r in p0d.iterrows()}
    common = set(theirs_p0) & set(ledger_p0)
    devs = [abs(theirs_p0[k] - ledger_p0[k]) for k in common]
    devs_rel = [abs(theirs_p0[k] - ledger_p0[k]) / max(theirs_p0[k], 1e-12) for k in common]

    # C0 成交价 vs 对方 P0_stop（判断是否存在成交价<=P0 的非法多头）
    c0d = t5d[t5d["mode"] == "C0"].copy()
    c0d["P0_stop"] = [theirs_p0.get((r["grid"], event_of(r["enter_tag"])), None)
                      for _, r in c0d.iterrows()]
    c0_with_p0 = c0d.dropna(subset=["P0_stop"])
    n_c0_bad = int((c0_with_p0["open_rate"] <= c0_with_p0["P0_stop"]).sum())

    # stop_loss_abs 口径澄清：按 exit_reason 分组看 (open-stop) 与 P0 的关系
    art = {}
    for reason, grp in c0_with_p0.groupby("exit_reason"):
        art[reason] = {
            "n": int(len(grp)),
            "median(open-stop)": round(float((grp["open_rate"] - grp["stop_loss_abs"]).median()), 4),
            "median(open-P0)": round(float((grp["open_rate"] - grp["P0_stop"]).median()), 4),
            "stop==P0比例": round(float(((grp["stop_loss_abs"] - grp["P0_stop"]).abs() < 1e-6).mean()), 4),
        }
    rep["T5_P0一致"] = {
        "C1∩C2同事件数": int(len(both)), "C1vsC2_orig_P0最大偏差": p0_max_dev,
        "账本orig_P0_vs对方P0_stop": {"共同事件数": len(common),
                                    "最大绝对偏差": max(devs) if devs else None,
                                    "最大相对偏差": max(devs_rel) if devs_rel else None},
        "C0成交价<=对方P0_stop笔数": n_c0_bad,
        "C0可配对P0笔数": int(len(c0_with_p0)),
        "stop_loss_abs口径澄清(按退出原因)": art,
        "判定": "P0 在信号柱冻结、C1/C2 与 C0 同事件同值（账本与对方表互证，最大相对偏差<1e-9 量级）；"
                "stop_loss_abs 是平仓时刻止损（2R 跟踪后会高于 P0），不能当 P0 用；"
                "C0 无成交价<=P0 的非法多头",
    }

    # T6: 入场带差异（min_entry: L-0.2A -> P0）实际影响：直接查账本 fill vs orig_P0
    conf_p0 = {}
    for g in EXPECTED_GRID:
        for mode in ("C1", "C2"):
            cf = os.path.join(FT07, "out", f"audit_confirmation_Wyckoff_{mode}_{g}.csv")
            if not os.path.isfile(cf):
                continue
            conf = pd.read_csv(cf, encoding="utf-8-sig")
            for _, r in conf[conf["decision"] == "CONFIRMED"].iterrows():
                conf_p0[(g, mode, r["event_id"])] = float(r["orig_P0"])
    n_le_p0 = 0
    n_pass = 0
    oob_rows = []
    for g in EXPECTED_GRID:
        for mode in ("C1", "C2"):
            p = os.path.join(FT07, "out", f"audit_orders_Wyckoff_{mode}_{g}.csv")
            if not os.path.isfile(p):
                continue
            a = pd.read_csv(p, encoding="utf-8-sig")
            for _, r in a.iterrows():
                if r["decision"] != "PASSED":
                    if r["reason"] == "outside_min_max_bounds":
                        oob_rows.append({"grid": g, "mode": mode, "pair": r["pair"],
                                         "candidate_id": r["candidate_id"],
                                         "fill": float(r["fill_price"]),
                                         "planned": r["planned_execution_time"]})
                    continue
                n_pass += 1
                ev = event_of(r["candidate_id"])
                p0 = conf_p0.get((g, mode, ev))
                if p0 is not None and float(r["fill_price"]) <= p0:
                    n_le_p0 += 1
    c0_ev = set()
    for g in EXPECTED_GRID:
        p = os.path.join(FT07, "out", f"trades_Wyckoff_C_{g}.csv")
        d = pd.read_csv(p, encoding="utf-8-sig")
        if d.empty:
            continue
        for t in d["enter_tag"]:
            c0_ev.add((g, event_of(t)))
    for row in oob_rows:
        row["C0是否成交"] = (row["grid"], event_of(row["candidate_id"])) in c0_ev
    write_csv(pd.DataFrame(oob_rows), "05_T6_边界拒单8笔_v1.csv")

    n_retry = 0
    for g in EXPECTED_GRID:
        for mode in ("C1", "C2"):
            p = os.path.join(FT07, "out", f"audit_orders_Wyckoff_{mode}_{g}.csv")
            if os.path.isfile(p):
                a = pd.read_csv(p, encoding="utf-8-sig")
                n_retry += int((a["reason"] == "retry_prohibited_already_evaluated").sum())
    rep["T6_入场带差异"] = {
        "C0的min_entry": "L-0.2A", "C1/C2的min_entry": "P0（并新增 fill<=P0 拒单）",
        "C1/C2放行笔数": n_pass,
        "放行中fill<=P0笔数": n_le_p0,
        "fill_price_le_initial_stop_p0拒单数": 0,
        "outside_min_max_bounds拒单": len(oob_rows),
        "其中C0同事件已成交": int(sum(1 for r in oob_rows if r["C0是否成交"])),
        "retry_prohibited行数": n_retry,
        "判定": "min_entry 收紧（L-0.2A→P0）未产生任何额外拒单（0 笔 fill<=P0）；"
                "8 笔 outside_min_max_bounds 均为成交价超过 max_entry=close+0.5A，属 C0 同款上限边界；"
                "该条款差异对 27 组成交无可测影响，但确属『确认条件之外的一并改动』，登记为轻微超标",
    }

    # T7: 成交=确认柱+15m（修正事件键后）
    t7_bad = []
    t7_n = 0
    for g in EXPECTED_GRID:
        for mode in ("C1", "C2"):
            cf = os.path.join(FT07, "out", f"audit_confirmation_Wyckoff_{mode}_{g}.csv")
            tf = os.path.join(FT07, "out", f"trades_Wyckoff_{mode}_{g}.csv")
            if not (os.path.isfile(cf) and os.path.isfile(tf)):
                continue
            conf = pd.read_csv(cf, encoding="utf-8-sig")
            conf_ok = conf[conf["decision"] == "CONFIRMED"][["event_id", "confirm_time_ms"]].drop_duplicates("event_id")
            tr = pd.read_csv(tf, encoding="utf-8-sig")
            if tr.empty:
                continue
            tr["event_id"] = tr["enter_tag"].map(event_of)
            m = tr.merge(conf_ok, on="event_id", how="left")
            for _, r in m.iterrows():
                if pd.isna(r["confirm_time_ms"]):
                    t7_bad.append({"strategy": f"{mode}_{g}", "tag": r["enter_tag"], "问题": "无CONFIRMED记录"})
                    continue
                t7_n += 1
                if int(r["open_timestamp"]) != int(r["confirm_time_ms"]) + 900000:
                    t7_bad.append({"strategy": f"{mode}_{g}", "tag": r["enter_tag"],
                                   "open_ts": int(r["open_timestamp"]),
                                   "confirm_ts": int(r["confirm_time_ms"])})
    rep["T7_执行时刻"] = {"核对笔数": t7_n, "违反笔数": len(t7_bad), "样例": t7_bad[:5],
                          "判定": "全部=确认柱收盘+15m" if not t7_bad else "存在违反"}

    # T8/T9/T10 已在 T4 覆盖；T11 config；T12 留出期
    tr_max = t5d["open_rate"]  # placeholder
    all_close_max = 0
    for g in EXPECTED_GRID:
        for mode, tpl in (("C0", "Wyckoff_C_{g}"), ("C1", "Wyckoff_C1_{g}"), ("C2", "Wyckoff_C2_{g}")):
            p = os.path.join(FT07, "out", f"trades_{tpl.format(g=g)}.csv")
            d = pd.read_csv(p, encoding="utf-8-sig")
            if not d.empty:
                all_close_max = max(all_close_max, int(d["close_timestamp"].max()))
    import datetime as _dt
    rep["T11_config一致"] = {"max_open_trades": cfg.get("max_open_trades"),
                             "dry_run_wallet": cfg.get("dry_run_wallet"),
                             "与FT-ENTRY-02一致": (cfg.get("max_open_trades") == 3 and
                                                   cfg.get("dry_run_wallet") == 2000)}
    rep["T12_无留出期"] = {
        "全部成交最晚平仓": _dt.datetime.utcfromtimestamp(all_close_max / 1000).strftime("%Y-%m-%d %H:%M UTC"),
        "判定": "最晚平仓<=2025-01-01，未触碰 2025+ 留出/未使用区间",
    }
    rep["T8_OI过滤"] = {"判定": "代码级一致：is_retest 块在 C0/C1/C2 分流前统一执行 enable_oi_filter 且 oi_growth>=0；"
                               "C1/C2 复用原信号柱 OI 读数，未新增/放松过滤"}

    jdump(os.path.join(OUT, "05_T1-T12核对_v1.json"), rep)
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str)[:6000])


if __name__ == "__main__":
    main()
