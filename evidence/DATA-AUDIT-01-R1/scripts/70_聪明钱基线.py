"""70 · 聪明钱 t0 基线（为「不补造历史、只做前向分析」定起点）

工单整改要求 5：聪明钱历史不足时的处理 —— **使用现存真实快照开展前向分析，不补造历史，
不用大户多空比替代**。

本脚本做两件事，都不涉及下载：
1. 全量清点 526 个快照文件的真实新鲜度（`fetched_at`），把「谁还在更新、谁已停更」分开；
2. 把当前快照**原样复制**一份到 `forward/t0/`，作为前向分析的 t0 内容锚点。

  out/70_smart_money_t0.json        全量清点结果 + t0 锚点清单（路径/字节/sha256/fetched_at）
  out/71_smart_money_freshness.csv  逐币新鲜度（全量 526）
  forward/t0/*.json                 t0 快照原样副本（只读复制，不改源文件）

只读源目录；只写本工单 evidence 目录。
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import OUT, ROOT, dump, now_ms, now_utc, pool, pct, quantiles, utc, write_csv

T0 = time.time()
STATS_AT = now_utc()
NOW = now_ms()
SRC = ROOT / "框架/backend/快照/smart_money"
# t0 锚点**一旦写下就不再覆盖**：复算时用 WX_T0_DIR 指到别处，
# 否则一次重跑就会把「t0 = 2026-10-26 那一刻的快照」变成「最新快照」，前向分析就没有起点了。
DST = pathlib.Path(os.environ.get("WX_T0_DIR") or (OUT.parent / "forward/t0"))
DST.mkdir(parents=True, exist_ok=True)
P = pool()
POOL = set(P["symbols"])


def log(m: str) -> None:
    print(f"[{time.time()-T0:6.1f}s] {m}", flush=True)


files = sorted(SRC.glob("binance__*.json"))
log(f"快照文件 {len(files)} 个")

rows, anchors = [], []
ages = []
t0_conflict = []
copied = 0
for f in files:
    sym = f.name.replace("binance__", "").replace(".json", "")
    try:
        raw = f.read_bytes()
        d = json.loads(raw)
    except Exception as exc:                                   # noqa: BLE001
        rows.append([sym, None, None, None, None, None, f"读取失败：{type(exc).__name__}"])
        continue
    fa_ms = d.get("fetched_at_ms")
    if fa_ms is None and d.get("fetched_at"):
        try:
            import datetime as dt
            fa_ms = int(dt.datetime.strptime(d["fetched_at"], "%Y-%m-%dT%H:%M:%SZ")
                        .replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        except Exception:                                      # noqa: BLE001
            fa_ms = None
    age = (NOW - fa_ms) / 3_600_000.0 if fa_ms else None
    if age is not None:
        ages.append(age)
    n_details = d.get("details_total")
    if n_details is None:
        n_details = len(d.get("details") or [])
    rows.append([sym, 1 if sym in POOL else 0, utc(fa_ms),
                 None if age is None else round(age, 2),
                 n_details, 1 if d.get("available") else 0,
                 (d.get("cohort_definition") or "")[:40]])
    anchors.append({"symbol": sym, "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "fetched_at_utc": utc(fa_ms), "age_hours": None if age is None else round(age, 2)})
    # t0 锚点不可覆盖：已存在则只比对 sha，不写盘
    target = DST / f.name
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != hashlib.sha256(raw).hexdigest():
            t0_conflict.append(sym)
    else:
        shutil.copy2(f, target)        # 只读复制源文件；不改源
        copied += 1

write_csv("71_smart_money_freshness.csv",
          ["symbol", "in_approved_pool", "fetched_at_utc", "age_hours",
           "details_total", "available", "cohort_definition"], rows)

fresh_1h = sum(1 for a in ages if a <= 1)
fresh_24h = sum(1 for a in ages if a <= 24)
progress = {}
try:
    progress = json.loads((SRC / "_progress.json").read_text(encoding="utf-8"))
except Exception:                                              # noqa: BLE001
    pass

out = {
    "stats_at_utc": STATS_AT,
    "population": f"全量（{len(files)} 个快照文件逐个读 fetched_at，无抽样）",
    "source_dir": str(SRC.relative_to(ROOT)),
    "files": len(files),
    "files_in_approved_pool": sum(1 for r in rows if r[1] == 1),
    "copied_to_t0": copied,
    "t0_dir": str(DST.relative_to(OUT.parent)) if DST.is_relative_to(OUT.parent) else str(DST),
    "t0_already_existed": len(files) - copied,
    "t0_sha_conflict": t0_conflict,
    "freshness_hours": quantiles(ages),
    "refreshed_le_1h": fresh_1h,
    "refreshed_le_1h_pct": pct(fresh_1h, len(ages)),
    "refreshed_le_24h": fresh_24h,
    "refreshed_le_24h_pct": pct(fresh_24h, len(ages)),
    "stale_gt_24h": sum(1 for a in ages if a > 24),
    "progress_file": progress,
    "history": {
        "append_only_history": False,
        "note": "快照按 symbol 覆盖写同一文件，**只有当前值、没有历史**；无任何时间序列表",
    },
    "cohort_definition_distinct": sorted({(r[6] or "") for r in rows}),
    "pool_symbols_without_snapshot": sorted(POOL - {r[0] for r in rows}),
    "t0_anchors": anchors,
}
dump("70_smart_money_t0.json", out)
log(f"1 小时内更新 {fresh_1h}/{len(ages)}；24 小时内 {fresh_24h}/{len(ages)}；"
    f"停更 >24h {out['stale_gt_24h']}")
print(f"\n完成，用时 {time.time()-T0:.1f}s；统计时点 {STATS_AT}")
