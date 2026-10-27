#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记录公开扩展快照的**每一轮**推进，作为「跨采集周期时间推进」的证据。

每轮追加一行到 `evidence/DATA-UI-01/轮次推进.jsonl`，字段：
  observed_at            本脚本观察到的时刻（UTC）
  snapshot_updated_at    快照索引自己写的时刻（UTC）
  ratio_extra_round      轮次号
  ratio_extra_window     本轮窗口
  ratio_extra_done       本轮**真取齐**的标的数
  ratio_extra_incomplete 本轮没取齐的标的（下一轮优先重试）
  requests               上游/缓存/节流/失败计数
  btc_blocks             BTC 三个额外比率块各自的 available 与 fetched_at
  btc_volume             BTC 未收盘 1h K 线的量与时点（证明实时层在推进）

用法：
  python track_rounds.py            # 采一次
  python track_rounds.py --watch 30 # 每 30s 采一次，直到 Ctrl-C
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
EVID = os.path.abspath(os.path.join(HERE, ".."))
ROOT = os.path.abspath(os.path.join(EVID, "..", ".."))
SNAP = os.path.join(ROOT, "框架", "backend", "快照", "public_extras")
OUT = os.path.join(EVID, "轮次推进.jsonl")
BLOCKS = ("top_position_long_short_ratio", "top_account_long_short_ratio",
          "global_account_long_short_ratio", "taker_buy_sell_ratio")


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sample():
    with open(os.path.join(SNAP, "_index.json"), encoding="utf-8") as fh:
        idx = json.load(fh)
    btc = {}
    p = os.path.join(SNAP, "binance__BTCUSDT.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as fh:
            snap = json.load(fh)
        ls = snap.get("long_short") or {}
        for b in BLOCKS:
            blk = ls.get(b) or {}
            btc[b] = {"available": bool(blk.get("available")),
                      "value": blk.get("value"),
                      "fetched_at": blk.get("fetched_at"),
                      "carried_over": bool(blk.get("carried_over")),
                      "reason": (blk.get("reason") or "")[:80] or None}
        btc["_refreshed_at"] = snap.get("snapshot_refreshed_at")
    return {
        "observed_at": now_iso(),
        "snapshot_updated_at": idx.get("updated_at"),
        "ratio_extra_round": idx.get("ratio_extra_round"),
        "ratio_extra_window": idx.get("ratio_extra_window"),
        "ratio_extra_done": idx.get("ratio_extra_done"),
        "ratio_extra_incomplete": idx.get("ratio_extra_incomplete"),
        "ratio_extra_pending_in": idx.get("ratio_extra_pending_in"),
        "requests": idx.get("requests"),
        "elapsed_s": idx.get("elapsed_s"),
        "btc_blocks": btc,
    }


def main():
    watch = 0
    if "--watch" in sys.argv:
        watch = int(sys.argv[sys.argv.index("--watch") + 1])
    seen = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as fh:
            for line in fh:
                try:
                    seen.add(json.loads(line).get("snapshot_updated_at"))
                except Exception:                                 # noqa: BLE001
                    pass
    n = 0
    while True:
        try:
            row = sample()
            if row["snapshot_updated_at"] not in seen:
                seen.add(row["snapshot_updated_at"])
                with open(OUT, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                n += 1
                print(f"[{row['observed_at']}] 记录轮次 "
                      f"{row['ratio_extra_round']}（快照 {row['snapshot_updated_at']}，"
                      f"取齐 {row['ratio_extra_done']}，"
                      f"未齐 {row['ratio_extra_incomplete']}）")
            else:
                print(f"[{row['observed_at']}] 无新轮次")
        except Exception as exc:                                  # noqa: BLE001
            print(f"采样失败：{type(exc).__name__}: {exc}")
        if not watch:
            break
        time.sleep(watch)
    print(f"新增 {n} 条，累计文件：{OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
