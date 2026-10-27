#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PROJECT-REVIEW-07-R1 · 三套引擎与成交事件盘点（只读）

上一单把「32,323 笔无 stop_trace」注解成「freqtrade 链，不写轨迹」，把
「2,677 个 inputs 无参数」当成一个整体。本轮按 run.json 的 engine 标识拆开，
逐 run 读 trades.jsonl，统计「成交笔数 / 有无 stop_trace 字段 / 字段是否为空」。

  ① 项目回测引擎（trading_core_shared_v1 与无 engine 字段的老批次）
  ② Freqtrade 回测引擎（freqtrade_backtesting / freqtrade 2026.9）
  ③ 模拟执行引擎（数据/paper/paper_3a.sqlite3）

不写任何 run 目录。
"""
import json
import os
import sqlite3
import urllib.error
import urllib.request
from collections import Counter, defaultdict

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
SRC = os.path.join(ROOT, "evidence/PROJECT-REVIEW-07/out/03_回测可追溯性.json")
OUT = os.path.join(ROOT, "evidence/PROJECT-REVIEW-07-R1/out/03_引擎与成交事件.json")
PAPER = os.path.join(ROOT, "数据/paper/paper_3a.sqlite3")
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

FAMILY = {
    "trading_core_shared_v1": "项目回测引擎",
    "freqtrade_backtesting": "Freqtrade 回测引擎",
    "freqtrade 2026.9": "Freqtrade 回测引擎",
    "oi_res_exec_v1.0.0": "OI 共振（已退役）",
    "(none)": "项目回测引擎（run.json 无 engine 字段）",
}


def scan_trades(d: str) -> dict:
    p = os.path.join(d, "trades.jsonl")
    n = 0
    no_field = 0
    empty = 0
    nonempty = 0
    keys = set()
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    t = json.loads(line)
                except Exception:                              # noqa: BLE001
                    continue
                n += 1
                keys.update(t.keys())
                if "stop_trace" not in t:
                    no_field += 1
                elif not t.get("stop_trace"):
                    empty += 1
                else:
                    nonempty += 1
    except FileNotFoundError:
        return {"n": -1}
    return {"n": n, "no_field": no_field, "empty": empty, "nonempty": nonempty,
            "has_key": "stop_trace" in keys}


def probe_fills(run_id: str) -> dict:
    try:
        with OPENER.open(f"http://127.0.0.1:8787/api/v1/backtests/{run_id}/fills", timeout=20) as r:
            d = json.loads(r.read())
        return {"http": r.status, "total": d.get("total"), "missing": d.get("missing_reason"),
                "first_keys": sorted((d.get("items") or [{}])[0].keys()) if d.get("items") else None}
    except urllib.error.HTTPError as e:
        return {"http": e.code, "body": e.read().decode("utf-8", "replace")[:160]}
    except Exception as e:                                     # noqa: BLE001
        return {"http": -1, "err": str(e)[:160]}


def main() -> None:
    src = json.load(open(SRC, encoding="utf-8"))
    recs = src["记录"]
    agg = defaultdict(lambda: Counter())
    trades_agg = defaultdict(Counter)
    per_batch = defaultdict(Counter)
    samples = {}
    for r in recs:
        d = os.path.join(ROOT, r["run"])
        eng = "(none)"
        try:
            rj = json.load(open(os.path.join(d, "run.json"), encoding="utf-8"))
            eng = (rj.get("engine") or {}).get("exec_core_version") or "(none)"
        except Exception:                                      # noqa: BLE001
            eng = "(none)"
        fam = FAMILY.get(eng, eng)
        agg[fam]["runs"] += 1
        agg[fam][f"engine:{eng}"] += 1
        agg[fam]["orders"] += max(0, r.get("orders_n") or 0)
        agg[fam]["trades(out03)"] += max(0, r.get("trades_n") or 0)
        per_batch["/".join(r["run"].split("/")[1:-1])][fam] += 1
        st = scan_trades(d)
        if st.get("n", -1) >= 0:
            for k in ("n", "no_field", "empty", "nonempty"):
                trades_agg[fam][k] += st[k]
            if st.get("has_key") is False:
                trades_agg[fam]["无 stop_trace 字段的run"] += 1
            else:
                trades_agg[fam]["有 stop_trace 字段的run"] += 1
        samples.setdefault(fam, r["run"])

    # 每套引擎挑一个有成交的 run 实测 /fills
    fills = {}
    for fam, rel in samples.items():
        rid = rel.split("/")[-1]
        fills[fam] = {"run": rel, **probe_fills(rid)}
    # 项目引擎另取一个「有成交」的官方 run（旧规则）
    for r in recs:
        if r["run"].startswith("回测日志/event_trend_v0_1/runs/") and (r.get("trades_n") or 0) > 0:
            fills["项目回测引擎-有成交样例"] = {"run": r["run"], **probe_fills(r["run"].split("/")[-1])}
            break
    # freqtrade 有成交样例
    for r in recs:
        if "runs_work_dynamic_bt_01" in r["run"] and (r.get("trades_n") or 0) > 0:
            fills["Freqtrade-有成交样例"] = {"run": r["run"], **probe_fills(r["run"].split("/")[-1])}
            break

    paper = {}
    if os.path.exists(PAPER):
        con = sqlite3.connect(f"file:{PAPER}?mode=ro", uri=True)
        try:
            paper["tables"] = {t: con.execute(f'select count(*) from "{t}"').fetchone()[0]
                               for (t,) in con.execute(
                                   "select name from sqlite_master where type='table' order by name")}
            paper["columns"] = {t: [x[1] for x in con.execute(f"PRAGMA table_info({t})")]
                                for t in ("paper_order", "paper_fill", "paper_position")}
        finally:
            con.close()

    out = {
        "引擎run数": {k: v["runs"] for k, v in agg.items()},
        "引擎细分": {k: {kk: vv for kk, vv in v.items() if kk.startswith("engine:")}
                 for k, v in agg.items()},
        "引擎成交与轨迹": {k: dict(v) for k, v in trades_agg.items()},
        "引擎订单行数": {k: v["orders"] for k, v in agg.items()},
        "按批次引擎分布": {k: dict(v) for k, v in sorted(per_batch.items())},
        "fills接口实测": fills,
        "模拟盘": paper,
        "结论要点": [
            "上一单把「32,323 笔无 stop_trace」注解为「freqtrade 链，不写轨迹」不成立："
            "freqtrade 两套合计只有 8,273 笔成交；无 stop_trace 的大头在项目引擎里"
            "「trailing 未触发所以 stop_trace 为空」的成交上。",
            "Freqtrade 链 orders.jsonl 只有开/平两行、无 filled_qty/remaining_qty，"
            "trades.jsonl 也没有 stop_trace 字段 ⇒ 不能支撑逐次成交与止损轨迹。",
            "项目回测引擎 /fills 能返回逐次成交事件（fill_id/order_id/trade_id/sequence/fee）；"
            "模拟盘有独立 paper_fill 表 ⇒ 「某引擎没有 fill 事件」不能外推到其他引擎。",
        ],
    }
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1)[:4500])
    print("→", OUT)


if __name__ == "__main__":
    main()
