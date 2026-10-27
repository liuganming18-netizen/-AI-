#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PROJECT-REVIEW-07 · 复核「4h 跟踪止损是否使用入场前价格」（只读）

判据（机械可复算）：
  1. 一笔成交的入场时刻 entry_ts；入场后**第一根收盘的 4h** 收盘时刻为
     first4h_close = floor(entry_ts/4h)*4h + 4h - 1ms（4h 收盘是 :59:59.999）。
  2. 该笔 stop_trace 里第一条 reason=="trail" 的时刻 first_trail_ts。
  3. 若 first_trail_ts < first4h_close ⇒ 这条跟踪线来自**入场之前**已收盘的 4h bar，
     即「使用入场前价格」。反之合规。
只读 trades.jsonl，不改任何文件。
"""
import json, os, glob
from collections import Counter, defaultdict

ROOT = "/Users/boos/Desktop/威科夫因子共振趋势"
H4 = 4 * 3600 * 1000
OUT = os.path.join(ROOT, "evidence/PROJECT-REVIEW-07/out/07_4h止损入场前价格复核.json")


def first_4h_close_after(ts_ms: int) -> int:
    # 4h bar 起点对齐 UTC 00/04/08/12/16/20；收盘 = 起点 + 4h - 1ms
    open_ms = (ts_ms // H4) * H4
    close_ms = open_ms + H4 - 1
    if close_ms < ts_ms:                      # 入场落在收盘之后（不可能，防御）
        close_ms += H4
    return close_ms


def main():
    files = sorted(glob.glob(os.path.join(ROOT, "回测日志/**/trades.jsonl"), recursive=True))
    stat = Counter()
    bad = []
    per_batch = defaultdict(lambda: Counter())
    for p in files:
        batch = os.sep.join(os.path.relpath(p, ROOT).split(os.sep)[:3])
        try:
            rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
        except Exception:
            stat["文件解析失败"] += 1
            continue
        for r in rows:
            stat["成交总数"] += 1
            st = r.get("stop_trace") or []
            entry = r.get("entry_time") or r.get("entry_time_ms")
            if not st or not isinstance(entry, int):
                stat["无止损轨迹或无数值入场时刻"] += 1
                continue
            trails = [x for x in st if str(x.get("reason")) == "trail" and isinstance(x.get("ts"), int)]
            if not trails:
                stat["从未上移过跟踪线"] += 1
                continue
            stat["有跟踪上移"] += 1
            f4 = first_4h_close_after(int(entry))
            ft = min(int(x["ts"]) for x in trails)
            if ft < f4:
                stat["跟踪线上移早于入场后首个4h收盘（用了入场前价格）"] += 1
                per_batch[batch]["疑似"] += 1
                if len(bad) < 20:
                    bad.append({
                        "file": os.path.relpath(p, ROOT),
                        "symbol": r.get("symbol"), "entry_time": int(entry),
                        "first_trail_ts": ft, "first_4h_close_after_entry": f4,
                        "gap_hours": round((f4 - ft) / 3600000, 3),
                        "entry_price": r.get("entry_price"),
                        "initial_stop": st[0].get("stop") if st else None,
                        "first_trail_stop": min(trails, key=lambda x: int(x["ts"])).get("stop"),
                        "exit_reason": r.get("exit_reason"),
                    })
            else:
                stat["合规（首条跟踪线来自入场后已收盘4h）"] += 1
    res = {"审计时刻_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
           "扫描文件数": len(files), "统计": dict(stat),
           "按批次疑似计数": {k: dict(v) for k, v in per_batch.items()},
           "疑似样例": bad,
           "判据": "first_trail_ts < (入场后第一根已收盘4h的收盘时刻) ⇒ 用了入场前价格"}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(res["统计"], ensure_ascii=False, indent=1))
    print("按批次:", json.dumps(res["按批次疑似计数"], ensure_ascii=False))
    print("→", OUT)


if __name__ == "__main__":
    main()
