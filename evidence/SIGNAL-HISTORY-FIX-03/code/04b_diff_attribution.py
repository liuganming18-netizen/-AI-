# -*- coding: utf-8 -*-
"""SIGNAL-HISTORY-FIX-03｜04b 三场景差异归因（只读 out/04_隔离一致性.json）。

把 04 的三组对照拆开看：
  · feed_path_*   —— 同窗口、只换喂法；若有差异，说明「追加」与「一次喂完」不等价；
  · window_shift_* —— 只换窗口起点；差异才是「移动历史窗口」的真实影响。
并对 window_shift 的差异做**成因归类**：箱体建档时刻差 = 多少小时、是否落在
`box_gate_bars >= atr_warmup_bars` 的闸门开闸点上。
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "out"


def u(ms):
    return (dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%m-%dT%H:%M")
            if ms else None)


def report(res: dict, key: str, label: str) -> dict:
    d = res["scenarios"].get(key) or {}
    print(f"\n=== {label} ({key}) ===")
    print(f"same {d.get('same')}/{d.get('n')}  diff {d.get('diff_n')}  missing {d.get('missing_n')}")
    if not d.get("diff_n"):
        print("  无差异")
        return {"same": d.get("same"), "n": d.get("n"), "diff_n": 0}
    keys = Counter(tuple(x["diff_keys"]) for x in d["diffs"])
    print("  diff_keys 分布:", keys.most_common())
    hours = Counter()
    for x in d["diffs"]:
        a = list(v for k, v in x.items() if k.startswith("S1_") or k == "S2")[0]
        b = x["S3"] if "S3" in x else x["S2"]
        if a.get("box_formed_ms") and b.get("box_formed_ms"):
            hours[round((a["box_formed_ms"] - b["box_formed_ms"]) / 3600000, 1)] += 1
        else:
            hours["一侧无箱体"] += 1
    print("  箱体建档时刻差(小时, 前者−后者):", hours.most_common(12))
    for x in d["diffs"]:
        a = list(v for k, v in x.items() if k.startswith("S1_") or k == "S2")[0]
        b = x["S3"] if "S3" in x else x["S2"]
        print(f"  {x['symbol']:16s} {str(x['diff_keys']):46s} "
              f"A {a['state']:8s} {u(a['box_formed_ms'])} | B {b['state']:8s} {u(b['box_formed_ms'])}")
    return {"same": d.get("same"), "n": d.get("n"), "diff_n": d.get("diff_n")}


def main():
    res = json.loads((OUT / "04_隔离一致性.json").read_text(encoding="utf-8"))
    print("now", res["now_utc"], "| S2 窗口起点", res.get("s2_window_start_utc"),
          "| S3 窗口起点", res.get("s3_window_start_utc"))
    print("secs", {k: res["scenarios"][k] for k in res["scenarios"] if k.endswith("_sec")})
    print("S2_used", res["scenarios"].get("S2_used"))
    print("S3_used", res["scenarios"].get("S3_used"))
    out = {}
    out["feed_f2"] = report(res, "feed_path_S1f2_vs_S2", "喂法等价（窗口 f2）")
    out["feed_f3"] = report(res, "feed_path_S1f3_vs_S3", "喂法等价（窗口 f3）")
    out["window"] = report(res, "window_shift_S2_vs_S3", "只换窗口起点（差 60h）")
    c = res["cadence_1h"]
    print("\n=== 1h 节奏 ===")
    print(f"sample={c['sample_n']} bars={c['bars_checked']} processed={c['bars_processed']} "
          f"unprocessed={c['bars_unprocessed']} median_lag={c['median_of_symbol_median_lag_min']}min "
          f"max_lag={c['max_lag_min']}min gt60={c['n_symbols_with_lag_gt_60min']} "
          f"gt240={c['n_symbols_with_lag_gt_240min']}")
    lag_hist = Counter(v["lag_min_median"] for v in c["per_symbol"].values())
    print("逐币中位延迟分布:", sorted(lag_hist.items()))
    worst = sorted(c["per_symbol"].items(), key=lambda kv: -(kv[1]["lag_min_max"] or 0))[:5]
    print("最大延迟前 5:", [(k, v["lag_min_max"]) for k, v in worst])
    (OUT / "04b_归因摘要.json").write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                            encoding="utf-8")


if __name__ == "__main__":
    main()
