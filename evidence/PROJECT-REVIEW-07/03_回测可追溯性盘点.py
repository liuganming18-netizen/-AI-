#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PROJECT-REVIEW-07 · 回测运行目录可追溯性盘点（只读）

目的：回答「回测K线、运行身份、订单及成交能否重开追溯」。
只读 回测日志/ 下每个 run 目录，统计：
  1. 九个标准文件是否存在（candidates/chart/equity_curve/inputs/manifest/orders/run/summary/trades）
  2. chart.json 是否符合官方契约（candles 为对象、trades[].trade_id 非空、signals 非空）
  3. inputs.json 是否带运行身份（rule / 参数哈希 / 数据范围 / 代码版本）
  4. orders.jsonl / trades.jsonl 行数与 order_id 是否为合成键
不修改任何文件，不写上游目录。
"""
import json, os, sys, hashlib
from pathlib import Path

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
BT = ROOT / "回测日志"
STD = ["candidates.jsonl", "chart.json", "equity_curve.jsonl", "inputs.json",
       "manifest.json", "orders.jsonl", "run.json", "summary.json", "trades.jsonl"]

def jload(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"__error__": f"{type(e).__name__}: {e}"}

def jlines(p, cap=None):
    n = 0
    rows = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                n += 1
                if cap is None or len(rows) < cap:
                    try:
                        rows.append(json.loads(line))
                    except Exception:
                        pass
    except Exception:
        return -1, []
    return n, rows

def audit_run(d: Path):
    rec = {"run": str(d.relative_to(ROOT)), "files": {}, "missing": []}
    for f in STD:
        p = d / f
        rec["files"][f] = p.exists()
        if not p.exists():
            rec["missing"].append(f)
    # chart 契约
    chart = jload(d / "chart.json") if (d / "chart.json").exists() else {}
    if "__error__" in chart:
        rec["chart"] = {"error": chart["__error__"]}
    else:
        candles = chart.get("candles") or []
        trades = chart.get("trades") or []
        sigs = chart.get("signals") or []
        c0 = candles[0] if candles else None
        rec["chart"] = {
            "candle_n": len(candles),
            "candle_is_object": isinstance(c0, dict),
            "candle_keys": sorted(c0.keys()) if isinstance(c0, dict) else None,
            "signal_n": len(sigs),
            "trade_n": len(trades),
            "trade_id_null_n": sum(1 for t in trades if not t.get("trade_id")),
            "stop_trace_key": (sorted((trades[0].get("stop_trace") or [{}])[0].keys())
                               if trades and (trades[0].get("stop_trace") or []) else None),
            "funding_events_n": len(chart.get("funding_events") or []),
        }
    # 身份
    inp = jload(d / "inputs.json") if (d / "inputs.json").exists() else {}
    keys = list(inp.keys()) if isinstance(inp, dict) else []
    ident = {}
    for k in keys:
        v = inp[k]
        if isinstance(v, (str, int, float, bool)) or v is None:
            if any(s in k.lower() for s in ("rule", "hash", "version", "sha", "symbol", "start", "end", "equity", "seed", "id")):
                ident[k] = v
    rec["inputs_keys"] = keys
    rec["identity"] = ident
    # 订单/成交
    on, orows = jlines(d / "orders.jsonl", cap=3)
    tn, trows = jlines(d / "trades.jsonl", cap=3)
    rec["orders_n"] = on
    rec["trades_n"] = tn
    rec["order_sample_keys"] = sorted(orows[0].keys()) if orows else None
    rec["trade_sample_keys"] = sorted(trows[0].keys()) if trows else None
    rec["order_id_sample"] = [o.get("order_id") for o in orows][:3]
    rec["created_at_sample"] = [o.get("created_at") for o in orows][:3]
    return rec

def main():
    runs = []
    for dp, dn, fn in os.walk(BT):
        if "chart.json" in fn or "inputs.json" in fn:
            if "candidates.jsonl" in fn or "run.json" in fn:
                runs.append(Path(dp))
    runs = sorted(set(runs))
    out = {"审计时刻_utc": __import__("datetime").datetime.utcnow().isoformat() + "Z",
           "run_目录数": len(runs), "记录": []}
    for d in runs:
        try:
            out["记录"].append(audit_run(d))
        except Exception as e:
            out["记录"].append({"run": str(d.relative_to(ROOT)), "error": f"{type(e).__name__}: {e}"})
    # 汇总
    n = len(out["记录"])
    def cnt(pred):
        return sum(1 for r in out["记录"] if pred(r))
    out["汇总"] = {
        "run_目录数": n,
        "九件套齐全": cnt(lambda r: not r.get("missing")),
        "chart_缺失": cnt(lambda r: "chart.json" in (r.get("missing") or [])),
        "inputs_缺失": cnt(lambda r: "inputs.json" in (r.get("missing") or [])),
        "candles_非对象契约": cnt(lambda r: (r.get("chart") or {}).get("candle_is_object") is False),
        "signals_为空": cnt(lambda r: (r.get("chart") or {}).get("signal_n") == 0),
        "trade_id_有空值": cnt(lambda r: ((r.get("chart") or {}).get("trade_id_null_n") or 0) > 0),
        "有成交但chart无成交": cnt(lambda r: (r.get("trades_n") or 0) > 0 and ((r.get("chart") or {}).get("trade_n") or 0) == 0),
    }
    dst = ROOT / "evidence/PROJECT-REVIEW-07/out/03_回测可追溯性.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(json.dumps(out["汇总"], ensure_ascii=False, indent=1))
    print("→", dst)

if __name__ == "__main__":
    main()
