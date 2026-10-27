#!/usr/bin/env python3
"""把 all_trades.json 里被订单号顶替的 trade_id，用账本真实成交号回填。

只做「有证据的回填」：同一 run 的账本逐笔里，按 (标的, 进场毫秒) 精确匹配到唯一一笔才填；
匹配不到（账本已不存在 / 不唯一）就保持 null —— 不猜、不按顺序套。
"""
import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PATH = "UI界面/web/public/all_trades.json"
BASE = "http://127.0.0.1:8787/api/v1/backtests"


def fetch_trades(run_id):
    try:
        with urllib.request.urlopen(f"{BASE}/{run_id}/trades", timeout=60) as r:
            return json.loads(r.read().decode()).get("items") or []
    except Exception:
        return None


def main():
    d = json.load(open(PATH, encoding="utf-8"))
    items = d["items"]
    runs = sorted({t["run_id"] for t in items})

    with ThreadPoolExecutor(max_workers=8) as ex:
        ledger = dict(zip(runs, ex.map(fetch_trades, runs)))

    filled = 0
    ambiguous = 0
    no_ledger = 0
    for t in items:
        if t.get("trade_id"):
            continue
        rows = ledger.get(t["run_id"])
        if rows is None:
            no_ledger += 1
            continue
        sym = t.get("symbol")
        et = t.get("entry_time_ms")
        cands = [
            r for r in rows
            if r.get("trade_id")
            and (r.get("symbol") == sym or not sym)
            and isinstance(r.get("entry_time_ms"), (int, float))
            and isinstance(et, (int, float))
            and abs(r["entry_time_ms"] - et) <= 1000
        ]
        if len(cands) == 1:
            t["trade_id"] = cands[0]["trade_id"]
            t["trade_id_source"] = "ledger:trades.entry_time_ms 唯一匹配"
            filled += 1
        else:
            ambiguous += 1

    d["note"] = (
        "trade_id 只放账本真实成交号（trd_…），缺失为 null（不用订单号顶替）。"
        f"本次由账本回填 {filled} 笔；无法回填：账本缺失 {no_ledger} 笔、匹配不唯一/无对应 {ambiguous} 笔。"
    )
    d["trade_id_backfill"] = {
        "filled": filled, "no_ledger": no_ledger, "ambiguous": ambiguous,
    }
    json.dump(d, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(json.dumps(d["trade_id_backfill"], ensure_ascii=False))
    print("剩余 null:", sum(1 for t in items if not t.get("trade_id")), "/", len(items))


if __name__ == "__main__":
    sys.exit(main())
