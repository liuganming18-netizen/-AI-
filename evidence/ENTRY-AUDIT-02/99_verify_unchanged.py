# -*- coding: utf-8 -*-
"""ENTRY-AUDIT-02 / 99 · 收尾自检：输入是否被本轮改动

两级判定：
1. 整文件 sha256：上游研究产物、策略代码、行情**归档文件**（2021.parquet 等）
   必须一位不差；任何变化即判失败。
2. 历史切片指纹：`live.parquet` 会被采集器持续追加，整文件哈希必然变化。
   因此按「open_time <= 冻结时最大 open_time」重算行内容指纹，
   证明历史区间未被改写、只是尾部追加。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path("/Users/boos/Desktop/威科夫因子共振趋势")
OUT = ROOT / "evidence" / "ENTRY-AUDIT-02"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def hist_digest_upto(p: Path, max_t: int):
    d = pd.read_parquet(p, columns=["open_time", "high", "low", "close"])
    d = d[d["open_time"] <= max_t].sort_values("open_time")
    h = hashlib.sha256()
    h.update(d["open_time"].to_numpy("int64").tobytes())
    for c in ("high", "low", "close"):
        h.update(pd.to_numeric(d[c], errors="coerce").to_numpy("float64").tobytes())
    return h.hexdigest(), int(len(d))


m = json.load(open(OUT / "00_freeze_manifest.json", encoding="utf-8"))
changed_archive, changed_live, hist_broken, missing = [], [], [], []

for it in m["files"]:
    p = ROOT / it["path"]
    if not p.exists():
        missing.append(it["path"])
        continue
    same = sha(p) == it["sha256"]
    is_live = it["path"].endswith("live.parquet")
    if same:
        continue
    if not is_live:
        changed_archive.append(it["path"])
        continue
    changed_live.append(it["path"])
    if it.get("hist_digest") and it.get("max_open_time") is not None:
        dg, rows = hist_digest_upto(p, int(it["max_open_time"]))
        if dg != it["hist_digest"] or rows != it["rows"]:
            hist_broken.append({"path": it["path"], "rows_then": it["rows"], "rows_now": rows})

fe5 = json.load(open(OUT / "03_fe05_frozen.json", encoding="utf-8"))
fe5_bad = [n for n, meta in fe5.items()
           if not (ROOT / "evidence" / "FACTOR-ENTRY-05" / n).exists()
           or sha(ROOT / "evidence" / "FACTOR-ENTRY-05" / n) != meta["sha256"]]

ok = not (changed_archive or missing or hist_broken or fe5_bad)
res = {
    "checked_files": len(m["files"]),
    "changed_archive_or_upstream": changed_archive,
    "changed_live_parquet_count": len(changed_live),
    "live_parquet_historical_slice_rewritten": hist_broken,
    "missing": missing,
    "fe05_checked": len(fe5),
    "fe05_changed": fe5_bad,
    "note": "live.parquet 由采集器持续追加，整文件哈希变化属预期；其历史切片指纹必须一致。",
    "verdict": "输入零改动（仅 live.parquet 尾部追加）" if ok else "输入发生变化",
}
json.dump(res, open(OUT / "99_verify_result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(json.dumps({k: v for k, v in res.items() if k != "changed_live_parquet_count"}, ensure_ascii=False, indent=2))
print("changed_live_parquet_count =", len(changed_live))
sys.exit(0 if ok else 1)
