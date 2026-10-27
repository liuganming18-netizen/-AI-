# -*- coding: utf-8 -*-
"""FT-REPLAY-AUDIT-01 / 13：固定抽样 + 源账本→系统逐笔核对（v2）。

抽样规则（事前写死，不用随机数，可复现）：
  每个模式（C / C1 / C2）各取 3 笔：
    ① 首笔盈利  ② 首笔亏损  ③ 首笔跨日（UTC 日期不同）
  三者币种互不相同；不足时放宽币种约束并记录。
  合计 9 笔 ≥ 工单要求的「各至少 2 笔」。

核对链路：源账本 CSV → 组级 trades.jsonl → 币级 trades.jsonl → 单 run API → K 线标记
逐字段比：入场时间 / 出场时间 / 入场价 / 出场价 / 数量 / 净额 / P0。
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
E09 = os.path.join(ROOT, "evidence/FT-ENTRY-09")
AUDIT = os.path.join(ROOT, "evidence/FT-REPLAY-AUDIT-01")
OUT = os.path.join(AUDIT, "out")
BT = os.path.join(ROOT, "回测日志/event_trend_v0_1")
GRID_DIR = os.path.join(BT, "runs_ft_entry_09_research")
SYM_DIR = os.path.join(BT, "runs_ft_replay_01")
BASE = "http://127.0.0.1:8787/api/v1/backtests"
GRIDS = ["Vs04_Z02", "Vs04_Z04", "Vs04_Z06", "Vs06_Z02", "Vs06_Z04",
         "Vs06_Z06", "Vs08_Z02", "Vs08_Z04", "Vs08_Z06"]
MODES = ["C", "C1", "C2"]


def get(path, timeout=90):
    p = subprocess.run(["curl", "-s", "-m", str(timeout), BASE + path],
                       capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"__error__": "非 JSON", "raw": (p.stdout or "")[:200]}


def read_jsonl(p):
    out = []
    if not os.path.isfile(p):
        return out
    with open(p, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def load_source():
    parts = []
    for mode in MODES:
        for g in GRIDS:
            st = "Wyckoff_%s_%s" % (mode, g)
            p = os.path.join(E09, "out", "trades_%s.csv" % st)
            if not os.path.isfile(p):
                continue
            d = pd.read_csv(p, encoding="utf-8-sig", low_memory=False)
            if d.empty:
                continue
            d["strategy"] = st
            d["mode"] = mode
            d["grid"] = g
            parts.append(d)
    src = pd.concat(parts, ignore_index=True)
    src["symbol"] = (src["pair"].astype(str).str.split("/").str[0]
                    + src["pair"].astype(str).str.split("/").str[1].str.split(":").str[0])
    src["open_ms"] = pd.to_numeric(src["open_timestamp"], errors="coerce").astype("int64")
    src["close_ms"] = pd.to_numeric(src["close_timestamp"], errors="coerce").astype("int64")
    src["net"] = pd.to_numeric(src["profit_abs"], errors="coerce")
    src["event_id"] = src["enter_tag"].astype(str).str.extract(
        r"(ev_FT_ENTRY_02_binance_[A-Z0-9]+_\d+)")[0]
    return src


def load_confirm():
    rows = []
    for mode in ("C1", "C2"):
        for g in GRIDS:
            st = "Wyckoff_%s_%s" % (mode, g)
            p = os.path.join(E09, "out", "audit_confirmation_%s.csv" % st)
            if not os.path.isfile(p):
                continue
            d = pd.read_csv(p, encoding="utf-8-sig", low_memory=False)
            d["strategy"] = st
            rows.append(d)
    return pd.concat(rows, ignore_index=True)


def pick(src):
    """固定抽样：每模式 首盈 / 首亏 / 首跨日，币种互不相同。"""
    picks = []
    used = set()   # 币种跨模式共用，尽量让 9 笔落在 9 个不同币种上
    for mode in MODES:
        d = src[src["mode"] == mode].sort_values(["open_ms", "symbol"]).reset_index(drop=True)
        chosen = []
        rules = [
            ("盈利", d[d["net"] > 0]),
            ("亏损", d[d["net"] < 0]),
            ("跨日", d[d.apply(lambda r: pd.Timestamp(r["open_ms"], unit="ms").date()
                               != pd.Timestamp(r["close_ms"], unit="ms").date(), axis=1)]),
        ]
        for tag, pool in rules:
            hit = None
            for _, r in pool.iterrows():
                if r["symbol"] not in used:
                    hit = r
                    break
            relaxed = False
            if hit is None and len(pool):
                hit = pool.iloc[0]
                relaxed = True
            if hit is None:
                continue
            used.add(hit["symbol"])
            chosen.append((tag, hit, relaxed))
        for tag, r, relaxed in chosen:
            picks.append({
                "模式": mode, "抽样类型": tag, "币种放宽": relaxed,
                "策略": r["strategy"], "参数组": r["grid"], "币种": r["symbol"],
                "event_id": r["event_id"],
                "源_入场毫秒": int(r["open_ms"]), "源_出场毫秒": int(r["close_ms"]),
                "源_入场价": float(r["open_rate"]), "源_出场价": float(r["close_rate"]),
                "源_数量": float(r["amount"]), "源_净额": float(r["net"]),
                "源_出场原因": r["exit_reason"],
            })
    return pd.DataFrame(picks)


def main():
    src = load_source()
    conf = load_confirm()
    picks = pick(src)

    # 补齐：确认表里的 P0 / 信号时间 / 确认时间
    conf_idx = {}
    for _, c in conf.iterrows():
        conf_idx[(c["strategy"], c["event_id"])] = c
    for i, r in picks.iterrows():
        c = conf_idx.get((r["策略"], r["event_id"]))
        picks.at[i, "源_P0"] = float(c["orig_P0"]) if c is not None else None
        picks.at[i, "源_信号毫秒"] = int(c["orig_signal_time_ms"]) if c is not None else None
        picks.at[i, "源_确认毫秒"] = int(c["confirm_time_ms"]) if c is not None else None

    # run_id 映射
    picks["组级run_id"] = "run_FT_ENTRY_09_" + picks["策略"]
    picks["币级run_id"] = "run_FT_ENTRY_09_" + picks["策略"].str.replace("Wyckoff_", "", regex=False) \
        + "_" + picks["币种"]

    rows = []          # 逐字段核对明细
    page_payload = []  # 交给页面脚本的期望值
    for _, r in picks.iterrows():
        # ① 组级 trades.jsonl
        gt = [t for t in read_jsonl(os.path.join(GRID_DIR, r["组级run_id"], "trades.jsonl"))
              if t.get("symbol") == r["币种"] and t.get("entry_time_ms") == r["源_入场毫秒"]]
        gt = gt[0] if gt else {}
        # ② 币级 trades.jsonl
        st = [t for t in read_jsonl(os.path.join(SYM_DIR, r["币级run_id"], "trades.jsonl"))
              if t.get("entry_time_ms") == r["源_入场毫秒"]]
        st = st[0] if st else {}
        # ③ 单 run API（页面逐笔表用的就是这条通道）
        api = get("/%s/trades" % r["币级run_id"])
        api_items = [x for x in (api.get("items") or [])
                     if x.get("entry_time_ms") == r["源_入场毫秒"]]
        api_it = api_items[0] if api_items else {}
        # ④ K 线标记
        ch = get("/%s/chart" % r["币级run_id"])
        chart = ch.get("chart") or {}
        mk = [x for x in (chart.get("trades") or [])
              if (x.get("entry") or {}).get("t") == r["源_入场毫秒"]]
        mk = mk[0] if mk else {}
        sigs = [s for s in (chart.get("signals") or []) if s.get("signal_id") == r["event_id"]]
        cfm = [c for c in (chart.get("confirms") or []) if c.get("signal_id") == r["event_id"]]

        exp = {
            "入场时间": r["源_入场毫秒"],
            "出场时间": r["源_出场毫秒"],
            "入场价": r["源_入场价"],
            "出场价": r["源_出场价"],
            "数量": r["源_数量"],
            "净额": r["源_净额"],
            "P0": r["源_P0"],
            "信号时间": r["源_信号毫秒"],
            "确认时间": r["源_确认毫秒"],
        }
        got = {
            "组级jsonl": {"入场时间": gt.get("entry_time_ms"), "出场时间": gt.get("exit_time_ms"),
                        "入场价": gt.get("entry_price"), "出场价": gt.get("exit_price"),
                        "数量": gt.get("qty"), "净额": gt.get("net_pnl"), "P0": gt.get("P0"),
                        "信号时间": gt.get("signal_time_ms"), "确认时间": gt.get("confirm_time_ms")},
            "币级jsonl": {"入场时间": st.get("entry_time_ms"), "出场时间": st.get("exit_time_ms"),
                        "入场价": st.get("entry_price"), "出场价": st.get("exit_price"),
                        "数量": st.get("qty"), "净额": st.get("net_pnl"), "P0": st.get("P0"),
                        "信号时间": st.get("signal_time_ms"), "确认时间": st.get("confirm_time_ms")},
            "单runAPI": {"入场时间": api_it.get("entry_time_ms"), "出场时间": api_it.get("exit_time_ms"),
                       "入场价": api_it.get("entry_price"), "出场价": api_it.get("exit_price"),
                       "数量": api_it.get("qty"), "净额": api_it.get("net_pnl"), "P0": api_it.get("P0"),
                       "信号时间": api_it.get("signal_time_ms"), "确认时间": api_it.get("confirm_time_ms")},
            "K线标记": {"入场时间": (mk.get("entry") or {}).get("t"),
                     "出场时间": (mk.get("exit") or {}).get("t"),
                     "入场价": (mk.get("entry") or {}).get("price"),
                     "出场价": (mk.get("exit") or {}).get("price"),
                     "数量": (mk.get("entry") or {}).get("qty"),
                     "净额": mk.get("net_pnl"),
                     "P0": (sigs[0].get("P0") if sigs else None),
                     "信号时间": (sigs[0].get("t") if sigs else None),
                     "确认时间": (cfm[0].get("t") if cfm else None)},
        }
        for stage, vals in got.items():
            for f, e in exp.items():
                a = vals.get(f)
                if e is None and a is None:
                    ok, note = True, "两侧皆空（源账本无此字段）"
                elif e is None:
                    ok, note = None, "源账本无值、系统有值"
                elif a is None:
                    ok, note = False, "系统缺值"
                else:
                    if isinstance(e, float):
                        ok = abs(float(a) - e) < 1e-9
                    else:
                        ok = int(a) == int(e)
                    note = ""
                rows.append({
                    "模式": r["模式"], "抽样类型": r["抽样类型"], "币种": r["币种"],
                    "策略": r["策略"], "环节": stage, "字段": f,
                    "源账本": e, "系统": a, "一致": ok, "说明": note,
                })

        page_payload.append({
            "模式": r["模式"], "抽样类型": r["抽样类型"], "币种": r["币种"],
            "策略": r["策略"], "event_id": r["event_id"],
            "组级run_id": r["组级run_id"], "币级run_id": r["币级run_id"],
            "期望": exp,
            "源_出场原因": r["源_出场原因"],
            "单runAPI_下单时间": api_it.get("order_time_ms"),
            "单runAPI_下单时间来源": api_it.get("order_time_source"),
            "单runAPI_trade_id": api_it.get("trade_id"),
            "单runAPI_trade_id来源": api_it.get("trade_id_source"),
        })

    det = pd.DataFrame(rows)
    det.to_csv(os.path.join(OUT, "13_逐笔核对明细_v2.csv"), index=False, encoding="utf-8-sig")
    picks.to_csv(os.path.join(OUT, "13_固定抽样_v2.csv"), index=False, encoding="utf-8-sig")
    with open(os.path.join(OUT, "13_页面期望值_v2.json"), "w", encoding="utf-8") as fh:
        json.dump(page_payload, fh, ensure_ascii=False, indent=2, default=str)

    # 汇总
    def rate(stage, field=None):
        d = det[det["环节"] == stage]
        if field:
            d = d[d["字段"] == field]
        v = d["一致"].dropna()
        return int(v.sum()), len(v)

    summary = {"抽样数": len(picks), "按模式": picks["模式"].value_counts().to_dict(),
               "盈利笔数": int((picks["源_净额"] > 0).sum()),
               "亏损笔数": int((picks["源_净额"] < 0).sum()),
               "不同币种数": int(picks["币种"].nunique()),
               "跨日笔数": int(sum(
                   pd.Timestamp(a, unit="ms").date() != pd.Timestamp(b, unit="ms").date()
                   for a, b in zip(picks["源_入场毫秒"], picks["源_出场毫秒"]))),
               "各环节一致率": {s: "%d/%d" % rate(s) for s in det["环节"].unique()},
               "按字段一致率": {f: "%d/%d" % rate("单runAPI", f)
                             for f in ["入场时间", "出场时间", "入场价", "出场价", "数量", "净额", "P0",
                                       "信号时间", "确认时间"]},
               "不一致明细数": int((det["一致"] == False).sum()),
               "两侧皆空数": int((det["一致"].isna()).sum())}
    with open(os.path.join(OUT, "13_抽样摘要_v2.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2, default=str)

    print(picks[["模式", "抽样类型", "币种", "策略", "源_入场毫秒", "源_净额", "源_P0"]].to_string(index=False))
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=1))
    bad = det[det["一致"] == False]
    if len(bad):
        print("\n不一致明细（前 30 条）：")
        print(bad.head(30).to_string(index=False))


if __name__ == "__main__":
    main()
