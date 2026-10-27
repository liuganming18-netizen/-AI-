#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PROJECT-REVIEW-07 · 回测「规则身份」盘点（只读）

问题：回测日志里每个 run 到底按哪一套退出规则跑的？
判据取自每个 run 自己的 inputs.json：
  fingerprints/config/params/{trailing_trigger_r, trailing_lookback_4h_bars,
                              max_holding_days, stoploss_s, trail_d, slippage_bps}
  fingerprints/rules/sha256
按批次聚合，识别「FT-LOW4H」与「WX_EVENT_TREND_V0_1」两套规则的实际分布。
只读，不写上游。
"""
import json, os, glob
from collections import Counter, defaultdict

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
OUT = os.path.join(ROOT, "evidence/PROJECT-REVIEW-07/out/09_回测规则身份分布.json")


def params_of(inp):
    fp = inp.get("fingerprints") or {}
    cfg = fp.get("config") or {}
    p = cfg.get("params") or {}
    rules = fp.get("rules") or {}
    return {
        "trailing_trigger_r": p.get("trailing_trigger_r"),
        "trailing_lookback_4h_bars": p.get("trailing_lookback_4h_bars"),
        "max_holding_days": p.get("max_holding_days"),
        "stoploss_s": p.get("stoploss_s"),
        "trail_d": p.get("trail_d"),
        "slippage_bps": p.get("slippage_bps"),
        "taker_fee_bps": p.get("taker_fee_bps"),
        "rules_sha256": (rules.get("sha256") or "")[:16],
        "rule_field": inp.get("rule") or p.get("rule"),
    }


def classify(p):
    tr, lb, mh = p["trailing_trigger_r"], p["trailing_lookback_4h_bars"], p["max_holding_days"]
    if tr == 0.0 and lb == 1 and mh is None:
        return "FT-LOW4H（入场即跟踪/1根/无到期）"
    if tr == 2.0 and lb == 10 and mh == 10:
        return "WX_EVENT_TREND_V0_1（2R触发/10根/10天强平）"
    if tr is None and lb is None and mh is None:
        return "未记录（inputs 缺参数）"
    return f"其它(tr={tr},lb={lb},mh={mh})"


def main():
    files = sorted(glob.glob(os.path.join(ROOT, "回测日志/**/inputs.json"), recursive=True))
    agg = defaultdict(Counter)
    detail = defaultdict(Counter)
    noparam = 0
    for p in files:
        try:
            inp = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        batch = os.sep.join(os.path.relpath(p, ROOT).split(os.sep)[:3])
        pp = params_of(inp)
        k = classify(pp)
        agg[batch][k] += 1
        detail[batch][(pp["slippage_bps"], pp["rules_sha256"], pp["rule_field"])] += 1
        if k.startswith("未记录"):
            noparam += 1
    res = {"审计时刻_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
           "inputs_文件数": len(files), "参数未记录数": noparam,
           "按批次规则分布": {k: dict(v) for k, v in sorted(agg.items())},
           "按批次(slippage,rules_sha,rule字段)": {k: {str(kk): vv for kk, vv in v.items()}
                                              for k, v in sorted(detail.items())}}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("inputs 文件数:", len(files), "｜参数未记录:", noparam)
    for k, v in sorted(agg.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"{k:64s} {dict(v)}")
    print("→", OUT)


if __name__ == "__main__":
    main()
